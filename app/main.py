"""
main.py — DataMaster HTTP server.

Endpoints:
  GET  /                       → frontend (single HTML file)
  GET  /permit                 → redirect to the permit form (PERMIT_URL in .env)
  GET  /api/config             → current registry
  PUT  /api/config             → replace registry (validated)
  POST /api/query              → run agent, return full trace (blocking)
  GET  /api/query/stream       → run agent, stream trace live via SSE
                                 params: input=…&fields=…
  POST /api/tests/run          → run assertion suite

Run:  python run.py          (host/port come from .env)
"""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

# Load .env before importing agent — it reads ANTHROPIC_API_KEY and
# GOV_API_BASE at import time.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from app.agent import load_config, run_agent, save_config, validate_entry  # noqa: E402

app = FastAPI(
    title="DataMaster",
    version="1.0.0",
    description=(
        "Config-driven government data broker. Send a loosely-identified "
        "subject and a list of wanted fields; the agent decides which agency "
        "APIs to call, in what order, and returns exactly those field names.\n\n"
        "See docs/MIDDLEWARE.md for integration patterns."
    ),
    servers=[{"url": "/", "description": "Wherever this service is running (see .env)"}],
    license_info={"name": "MIT", "identifier": "MIT"},
    openapi_tags=[
        {"name": "Query", "description": "Run the routing agent."},
        {"name": "Config", "description": "Read and replace the agency registry."},
        {"name": "Tests", "description": "Assert routing behaviour, including data minimisation."},
        {"name": "UI", "description": "Static pages and redirects."},
    ],
)

# The permit form is served from its own origin now, so it needs to be allowed
# explicitly. Set CORS_ORIGINS=* in .env to fall back to open access.
_origins = os.getenv("CORS_ORIGINS", "*").strip()
ALLOWED_ORIGINS = ["*"] if _origins == "*" else [o.strip() for o in _origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend" / "index.html"

# The permit form lives in the batam-permit repo. /permit redirects there.
PERMIT_URL = os.getenv("PERMIT_URL", "").strip()


# ---------- models ----------
#
# Field descriptions and examples here are not decoration: they are the only
# source the generated OpenAPI spec has. A bare `input: str` produces a spec
# an integrator cannot use.

class QueryIn(BaseModel):
    input: str = Field(
        ...,
        description=(
            "Loosely-formatted object identifying the subject. Key names are "
            "arbitrary — 'no', 'hp' and 'telp' are all understood as phone."
        ),
        examples=["{name: John Doe, phone: +62838292938}"],
    )
    fields: str = Field(
        ...,
        description=(
            "Exact output field names wanted back. Returned verbatim as the "
            "keys of `answer`, with no renaming or additions."
        ),
        examples=["{npwp, companyName, bloodType}"],
    )


class TraceEvent(BaseModel):
    type: str = Field(..., description="One of: thinking, call, result, final, error.")
    text: str | None = Field(None, description="Present on thinking, final and error events.")
    tool: str | None = Field(None, description="Tool id, on call and result events.")
    args: dict | None = Field(None, description="Arguments sent, on call events.")
    status: int | None = Field(None, description="HTTP status, on result events. 599 means the request never completed.")
    ms: float | None = Field(None, description="Round-trip time in milliseconds, on result events.")


class QueryOut(BaseModel):
    answer: dict | None = Field(
        None,
        description=(
            "Keys exactly as requested in `fields`. Unavailable values are "
            "null, never invented. May carry a `_note` key when something "
            "needs explaining."
        ),
        examples=[{"npwp": "09.254.294.3-217.000", "companyName": "PT Selat Niaga Makmur"}],
    )
    called_tools: list[str] = Field(
        default_factory=list,
        description="Agency APIs actually called, in order. This is the audit trail.",
    )
    trace: list[TraceEvent] = Field(default_factory=list, description="Every routing step.")
    raw: str = Field("", description="The model's unparsed final message.")
    parse_error: str | None = Field(None, description="Set when `raw` could not be parsed as JSON.")


class ApiParam(BaseModel):
    name: str = Field(..., description="Query-string parameter name.")
    type: str = Field("string", description="'string' or 'number'.")
    required: bool = Field(False, description="Whether the agent must supply it.")
    desc: str = Field("", description="Written for the agent, not for a human reader.")


class ApiEntry(BaseModel):
    id: str = Field(..., description="Stable identifier; becomes the tool name.", examples=["dukcapil_getNIK"])
    api: str = Field(
        ...,
        description=(
            "Endpoint URL. May contain ${GOV_API_BASE}, expanded from the "
            "environment when the call is made."
        ),
        examples=["${GOV_API_BASE}/dukcapil/getNIK"],
    )
    method: str = Field("GET", description="HTTP method.")
    desc: str = Field(..., description="The routing logic. The agent reads this to decide when to call.")
    params: list[ApiParam] = Field(default_factory=list)
    returns: str = Field("", description="Human-readable summary of the response shape.")


class ConfigIn(BaseModel):
    apis: list[dict] = Field(..., description="Full registry. Replaces the existing one wholesale.")


class ConfigOut(BaseModel):
    apis: list[ApiEntry] = Field(
        ...,
        description=(
            "The registry as stored. `api` values are returned unexpanded, so "
            "that a round-trip through PUT does not bake in the current host."
        ),
    )


class ConfigSaved(BaseModel):
    ok: bool
    count: int = Field(..., description="Number of entries saved.")


class TestCase(BaseModel):
    name: str
    input: str
    fields: str
    expect: list[str] = Field(default_factory=list, description="Tools that MUST be called.")
    forbid: list[str] = Field(
        default_factory=list,
        description=(
            "Tools that must NOT be called. This is the data-minimisation "
            "check — e.g. a health-only query must not reach the tax API."
        ),
    )
    expect_values: dict = Field(default_factory=dict, description="Exact values expected in `answer`.")


class TestSuiteIn(BaseModel):
    tests: list[TestCase]


class TestResult(BaseModel):
    name: str
    pass_: bool = Field(..., alias="pass")
    called: list[str] = []
    missing: list[str] = Field(default=[], description="Expected tools that were not called.")
    forbidden: list[str] = Field(default=[], description="Forbidden tools that were called anyway.")
    value_problems: list[str] = []
    answer: dict | None = None
    parse_error: str | None = None
    error: str | None = Field(None, description="Set if the run raised before assertions could be checked.")

    model_config = {"populate_by_name": True}


class TestSuiteOut(BaseModel):
    results: list[TestResult]
    passed: int
    total: int


# ---------- frontend ----------

@app.get(
    "/",
    tags=["UI"],
    summary="Console",
    description="Serves the playground and config editor.",
    responses={404: {"description": "frontend/index.html is missing from the checkout."}},
)
def index():
    return FileResponse(FRONTEND)


@app.get(
    "/permit",
    tags=["UI"],
    summary="Redirect to the permit form",
    description="The form lives in the batam-permit repo. 404s if PERMIT_URL is unset.",
    responses={
        307: {"description": "Redirect to PERMIT_URL."},
        404: {"description": "PERMIT_URL is not configured in .env."},
    },
)
def permit_page():
    """
    The permit form moved to the batam-permit repo. Redirect rather than
    serve a file that no longer exists here.
    """
    if not PERMIT_URL:
        raise HTTPException(
            status_code=404,
            detail=(
                "The permit form lives in the batam-permit repo. "
                "Set PERMIT_URL in .env to enable this redirect."
            ),
        )
    return RedirectResponse(PERMIT_URL)


# ---------- config CRUD ----------

@app.get(
    "/api/config",
    tags=["Config"],
    summary="Read the agency registry",
    description="Returns entries exactly as stored, with ${GOV_API_BASE} unexpanded.",
    response_model=ConfigOut,
    responses={422: {"description": "config.json on disk is malformed or fails validation."}},
)
def get_config():
    return {"apis": load_config()}


@app.put(
    "/api/config",
    tags=["Config"],
    summary="Replace the agency registry",
    description="Validates every entry before writing. Rejects the whole payload on any failure. Takes effect on the next query — no restart.",
    response_model=ConfigSaved,
)
def put_config(body: ConfigIn):
    try:
        for e in body.apis:
            validate_entry(e)
        save_config(body.apis)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True, "count": len(body.apis)}


# ---------- query (blocking) ----------

@app.post(
    "/api/query",
    tags=["Query"],
    summary="Run a query (blocking)",
    description="Runs the full routing loop and returns once the agent produces an answer. Use the SSE endpoint if you want live progress.",
    response_model=QueryOut,
)
def post_query(body: QueryIn):
    try:
        return run_agent(body.input, body.fields)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------- query (SSE live trace) ----------

@app.get(
    "/api/query/stream",
    tags=["Query"],
    summary="Run a query (Server-Sent Events)",
    description="Same semantics as POST /api/query, but each routing step arrives as an event. Event types: thinking, call, result, final, error. Terminates with an event named `done`.",
)
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


@app.post(
    "/api/tests/run",
    tags=["Tests"],
    summary="Run an assertion suite",
    description="Each case may require tools (`expect`), forbid tools (`forbid`), and assert exact values. Routing is non-deterministic, so this measures it rather than proving it.",
    response_model=TestSuiteOut,
)
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
