"""Optional server-side adapter for a configured chat-completions endpoint."""

import json
import os
from http.client import HTTPException
from ipaddress import ip_address
from threading import BoundedSemaphore
from typing import Annotated
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .calculation import AnalysisError

MODEL_SLOTS = BoundedSemaphore(4)
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: ShortText
    estimate_ids: list[ShortText] = Field(min_length=1, max_length=8)


class ModelExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: ShortText
    statements: list[Statement] = Field(min_length=1, max_length=4)
    limitations: list[ShortText] = Field(max_length=3)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def model_unavailable():
    return AnalysisError("The calculation is ready. Explanation is temporarily unavailable.",
                         "MODEL_UNAVAILABLE", 503)


def explain(run, target_weights=None):
    if not MODEL_SLOTS.acquire(blocking=False):
        raise model_unavailable()
    try:
        return request_explanation(run, target_weights)
    finally:
        MODEL_SLOTS.release()


def is_local(parsed):
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    try:
        return ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def explanation_timeout():
    try:
        return 45 if is_local(urlsplit(os.getenv("RESEARCHLENS_MODEL_URL", ""))) else 12
    except ValueError:
        return 12


def request_explanation(run, target_weights):
    url = os.getenv("RESEARCHLENS_MODEL_URL", "")
    model = os.getenv("RESEARCHLENS_MODEL", "")
    key = os.getenv("RESEARCHLENS_MODEL_KEY", "")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        local = is_local(parsed)
        remote = parsed.scheme == "https" and bool(key)
        if not (local or remote) or not host or parsed.username is not None or not model:
            raise ValueError("Missing model configuration.")
        # Field labels and claim are supplied by users. Send only anonymized numeric results.
        groups = {}
        strata = {}
        evidence = []
        ids = {}
        for estimate in run["estimates"]:
            stratum = estimate["stratum"]
            if stratum is not None and stratum not in strata and len(strata) >= 2:
                continue
            alias = f"e{len(evidence) + 1}"
            ids[alias] = estimate["id"]
            item = {key: estimate[key] for key in ("estimand", "numerator", "denominator", "value")}
            item["id"] = alias
            item["group"] = groups.setdefault(estimate["group"], f"G{len(groups) + 1}")
            item["stratum"] = strata.setdefault(stratum, f"S{len(strata) + 1}") if stratum is not None else None
            evidence.append(item)
        context = {
            "estimates": evidence,
            "omitted_strata": max(0, len(target_weights or {}) - len(strata)),
            "target_weights": {alias: target_weights[name] for name, alias in strata.items()}
            if target_weights else {},
        }
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 600,
            "response_format": (
                {"type": "json_schema", "json_schema": {
                    "name": "researchlens_explanation", "schema": ModelExplanation.model_json_schema(),
                }} if local else {"type": "json_object"}
            ),
            "messages": [
                {"role": "system", "content": (
                    "Explain the ResearchLens comparisons in plain English using the group and stratum aliases. "
                    "Return JSON with a summary string, an array of statements (each with text and an array of estimate_ids), "
                    "and a limitations array of short strings. "
                    "Start the summary with the observed leader and the standardized leader, or say when tied. "
                    "Compare groups and report rates as percentages. Use up to four short statements. "
                    "Include estimate IDs only in estimate_ids, never in prose. "
                    "The observed values use each group's own sample mix. "
                    "The standardized values use the plan's full shared target mix. If omitted_strata is positive, "
                    "state that the explanation covers only selected strata. "
                    "Use only supplied values. Mention that the analysis is descriptive. "
                    "Do not infer causes, confidence intervals, or generalize beyond the samples and target mix."
                )},
                {"role": "user", "content": json.dumps(context)},
            ],
        }
        request = Request(url, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {key}"} if remote else {}),
        })
        handlers = [NoRedirect(), ProxyHandler({})] if local else [NoRedirect()]
        with build_opener(*handlers).open(request, timeout=40 if local else 10) as response:
            raw = response.read(64_001)
        if len(raw) > 64_000:
            raise ValueError("Model response too large.")
        message = json.loads(raw)["choices"][0]["message"]["content"]
        explanation = ModelExplanation.model_validate_json(message)
        for statement in explanation.statements:
            if not statement.estimate_ids or any(ref not in ids for ref in statement.estimate_ids):
                raise ValueError("Unverified estimate references.")
            statement.estimate_ids = [ids[ref] for ref in statement.estimate_ids]
        return {"run_id": run["id"], **explanation.model_dump()}
    except (OSError, HTTPException, ValueError, KeyError, TypeError, IndexError) as exc:
        raise model_unavailable() from exc
