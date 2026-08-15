"""Deterministic OpenAPI structural diffing; no model is used here."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_contract(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _operations(contract: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    operations: dict[tuple[str, str], dict[str, Any]] = {}
    for path, path_item in contract.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "patch", "delete"}:
                operations[(method.upper(), path)] = operation
    return operations


def _response_fields(operation: dict[str, Any]) -> set[str]:
    response = operation.get("responses", {}).get("200", {})
    schema = response.get("content", {}).get("application/json", {}).get("schema", {})
    return set(schema.get("properties", {}))


def _parameters(operation: dict[str, Any]) -> set[str]:
    return {str(parameter.get("name")) for parameter in operation.get("parameters", [])}


def diff_openapi(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_operations = _operations(old)
    new_operations = _operations(new)
    old_keys, new_keys = set(old_operations), set(new_operations)
    removed_operations = sorted(old_keys - new_keys)
    added_operations = sorted(new_keys - old_keys)

    old_fields = set().union(*(_response_fields(value) for value in old_operations.values()))
    new_fields = set().union(*(_response_fields(value) for value in new_operations.values()))
    old_params = set().union(*(_parameters(value) for value in old_operations.values()))
    new_params = set().union(*(_parameters(value) for value in new_operations.values()))

    return {
        "old_version": old.get("info", {}).get("version"),
        "new_version": new.get("info", {}).get("version"),
        "removed_paths": [
            {"method": method, "path": path} for method, path in removed_operations
        ],
        "added_paths": [
            {"method": method, "path": path} for method, path in added_operations
        ],
        "removed_parameters": sorted(old_params - new_params),
        "added_parameters": sorted(new_params - old_params),
        "removed_response_fields": sorted(old_fields - new_fields),
        "added_response_fields": sorted(new_fields - old_fields),
    }
