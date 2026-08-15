"""Render small human-readable docs directly from an OpenAPI contract."""

from __future__ import annotations

from typing import Any


def render_markdown(contract: dict[str, Any]) -> str:
    info = contract.get("info", {})
    lines = [
        f"# {info.get('title', 'Generated API documentation')}",
        "",
        f"Contract version: `{info.get('version', 'unknown')}`",
        "",
        "> Generated from OpenAPI. OpenAPI describes this API; DataMaster separately manages its consumers and mappings.",
        "",
    ]
    for path, path_item in contract.get("paths", {}).items():
        for method, operation in path_item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            lines.extend(
                [
                    f"## {operation.get('summary', operation.get('operationId', path))}",
                    "",
                    f"`{method.upper()} {path}`",
                    "",
                    "Parameters:",
                    "",
                ]
            )
            parameters = operation.get("parameters", [])
            lines.extend(
                f"- `{parameter.get('name')}` ({parameter.get('in', 'unknown')})"
                for parameter in parameters
            )
            schema = (
                operation.get("responses", {})
                .get("200", {})
                .get("content", {})
                .get("application/json", {})
                .get("schema", {})
            )
            lines.extend(["", "Returns:", ""])
            lines.extend(f"- `{field}`" for field in schema.get("properties", {}))
            lines.append("")
    return "\n".join(lines)
