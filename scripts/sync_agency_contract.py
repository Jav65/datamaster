#!/usr/bin/env python3
"""Synchronize DataMaster's agency registry from batam-business OpenAPI.

Usage:
  python scripts/sync_agency_contract.py --spec ../batam-business/docs/api/openapi.json
  python scripts/sync_agency_contract.py --spec ... --check

The OpenAPI document is authoritative for endpoint paths, HTTP methods,
parameters, required flags and response fields. DataMaster-specific routing
text (desc) is preserved for existing tools and generated from the operation
summary/description for new tools.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def operation_id(method: str, operation: dict, path: str) -> str:
    value = operation.get("operationId")
    if value:
        return value
    # Fallback for specs that don't provide operationId.
    tail = re.sub(r"[^A-Za-z0-9]+", "_", path.strip("/"))
    return f"{method.lower()}_{tail}".strip("_")


def schema_type(schema: dict) -> str:
    typ = schema.get("type", "string")
    if typ in {"integer", "number"}:
        return "number"
    if typ == "boolean":
        return "boolean"
    return "string"


def response_fields(operation: dict) -> list[str]:
    response = operation.get("responses", {}).get("200", {})
    content = response.get("content", {})
    app_json = content.get("application/json", {})
    schema = app_json.get("schema", {})
    if "$ref" in schema:
        return []
    return list((schema.get("properties") or {}).keys())


def endpoint_records(spec: dict) -> dict[str, dict]:
    records = {}
    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            oid = operation_id(method, operation, path)
            params = []
            for p in operation.get("parameters", []):
                if p.get("in") not in {"query", "path"}:
                    continue
                schema = p.get("schema") or {}
                params.append({
                    "name": p["name"],
                    "type": schema_type(schema),
                    "required": bool(p.get("required", False) or p.get("in") == "path"),
                    "desc": p.get("description", ""),
                })
            response = response_fields(operation)
            records[oid] = {
                "path": path,
                "method": method.upper(),
                "params": params,
                "returns": ", ".join(response) if response else "unspecified",
                "description": (operation.get("summary") or operation.get("description") or "").strip(),
            }
    return records


def url_for(path: str) -> str:
    return "${GOV_API_BASE}" + path


def sync(spec: dict, existing: dict) -> tuple[dict, list[str], list[str]]:
    records = endpoint_records(spec)
    old = {e["id"]: e for e in existing.get("apis", [])}
    result = []
    added, changed = [], []

    for oid, r in records.items():
        e = dict(old.get(oid, {}))
        is_new = not e
        e["id"] = oid
        e["api"] = url_for(r["path"])
        e["method"] = r["method"]
        e["params"] = r["params"]
        e["returns"] = r["returns"]
        if not e.get("desc"):
            e["desc"] = r["description"] or f"Call {r['method']} {r['path']}."
        result.append(e)
        (added if is_new else changed).append(oid)

    missing = sorted(set(old) - set(records))
    return {"apis": result}, added, missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, type=Path)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    spec = load_json(args.spec)
    current = load_json(CONFIG)
    synced, added, missing = sync(spec, current)

    if missing:
        print("Removed API operations:")
        for x in missing:
            print(f"  - {x}")

    rendered = json.dumps(synced, indent=2, ensure_ascii=False) + "\n"
    current_rendered = json.dumps(current, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if rendered != current_rendered:
            print("config.json is out of sync with the batam-business OpenAPI contract.")
            print("Run: python scripts/sync_agency_contract.py --spec <path>")
            return 1
        print("config.json matches the batam-business OpenAPI contract.")
        return 0

    CONFIG.write_text(rendered, encoding="utf-8")
    print(f"Updated {CONFIG.relative_to(ROOT)}")
    print(f"Operations: {len(synced['apis'])}; added: {len(added)}; removed: {len(missing)}")
    if added:
        print("Added: " + ", ".join(added))
    if missing:
        print("WARNING: removed operations were dropped from config.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())