"""ResearchLens API v1."""

import asyncio
import hashlib
import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from python_multipart.exceptions import MultipartParseError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from .calculation import (
    MAX_BYTES, AnalysisError, calculate, dataset_warnings, parse_csv, validate_plan,
)
from .explanation import explain, model_unavailable
from .export import export_run

PREFIX = "/api/v1"
FIXTURES = Path(__file__).resolve().parents[1] / "contracts" / "fixtures"
EXAMPLES = json.loads((FIXTURES / "examples.json").read_text(encoding="utf-8"))
EXAMPLE_IDS = {example["id"] for example in EXAMPLES}
COOKIE = "researchlens_session"
SESSION_TTL = 3600
MAX_SESSIONS = 128
MAX_TOTAL_DATA_BYTES = 64_000_000
MAX_REQUEST_BYTES = MAX_BYTES + 65_536
FRONTEND_ORIGIN = os.getenv("RESEARCHLENS_FRONTEND_ORIGIN")
SECURE_COOKIE = os.getenv("RESEARCHLENS_SECURE_COOKIE") == "1"


@dataclass
class Session:
    expires_at: float
    datasets: dict = field(default_factory=dict)
    plans: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)
    keys: dict = field(default_factory=dict)
    explanations: dict = field(default_factory=dict)
    explaining: set = field(default_factory=set)


api = FastAPI(title="ResearchLens", version="1")
api.state.sessions = {}
api.state.requests = deque()


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


ResourceID = Annotated[str, StringConstraints(min_length=1, max_length=128)]
Column = Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ExampleInput(Input):
    example_id: ResourceID


class PlanInput(Input):
    dataset_id: ResourceID
    claim: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    outcome_column: Column
    group_column: Column
    stratum_column: Column
    positive_value: Column | int
    groups: list[Column] = Field(min_length=2, max_length=2)
    target_weights: dict[Column, float] = Field(min_length=1, max_length=100)


class RunInput(Input):
    plan_id: ResourceID


def error_response(exc):
    return JSONResponse({"error": exc.payload(), "request_id": uuid4().hex}, status_code=exc.status)


@api.exception_handler(AnalysisError)
async def analysis_error(request, exc):
    return error_response(exc)


@api.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    status = 400 if any(error["type"] == "json_invalid" for error in exc.errors()) else 422
    return error_response(AnalysisError("Check the request fields.", "INVALID_REQUEST", status))


@api.exception_handler(HTTPException)
async def http_error(request, exc):
    return error_response(AnalysisError("The requested API operation is unavailable.",
                                       "NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST",
                                       exc.status_code))


async def protect_requests(request, call_next):
    if not request.url.path.startswith(PREFIX + "/") or request.method == "OPTIONS":
        return await call_next(request)
    now = time.monotonic()
    recent = request.app.state.requests
    while recent and recent[0] < now - 60:
        recent.popleft()
    if len(recent) >= 600:
        return error_response(AnalysisError("Request limit reached. Retry in one minute.", "RATE_LIMITED", 429))
    recent.append(now)
    if request.method == "POST":
        origin = request.headers.get("origin")
        same_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin and origin not in {same_origin, FRONTEND_ORIGIN}:
            return error_response(AnalysisError("This origin cannot modify the session.", "FORBIDDEN", 403))
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class LimitBody:
    def __init__(self, app):
        self.app = app
        self.slots = asyncio.Semaphore(4)

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope["method"] != "POST"
                or not scope["path"].startswith(PREFIX + "/")):
            await self.app(scope, receive, send)
            return
        if scope["path"] != PREFIX + "/datasets":
            await self.read_body(scope, receive, send)
            return
        if self.slots.locked():
            error = AnalysisError("Too many incoming requests. Try again.", "RATE_LIMITED", 429)
            await error_response(error)(scope, receive, send)
            return
        async with self.slots:
            await self.read_body(scope, receive, send)

    async def read_body(self, scope, receive, send):
        limit = MAX_REQUEST_BYTES if scope["path"] == PREFIX + "/datasets" else 64_000
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                async for chunk in Request(scope, receive).stream():
                    if len(body) + len(chunk) > limit:
                        error = AnalysisError("Request body exceeds the limit.", "LIMIT_EXCEEDED", 413)
                        await error_response(error)(scope, receive, send)
                        return
                    body.extend(chunk)
        except TimeoutError:
            error = AnalysisError("Request body timed out.", "REQUEST_TIMEOUT", 408)
            await error_response(error)(scope, receive, send)
            return
        except ClientDisconnect:
            await Response(status_code=499)(scope, receive, send)
            return

        async def replay():
            nonlocal body
            if body is None:
                return await receive()
            message = {"type": "http.request", "body": bytes(body), "more_body": False}
            body = None
            return message

        await self.app(scope, replay, send)


api.add_middleware(LimitBody)
api.middleware("http")(protect_requests)
if FRONTEND_ORIGIN:
    api.add_middleware(CORSMiddleware, allow_origins=[FRONTEND_ORIGIN], allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["Idempotency-Key", "Content-Type"])


def session_for(request):
    sessions = request.app.state.sessions
    now = time.monotonic()
    for key in [key for key, session in sessions.items() if session.expires_at <= now]:
        del sessions[key]
    token = request.cookies.get(COOKIE)
    session = sessions.get(token)
    if session:
        session.expires_at = now + SESSION_TTL
    return session


def find(records, key):
    if records is None or key not in records:
        raise AnalysisError("Resource not found.", "NOT_FOUND", 404)
    return records[key]


def make_id(prefix):
    return f"{prefix}_{uuid4().hex}"


def capacity(records, limit):
    if len(records) >= limit:
        raise AnalysisError("Session resource limit reached.", "RATE_LIMITED", 429)


@api.get(PREFIX + "/health")
async def health():
    return {"status": "ok", "api_version": "1"}


@api.get(PREFIX + "/examples")
async def examples():
    return EXAMPLES


@api.post(PREFIX + "/datasets")
async def create_dataset(request: Request, response: Response):
    content_type = request.headers.get("content-type", "").split(";")[0].lower()
    synthetic = content_type == "application/json"
    if synthetic:
        try:
            example_id = ExampleInput.model_validate(await request.json()).example_id
        except (ValidationError, ValueError) as exc:
            raise AnalysisError("Provide an example_id.", "INVALID_REQUEST", 400) from exc
        if example_id not in EXAMPLE_IDS:
            raise AnalysisError("Example not found.", "NOT_FOUND", 404)
        raw = (FIXTURES / f"{example_id}.csv").read_bytes()
    elif content_type == "multipart/form-data":
        try:
            async with request.form(max_files=1, max_fields=0) as form:
                upload = form.get("file")
                if not isinstance(upload, UploadFile):
                    raise AnalysisError("Upload a CSV in the file field.", "INVALID_CSV", 400)
                raw = await upload.read(MAX_BYTES + 1)
        except MultipartParseError as exc:
            raise AnalysisError("Malformed multipart upload.", "INVALID_CSV", 400) from exc
    else:
        raise AnalysisError("Use JSON or multipart/form-data.", "INVALID_REQUEST", 400)
    if len(raw) > MAX_BYTES:
        raise AnalysisError("CSV exceeds 10 MB.", "LIMIT_EXCEEDED", 413)
    columns, rows = parse_csv(raw)
    session = session_for(request)
    sessions = request.app.state.sessions
    total_bytes = sum(len(dataset["raw"]) for item in sessions.values() for dataset in item.datasets.values())
    if total_bytes + len(raw) > MAX_TOTAL_DATA_BYTES:
        raise AnalysisError("Dataset storage is temporarily full.", "RATE_LIMITED", 429)
    if session is None:
        capacity(sessions, MAX_SESSIONS)
        token = uuid4().hex
        session = sessions[token] = Session(time.monotonic() + SESSION_TTL)
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=SECURE_COOKIE)
    capacity(session.datasets, 8)
    public = dict(id=make_id("dataset"), synthetic=synthetic, row_count=len(rows),
                  columns=columns, preview=rows[:20])
    session.datasets[public["id"]] = {"public": public, "raw": raw,
                                      "hash": hashlib.sha256(raw).hexdigest()}
    return public


@api.get(PREFIX + "/datasets/{dataset_id}")
async def dataset(dataset_id: str, request: Request):
    session = session_for(request)
    return find(session.datasets if session else None, dataset_id)["public"]


@api.post(PREFIX + "/plans")
async def create_plan(body: PlanInput, request: Request):
    session = session_for(request)
    dataset = find(session.datasets if session else None, body.dataset_id)
    capacity(session.plans, 32)
    plan = {"id": make_id("plan"), **body.model_dump(), "confirmed": True}
    validate_plan(dataset["public"]["columns"], plan)
    session.plans[plan["id"]] = plan
    return plan


@api.get(PREFIX + "/plans/{plan_id}")
async def plan(plan_id: str, request: Request):
    session = session_for(request)
    return find(session.plans if session else None, plan_id)


@api.post(PREFIX + "/runs")
async def create_run(body: RunInput, request: Request):
    session = session_for(request)
    plan = find(session.plans if session else None, body.plan_id)
    key = request.headers.get("idempotency-key")
    if key is not None and not 1 <= len(key) <= 128:
        raise AnalysisError("Idempotency-Key must contain 1–128 characters.", "INVALID_REQUEST", 400)
    if key in session.keys:
        previous = session.runs[session.keys[key]]
        if previous["plan_id"] != body.plan_id:
            raise AnalysisError("Idempotency-Key was used for a different plan.", "IDEMPOTENCY_CONFLICT", 409)
        return previous

    capacity(session.runs, 64)
    dataset = session.datasets[plan["dataset_id"]]
    columns, records = parse_csv(dataset["raw"])
    synthetic = dataset["public"]["synthetic"]
    try:
        estimates, warnings = calculate(columns, records, plan, synthetic)
        status, error = "completed", None
    except AnalysisError as exc:
        estimates, warnings = [], dataset_warnings(synthetic)
        status, error = "failed", exc.payload()
    run = dict(id=make_id("run"), plan_id=plan["id"], status=status,
               dataset_hash=dataset["hash"], estimates=estimates, warnings=warnings,
               explanation_status="not_requested", error=error)
    session.runs[run["id"]] = run
    if key is not None:
        session.keys[key] = run["id"]
    return run


@api.get(PREFIX + "/runs/{run_id}")
async def run(run_id: str, request: Request):
    session = session_for(request)
    return find(session.runs if session else None, run_id)


@api.post(PREFIX + "/runs/{run_id}/explanation")
async def create_explanation(run_id: str, request: Request):
    session = session_for(request)
    run = find(session.runs if session else None, run_id)
    if run["status"] != "completed":
        raise AnalysisError("Complete the calculation before requesting an explanation.",
                            "INVALID_RUN", 422)
    if run_id in session.explanations:
        return session.explanations[run_id]
    if session.explaining:
        raise AnalysisError("An explanation is already being generated.", "RATE_LIMITED", 429)
    session.explaining.add(run_id)
    try:
        result = await asyncio.wait_for(asyncio.to_thread(explain, run), timeout=12)
    except TimeoutError as exc:
        run["explanation_status"] = "failed"
        raise model_unavailable() from exc
    except AnalysisError:
        run["explanation_status"] = "failed"
        raise
    finally:
        session.explaining.discard(run_id)
    session.explanations[run_id] = result
    run["explanation_status"] = "ready"
    return result


@api.get(PREFIX + "/runs/{run_id}/export")
async def download(run_id: str, request: Request, include_data: bool = False):
    session = session_for(request)
    run = find(session.runs if session else None, run_id)
    plan = session.plans[run["plan_id"]]
    dataset = session.datasets[plan["dataset_id"]]
    archive = await run_in_threadpool(export_run, dataset, plan, dict(run), include_data)
    return Response(archive, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"',
                             "Cache-Control": "no-store"})


# The built client and API can share one origin without a development proxy.
frontend = FIXTURES.parents[1] / "frontend" / "dist"
if frontend.is_dir():
    from starlette.staticfiles import StaticFiles

    api.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
