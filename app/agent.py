"""
agent.py — the DataMaster brain.

Pattern: single tool-calling agent.
  config.json entry  →  Anthropic tool definition
  LLM picks tools    →  we execute real HTTP calls
  results fed back   →  LLM composes a JSON answer with EXACTLY
                        the output fields the caller requested.

Input is flexible-named JSON-ish, e.g.  {name: Budi, no: +62810320123}
Output spec is a field list, e.g.       {testResult, bloodType, lastUpdated}
The LLM maps loose names ("no" → phone, "bloodType" → blood_type) itself.

Every step is reported through an on_trace callback so the frontend
can render a live trace (SSE).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from anthropic import Anthropic
from dotenv import load_dotenv

# .env lives in the project root (one level above app/), found regardless of CWD
# override=False: a real environment variable (Docker, CI, systemd) must win
# over the local .env file, which is a developer convenience only.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def require_api_key() -> str:
    """
    Validate the key at the point of use rather than at import.

    Importing this module must stay side-effect-free so that tooling which
    only needs the app object — OpenAPI generation in CI, for instance — can
    run without a secret. run.py still checks at startup, so a misconfigured
    server fails immediately rather than on the first query.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key.startswith("sk-ant-"):
        raise RuntimeError(
            f"ANTHROPIC_API_KEY missing or malformed (got {len(key)} chars). "
            "Put it in datamaster/.env as: ANTHROPIC_API_KEY=sk-ant-..."
        )
    return key

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
MODEL = "claude-sonnet-4-6"
MAX_ROUNDS = 6

# The agency endpoints live in a separate repo and may run anywhere.
# config.json stores "${GOV_API_BASE}/dukcapil/getNIK" rather than a hardcoded
# host; the placeholder is expanded at call time — not at load time — so that
# a config saved from the UI round-trips without baking in whatever host
# happened to be set when someone clicked Save.
GOV_API_BASE = os.environ.get("GOV_API_BASE", "http://localhost:9001").rstrip("/")


def resolve_url(url: str) -> str:
    """Expand ${GOV_API_BASE} in a configured endpoint URL."""
    return url.replace("${GOV_API_BASE}", GOV_API_BASE)

SYSTEM = """You are DataMaster, a data broker middleware for Indonesian government APIs.

You receive:
1. INPUT — a loosely-formatted object identifying a subject. Key names are
   arbitrary (e.g. "no", "hp", "telp" may mean phone; "nama" means name).
   Infer the meaning of each key.
2. REQUESTED OUTPUT FIELDS — the exact field names the caller wants back.

Decide which registered APIs to call (often chained: identity resolution first),
gather the data, then respond with ONLY a valid JSON object:
- keys: EXACTLY the requested field names, verbatim, no additions, no renames
- values: the data found; use null if unavailable (never invent data);
  you may add a "_note" key ONLY if something needs explaining
- no markdown, no code fences, no prose before or after the JSON."""


# ---------- config ----------

def load_config() -> list[dict]:
    with open(CONFIG_PATH) as f:
        data = json.load(f)
    apis = data["apis"] if isinstance(data, dict) else data
    for e in apis:
        validate_entry(e)
    return apis


def save_config(apis: list[dict]) -> None:
    for e in apis:
        validate_entry(e)
    CONFIG_PATH.write_text(json.dumps({"apis": apis}, indent=2))


def validate_entry(e: dict) -> None:
    for key in ("id", "api", "desc"):
        if not e.get(key, "").strip():
            raise ValueError(f"config entry missing required field '{key}': {e}")
    for p in e.get("params", []):
        if not p.get("name"):
            raise ValueError(f"param without name in '{e['id']}'")


# ---------- config → tools ----------

def tool_name(entry: dict) -> str:
    return "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in entry["id"])[:64]


def build_tools(apis: list[dict]) -> list[dict]:
    tools = []
    for e in apis:
        props = {
            p["name"]: {
                "type": "number" if p.get("type") == "number" else "string",
                "description": p.get("desc", ""),
            }
            for p in e.get("params", [])
        }
        tools.append(
            {
                "name": tool_name(e),
                "description": f"{e['desc']}\nEndpoint: {resolve_url(e['api'])}\nReturns: {e.get('returns', 'unspecified')}",
                "input_schema": {
                    "type": "object",
                    "properties": props,
                    "required": [p["name"] for p in e.get("params", []) if p.get("required")],
                },
            }
        )
    return tools


# ---------- HTTP executor ----------

def execute_api(entry: dict, args: dict) -> tuple[int, Any, float]:
    """Call the real endpoint from config. Returns (status, body, ms)."""
    t0 = time.perf_counter()
    try:
        method = entry.get("method", "GET").upper()
        url = resolve_url(entry["api"])
        with httpx.Client(timeout=10) as client:
            if method == "GET":
                r = client.get(url, params=args)
            else:
                r = client.request(method, url, json=args)
        ms = (time.perf_counter() - t0) * 1000
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:2000]}
        return r.status_code, body, ms
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return 599, {"error": f"request failed: {exc}"}, ms


# ---------- JSON extraction ----------

def parse_json_answer(text: str) -> tuple[dict | None, str | None]:
    """Best-effort: strip fences, find outermost {...}, parse."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "no JSON object found in model output"
    try:
        return json.loads(cleaned[start : end + 1]), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


# ---------- the agent loop ----------

def run_agent(
    input_data: str,
    output_fields: str,
    apis: list[dict] | None = None,
    on_trace: Callable[[dict], None] = lambda ev: None,
) -> dict:
    """
    input_data:    loose JSON-ish subject, e.g. "{name: Budi, no: +62810320123}"
    output_fields: requested fields, e.g.   "{testResult, bloodType, lastUpdated}"

    Returns {"answer": dict|None, "raw": str, "parse_error": str|None,
             "called_tools": [str], "trace": [events]}.
    """
    apis = apis if apis is not None else load_config()
    tools = build_tools(apis)
    by_name = {tool_name(e): e for e in apis}
    client = Anthropic(api_key=require_api_key())

    user_msg = f"INPUT:\n{input_data}\n\nREQUESTED OUTPUT FIELDS:\n{output_fields}"
    messages: list[dict] = [{"role": "user", "content": user_msg}]
    trace: list[dict] = []
    called: list[str] = []

    def emit(ev: dict) -> None:
        trace.append(ev)
        on_trace(ev)

    for round_no in range(MAX_ROUNDS):
        emit({"type": "thinking", "round": round_no})
        resp = client.messages.create(
            model=MODEL, max_tokens=1000, system=SYSTEM, messages=messages, tools=tools
        )
        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        text = "\n".join(b.text for b in resp.content if b.type == "text")

        if not tool_uses:
            parsed, perr = parse_json_answer(text)
            emit({"type": "final", "json": parsed, "raw": text, "parse_error": perr})
            return {"answer": parsed, "raw": text, "parse_error": perr,
                    "called_tools": called, "trace": trace}

        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for tu in tool_uses:
            entry = by_name.get(tu.name)
            emit({"type": "call", "tool": tu.name, "api": entry["api"] if entry else "?", "args": tu.input})
            if entry:
                status, body, ms = execute_api(entry, tu.input)
            else:
                status, body, ms = 500, {"error": "unknown tool"}, 0.0
            called.append(tu.name)
            emit({"type": "result", "tool": tu.name, "status": status, "ms": round(ms), "body": body})
            results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": json.dumps(body)}
            )
        messages.append({"role": "user", "content": results})

    emit({"type": "final", "json": None, "raw": "", "parse_error": "stopped: too many rounds"})
    return {"answer": None, "raw": "", "parse_error": "stopped: too many rounds",
            "called_tools": called, "trace": trace}
