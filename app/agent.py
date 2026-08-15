"""OpenAI-powered tool-routing agent used by DataMaster's Playground.

Each configured government API becomes a strict Responses API function tool.
The model selects tools, DataMaster executes the HTTP calls, and their results
are returned to the model until it produces the exact requested JSON fields.
Every step is emitted to the browser as a trace event.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6"
MAX_ROUNDS = 6

SYSTEM = """You are DataMaster, a data broker middleware for Indonesian government APIs.

You receive a loosely formatted subject and exact requested output field names.
Infer flexible input names such as no/hp/telp as phone and nama as name.

Rules:
- Use the provided government API tools whenever their data is required.
- Chain calls when necessary; for example identity first, then NIK-based tools.
- Never invent government data.
- Return every requested output field using its exact spelling.
- Use null when a requested value is unavailable.
- Do not add fields that were not requested.
- The final response must be the schema-constrained JSON object only."""


class LLMUnavailableError(RuntimeError):
    """Raised when the OpenAI-backed Playground cannot run."""


def _api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def query_model() -> str:
    return (
        os.environ.get("OPENAI_QUERY_MODEL", "").strip()
        or os.environ.get("OPENAI_REPOSITORY_MODEL", "").strip()
        or DEFAULT_MODEL
    )


def llm_available() -> bool:
    """Return whether the OpenAI-powered query Playground can be used."""

    return bool(_api_key())


# ---------- config ----------

def load_config() -> list[dict]:
    with CONFIG_PATH.open() as config_file:
        data = json.load(config_file)
    apis = data["apis"] if isinstance(data, dict) else data
    for entry in apis:
        validate_entry(entry)
    return apis


def save_config(apis: list[dict]) -> None:
    for entry in apis:
        validate_entry(entry)
    CONFIG_PATH.write_text(json.dumps({"apis": apis}, indent=2) + "\n")


def validate_entry(entry: dict) -> None:
    for key in ("id", "api", "desc"):
        if not str(entry.get(key, "")).strip():
            raise ValueError(f"config entry missing required field '{key}': {entry}")
    for parameter in entry.get("params", []):
        if not parameter.get("name"):
            raise ValueError(f"param without name in '{entry['id']}'")


# ---------- config to OpenAI function tools ----------

def tool_name(entry: dict) -> str:
    return "".join(
        character if character.isalnum() or character in "_-" else "_"
        for character in entry["id"]
    )[:64]


def build_tools(apis: list[dict]) -> list[dict]:
    """Create strict function tools following the Responses API schema."""

    tools = []
    for entry in apis:
        properties: dict[str, dict[str, Any]] = {}
        parameters = entry.get("params", [])
        for parameter in parameters:
            base_type = "number" if parameter.get("type") == "number" else "string"
            parameter_type: str | list[str] = base_type
            if not parameter.get("required"):
                parameter_type = [base_type, "null"]
            properties[parameter["name"]] = {
                "type": parameter_type,
                "description": parameter.get("desc", ""),
            }
        tools.append(
            {
                "type": "function",
                "name": tool_name(entry),
                "description": (
                    f"{entry['desc']}\nEndpoint: {entry['api']}\n"
                    f"Returns: {entry.get('returns', 'unspecified')}"
                ),
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": [parameter["name"] for parameter in parameters],
                    "additionalProperties": False,
                },
            }
        )
    return tools


# ---------- HTTP executor ----------

def execute_api(entry: dict, arguments: dict) -> tuple[int, Any, float]:
    """Call one configured government endpoint and return status, body, and ms."""

    started = time.perf_counter()
    endpoint = os.path.expandvars(entry["api"])
    try:
        method = entry.get("method", "GET").upper()
        with httpx.Client(timeout=10.0) as client:
            if method == "GET":
                response = client.get(endpoint, params=arguments)
            else:
                response = client.request(method, endpoint, json=arguments)
        elapsed_ms = (time.perf_counter() - started) * 1_000
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:2_000]}
        return response.status_code, body, elapsed_ms
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1_000
        return 599, {"error": f"request failed: {exc}"}, elapsed_ms


# ---------- output schemas and parsing ----------

def requested_field_names(output_fields: str) -> list[str]:
    """Parse the Playground's compact ``{fieldOne, fieldTwo}`` syntax."""

    compact = output_fields.strip()
    if compact[:1] in "{[" and compact[-1:] in "}]":
        compact = compact[1:-1]
    fields = [part.strip().strip("\"'") for part in compact.split(",") if part.strip()]
    if not fields:
        raise ValueError("Enter at least one requested output field")
    if len(fields) > 30:
        raise ValueError("At most 30 output fields can be requested")
    if len(fields) != len(set(fields)):
        raise ValueError("Requested output fields must be unique")
    for field in fields:
        if len(field) > 80 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", field):
            raise ValueError(f"Invalid requested output field: {field}")
    return fields


def answer_format(fields: list[str]) -> dict[str, Any]:
    scalar_or_list = {
        "anyOf": [
            {"type": "string"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "null"},
            {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "boolean"},
                    ]
                },
            },
        ]
    }
    return {
        "type": "json_schema",
        "name": "datamaster_query_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {field: scalar_or_list for field in fields},
            "required": fields,
            "additionalProperties": False,
        },
    }


def parse_json_answer(text: str) -> tuple[dict | None, str | None]:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "no JSON object found in model output"
    try:
        return json.loads(cleaned[start : end + 1]), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"


def _output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    texts = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "refusal":
                raise LLMUnavailableError("OpenAI declined to process this query")
            if content.get("type") == "output_text":
                texts.append(str(content.get("text", "")))
    return "\n".join(texts)


def _create_response(client: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post(
            OPENAI_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {_api_key()}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    except httpx.RequestError as exc:
        raise LLMUnavailableError("DataMaster could not reach the OpenAI API") from exc
    if response.is_error:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = ""
        safe_message = str(message).strip()[:300] or f"HTTP {response.status_code}"
        raise LLMUnavailableError(f"OpenAI query routing failed: {safe_message}")
    try:
        return response.json()
    except ValueError as exc:
        raise LLMUnavailableError("OpenAI returned an invalid API response") from exc


# ---------- agent loop ----------

def run_agent(
    input_data: str,
    output_fields: str,
    apis: list[dict] | None = None,
    on_trace: Callable[[dict], None] = lambda event: None,
    *,
    openai_client: httpx.Client | None = None,
) -> dict:
    """Route a flexible query through registered APIs with OpenAI tools."""

    if not llm_available():
        raise LLMUnavailableError(
            "The Playground requires OPENAI_API_KEY on the DataMaster server"
        )
    fields = requested_field_names(output_fields)
    apis = apis if apis is not None else load_config()
    tools = build_tools(apis)
    by_name = {tool_name(entry): entry for entry in apis}
    input_items: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"INPUT:\n{input_data}\n\n"
                f"REQUESTED OUTPUT FIELDS:\n{json.dumps(fields)}"
            ),
        }
    ]
    trace: list[dict] = []
    called: list[str] = []

    def emit(event: dict) -> None:
        trace.append(event)
        on_trace(event)

    owns_client = openai_client is None
    if openai_client is None:
        openai_client = httpx.Client(timeout=httpx.Timeout(90.0, connect=10.0))
    try:
        for round_number in range(MAX_ROUNDS):
            emit({"type": "thinking", "round": round_number})
            response = _create_response(
                openai_client,
                {
                    "model": query_model(),
                    "store": False,
                    "instructions": SYSTEM,
                    "input": input_items,
                    "tools": tools,
                    "parallel_tool_calls": True,
                    "text": {"format": answer_format(fields)},
                    "max_output_tokens": 2_000,
                },
            )
            function_calls = [
                item for item in response.get("output", [])
                if item.get("type") == "function_call"
            ]
            if not function_calls:
                text = _output_text(response)
                parsed, parse_error = parse_json_answer(text)
                emit(
                    {
                        "type": "final",
                        "json": parsed,
                        "raw": text,
                        "parse_error": parse_error,
                    }
                )
                return {
                    "answer": parsed,
                    "raw": text,
                    "parse_error": parse_error,
                    "called_tools": called,
                    "trace": trace,
                }

            input_items.extend(response.get("output", []))
            for function_call in function_calls:
                name = str(function_call.get("name", ""))
                entry = by_name.get(name)
                try:
                    arguments = json.loads(function_call.get("arguments", "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                emit(
                    {
                        "type": "call",
                        "tool": name,
                        "api": entry["api"] if entry else "?",
                        "args": arguments,
                    }
                )
                if entry:
                    status, body, elapsed_ms = execute_api(entry, arguments)
                else:
                    status, body, elapsed_ms = 500, {"error": "unknown tool"}, 0.0
                called.append(name)
                emit(
                    {
                        "type": "result",
                        "tool": name,
                        "status": status,
                        "ms": round(elapsed_ms),
                        "body": body,
                    }
                )
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": function_call["call_id"],
                        "output": json.dumps({"status": status, "body": body}),
                    }
                )
    finally:
        if owns_client:
            openai_client.close()

    error = "stopped: too many tool-calling rounds"
    emit({"type": "final", "json": None, "raw": "", "parse_error": error})
    return {
        "answer": None,
        "raw": "",
        "parse_error": error,
        "called_tools": called,
        "trace": trace,
    }
