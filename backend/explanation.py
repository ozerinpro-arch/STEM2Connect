"""Optional server-side adapter for a configured chat-completions endpoint."""

import json
import os
from http.client import HTTPException
from threading import BoundedSemaphore
from typing import Annotated
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import BaseModel, ConfigDict, StringConstraints

from .calculation import AnalysisError

MODEL_SLOTS = BoundedSemaphore(4)
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Statement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: ShortText
    estimate_ids: list[ShortText]


class ModelExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: ShortText
    statements: list[Statement]
    limitations: list[ShortText]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def model_unavailable():
    return AnalysisError("The calculation is ready. Explanation is temporarily unavailable.",
                         "MODEL_UNAVAILABLE", 503)


def explain(run):
    if not MODEL_SLOTS.acquire(blocking=False):
        raise model_unavailable()
    try:
        return request_explanation(run)
    finally:
        MODEL_SLOTS.release()


def request_explanation(run):
    url = os.getenv("RESEARCHLENS_MODEL_URL", "")
    model = os.getenv("RESEARCHLENS_MODEL", "")
    key = os.getenv("RESEARCHLENS_MODEL_KEY", "")
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or not model or not key:
            raise ValueError("Missing model configuration.")
        # Field labels and claim are supplied by users. Send only anonymized numeric results.
        groups = {}
        strata = {}
        evidence = []
        ids = {}
        for index, estimate in enumerate(run["estimates"], 1):
            alias = f"e{index}"
            ids[alias] = estimate["id"]
            item = {key: estimate[key] for key in ("estimand", "numerator", "denominator", "value")}
            item["id"] = alias
            item["group"] = groups.setdefault(estimate["group"], f"G{len(groups) + 1}")
            stratum = estimate["stratum"]
            item["stratum"] = strata.setdefault(stratum, f"S{len(strata) + 1}") if stratum is not None else None
            evidence.append(item)
        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": (
                    "Explain the supplied ResearchLens estimates in English using group aliases. "
                    "Return JSON with summary, statements (each with text and estimate_ids), and limitations. "
                    "Cite the supplied IDs. Compare groups only within the same estimand and stratum. "
                    "Describe differences between estimands as different population questions. "
                    "Do not infer causality, confidence intervals, or hypothesis probabilities."
                )},
                {"role": "user", "content": json.dumps(evidence)},
            ],
        }
        request = Request(url, data=json.dumps(payload).encode(), headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        })
        with build_opener(NoRedirect()).open(request, timeout=10) as response:
            raw = response.read(64_001)
        if len(raw) > 64_000:
            raise ValueError("Model response too large.")
        message = json.loads(raw)["choices"][0]["message"]["content"]
        explanation = ModelExplanation.model_validate_json(message)
        if not 1 <= len(explanation.statements) <= 20 or len(explanation.limitations) > 20:
            raise ValueError("Invalid statement count.")
        for statement in explanation.statements:
            if not statement.estimate_ids or any(ref not in ids for ref in statement.estimate_ids):
                raise ValueError("Unverified estimate references.")
            statement.estimate_ids = [ids[ref] for ref in statement.estimate_ids]
        return {"run_id": run["id"], **explanation.model_dump()}
    except (OSError, HTTPException, ValueError, KeyError, TypeError, IndexError) as exc:
        raise model_unavailable() from exc
