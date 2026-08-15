"""DataMaster HTTP boundary for the stable resolver and judge-facing console.

The deterministic permit, policy, registry, onboarding, contract-change, reset,
and test endpoints work without an LLM. The older flexible query playground is
kept as an optional, gracefully degrading feature.

Run with ``python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000``.
"""

from __future__ import annotations

import json
import os
import queue
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from app.agent import (
    LLMUnavailableError,
    llm_available,
    load_config,
    run_agent,
    save_config,
    validate_entry,
)
from app.change_manager import (
    ChangeError,
    approve_change,
    detect_oss_v2_change,
    edit_mapping,
    list_changes,
    reject_change,
)
from app.demo_checks import run_demo_checks
from app.demo_resident_records import register_demo_resident
from app.github_webhook import WebhookError, parse_oss_contract_event, verify_signature
from app.onboarding import (
    OnboardingError,
    approve_onboarding,
    list_onboarding,
    query_legacy_lms,
    scan_legacy_lms,
)
from app.policy import PolicyDenied
from app.repository_scanner import (
    DEFAULT_OPENAI_MODEL,
    RepositoryScanError,
    connect_repository,
)
from app.repository_monitor import REPOSITORY_MONITOR
from app.resolver import ResolutionError, resolve
from app.state_store import STORE


@asynccontextmanager
async def lifespan(_app: FastAPI):
    REPOSITORY_MONITOR.start()
    yield
    REPOSITORY_MONITOR.close()


app = FastAPI(title="DataMaster", lifespan=lifespan)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"


# ---------- models ----------

class QueryIn(BaseModel):
    input: str          # loose JSON-ish subject: "{name: Budi, no: +62810...}"
    fields: str         # requested output fields: "{testResult, bloodType}"


class ConfigIn(BaseModel):
    apis: list[dict]


class TestCase(BaseModel):
    name: str
    input: str
    fields: str
    expect: list[str] = Field(default_factory=list)  # tools that MUST be called
    forbid: list[str] = Field(default_factory=list)  # tools that must NOT be called
    expect_values: dict = Field(default_factory=dict)


class TestSuiteIn(BaseModel):
    tests: list[TestCase]


class SubjectIn(BaseModel):
    name: str
    phone: str


class ResolveIn(BaseModel):
    subject: SubjectIn
    fields: list[str]
    purpose: str


class MappingEditIn(BaseModel):
    concept: str
    new_field: str


class LegacyQueryIn(BaseModel):
    nib: str


class ResetIn(BaseModel):
    actor: str = Field(default="demo_operator", max_length=100)


class RepositoryConnectIn(BaseModel):
    repository_url: str = Field(min_length=1, max_length=500)


class DisdukcapilRecordIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    phone: str = Field(pattern=r"^\+?[0-9]{9,15}$")
    nik: str = Field(pattern=r"^[0-9]{16}$")
    dob: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    registered_address: str = Field(min_length=5, max_length=300)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=200)
    npwp: str = Field(min_length=5, max_length=40)
    company_name: str = Field(min_length=2, max_length=200)
    nib: str = Field(pattern=r"^[0-9]{13}$")
    deed_number: str = Field(min_length=3, max_length=120)
    sk_kemenkumham: str = Field(min_length=3, max_length=160)
    notary: str = Field(min_length=3, max_length=160)
    risk_level: str = Field(min_length=2, max_length=80)
    kbli: list[str] = Field(min_length=1, max_length=8)


# ---------- frontend ----------

@app.get("/")
def index():
    return FileResponse(FRONTEND)


@app.get("/permit")
def permit_page():
    return FileResponse(FRONTEND.parent / "permit.html")


@app.get("/disdukcapil")
def disdukcapil_page():
    return FileResponse(FRONTEND.parent / "disdukcapil.html")


# ---------- config CRUD ----------

@app.get("/api/config")
def get_config():
    return {"apis": load_config()}


@app.put("/api/config")
def put_config(body: ConfigIn):
    try:
        for e in body.apis:
            validate_entry(e)
        save_config(body.apis)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "count": len(body.apis)}


# ---------- query (blocking) ----------

@app.post("/api/query")
def post_query(body: QueryIn):
    try:
        return run_agent(body.input, body.fields)
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------- stable semantic resolver ----------

@app.post("/api/resolve")
def post_resolve(body: ResolveIn):
    """Stable downstream contract; callers never send upstream field names."""
    try:
        return resolve(
            body.subject.model_dump(),
            body.fields,
            body.purpose,
        )
    except PolicyDenied as exc:
        raise HTTPException(
            status_code=403,
            detail={
                "message": str(exc),
                "purpose": exc.purpose,
                "denied_fields": exc.denied_fields,
                "executed_services": [],
            },
        ) from exc
    except ResolutionError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"message": str(exc), "service": exc.service},
        ) from exc


@app.post("/api/disdukcapil/records", status_code=201)
def create_disdukcapil_record(body: DisdukcapilRecordIn):
    """Save one local demo registration used by the mock authoritative APIs."""

    record = register_demo_resident(body.model_dump())
    return {
        "ok": True,
        "message": "Resident and legal-entity data registered for the demo.",
        "record": {
            "name": record["name"],
            "phone": record["phone"],
            "nik": record["nik"],
            "nib": record["nib"],
            "registered_at": record["registered_at"],
        },
    }


# ---------- control-layer read models ----------

@app.get("/api/overview")
def overview():
    registry = STORE.registry()
    dependencies = STORE.dependencies()["consumers"]
    proposals = STORE.proposals()
    pending = sum(
        item["status"] == "pending"
        for collection in proposals.values()
        for item in collection
    )
    return {
        "tagline": "Government Integration Control Layer",
        "principle": (
            "OpenAPI describes an API. DataMaster manages the dependency and "
            "integration lifecycle across many APIs."
        ),
        "counts": {
            "services": sum(
                service.get("documentation_status") != "undocumented"
                for service in registry["services"].values()
            ),
            "concepts": len(registry["concepts"]),
            "consumers": len(dependencies),
            "pending_reviews": pending,
        },
        "pending_changes": sum(
            item["status"] == "pending" for item in proposals["changes"]
        ),
        "registry_revision": registry["revision"],
        "llm_available": llm_available(),
        "demo": STORE.demo(),
    }


@app.get("/api/services")
def services():
    registry = STORE.registry()
    return {
        "services": registry["services"],
        "concepts": registry["concepts"],
        "repository_connector": {
            "openai_configured": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
            "model": os.environ.get("OPENAI_REPOSITORY_MODEL", DEFAULT_OPENAI_MODEL),
        },
        "repository_monitor": REPOSITORY_MONITOR.status(),
    }


@app.post("/api/repositories/connect")
def connect_public_repository(body: RepositoryConnectIn):
    """Document the exposed APIs in a bounded public GitHub repository clone."""
    try:
        return connect_repository(body.repository_url)
    except RepositoryScanError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/repositories/bp-batam/connect", include_in_schema=False)
def connect_legacy_repository_route(body: RepositoryConnectIn):
    """Keep stale browser tabs functional while they refresh to the generic UI."""
    return connect_public_repository(body)


@app.get("/api/repositories/monitor")
def repository_monitor_status():
    return REPOSITORY_MONITOR.status()


@app.post("/api/repositories/monitor")
def inspect_repositories_now():
    """Queue a full documentation inspection independently of the current SHA."""
    return REPOSITORY_MONITOR.request_inspection()


# ---------- human-reviewed legacy onboarding ----------

@app.get("/api/onboarding")
def get_onboarding():
    return {"proposals": list_onboarding()}


@app.post("/api/onboarding/scan")
def post_onboarding_scan():
    try:
        return scan_legacy_lms()
    except OnboardingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/onboarding/{proposal_id}/approve")
def post_onboarding_approve(proposal_id: str):
    try:
        return approve_onboarding(proposal_id)
    except OnboardingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/onboarding/query")
def post_onboarding_query(body: LegacyQueryIn):
    try:
        return query_legacy_lms(body.nib)
    except OnboardingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ---------- dependency-aware contract change review ----------

@app.get("/api/changes")
def get_changes():
    return {"changes": list_changes(), "demo": STORE.demo()}


@app.post("/api/changes/simulate-oss-v2")
def simulate_oss_v2():
    """Reliable local stand-in for the production GitHub App delivery path."""
    try:
        return detect_oss_v2_change("local_github_app_simulator")
    except ChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.patch("/api/changes/{proposal_id}/mapping")
def patch_change_mapping(proposal_id: str, body: MappingEditIn):
    try:
        return edit_mapping(proposal_id, body.concept, body.new_field)
    except ChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/changes/{proposal_id}/approve")
def post_change_approve(proposal_id: str):
    try:
        return approve_change(proposal_id)
    except ChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/changes/{proposal_id}/reject")
def post_change_reject(proposal_id: str):
    try:
        return reject_change(proposal_id)
    except ChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/github/webhook")
async def github_webhook(request: Request):
    """Model a signed GitHub App event; never apply a proposal automatically."""
    body = await request.body()
    try:
        verify_signature(body, request.headers.get("x-hub-signature-256"))
        event = parse_oss_contract_event(body, request.headers.get("x-github-event"))
    except WebhookError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        proposal = detect_oss_v2_change("github_webhook")
    except ChangeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"accepted": True, "event": event, "proposal": proposal}


# ---------- deterministic demo reset ----------

@app.post("/api/demo/reset")
def reset_demo(body: ResetIn):
    STORE.reset(body.actor)
    return {"ok": True, "demo": STORE.demo(), "registry_revision": STORE.registry()["revision"]}


@app.post("/api/demo/tests")
def demo_tests():
    """Run isolated acceptance checks without changing the live demo state."""
    return run_demo_checks()


# ---------- query (SSE live trace) ----------

@app.get("/api/query/stream")
def stream_query(input: str, fields: str):
    ch: queue.Queue = queue.Queue()

    def worker():
        try:
            run_agent(input, fields, on_trace=ch.put)
        except Exception as exc:
            ch.put({"type": "error", "text": str(exc)})
        finally:
            ch.put(None)  # sentinel

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            ev = ch.get()
            if ev is None:
                yield "event: done\ndata: {}\n\n"
                break
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- test runner ----------

def _check_values(answer: dict | None, expected: dict) -> list[str]:
    """Return list of mismatch descriptions."""
    problems = []
    if not expected:
        return problems
    if answer is None:
        return [f"expected values {expected} but answer was not valid JSON"]
    for k, v in expected.items():
        if k not in answer:
            problems.append(f"missing field '{k}'")
        elif answer[k] != v:
            problems.append(f"'{k}': expected {v!r}, got {answer[k]!r}")
    return problems


@app.post("/api/tests/run")
def run_tests(body: TestSuiteIn):
    results = []
    for t in body.tests:
        try:
            out = run_agent(t.input, t.fields)
            called = out["called_tools"]
            missing = [x for x in t.expect if x not in called]
            forbidden = [x for x in t.forbid if x in called]
            value_problems = _check_values(out["answer"], t.expect_values)
            ok = not missing and not forbidden and not value_problems
            results.append(
                {
                    "name": t.name,
                    "pass": ok,
                    "called": called,
                    "missing": missing,
                    "forbidden": forbidden,
                    "value_problems": value_problems,
                    "answer": out["answer"],
                    "parse_error": out["parse_error"],
                }
            )
        except Exception as exc:
            results.append({"name": t.name, "pass": False, "error": str(exc)})
    return {"results": results, "passed": sum(r["pass"] for r in results), "total": len(results)}
