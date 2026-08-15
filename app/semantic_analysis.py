"""Optional AI semantic proposals with a deterministic offline fallback."""

from __future__ import annotations

import json
import os
import re
from typing import Any

FALLBACK_MAPPINGS = {
    "nib": (
        "business_identification_number",
        0.97,
        "Both names denote Indonesia's Business Identification Number.",
    ),
    "company_name": (
        "legal_name",
        0.94,
        "Both fields identify the registered legal name of the business.",
    ),
    "kbli": (
        "activity_codes",
        0.88,
        "Both fields contain registered business activity classifications.",
    ),
    "risk_level": (
        "risk_classification",
        0.91,
        "Both fields represent the OSS risk classification.",
    ),
}


def _fallback(removed: list[str], added: list[str], reason: str) -> dict[str, Any]:
    mappings = []
    for old_field in removed:
        candidate = FALLBACK_MAPPINGS.get(old_field)
        if candidate and candidate[0] in added:
            mappings.append(
                {
                    "old_field": old_field,
                    "new_field": candidate[0],
                    "semantic_match": True,
                    "confidence": candidate[1],
                    "reason": candidate[2],
                }
            )
    return {"mode": "deterministic_fallback", "fallback_reason": reason, "mappings": mappings}


def propose_semantic_mappings(removed: list[str], added: list[str]) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key.startswith("sk-ant-"):
        return _fallback(removed, added, "ANTHROPIC_API_KEY is not configured")

    try:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key, timeout=8.0)
        prompt = f"""Compare removed and added fields in an Indonesian OSS API contract.
Removed: {json.dumps(removed)}
Added: {json.dumps(added)}
Return only a JSON array. Each item must have old_field, new_field,
semantic_match (boolean), confidence (0 to 1), and reason. Propose only defensible matches."""
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "\n".join(block.text for block in response.content if block.type == "text")
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            raise ValueError("model response did not contain a JSON array")
        mappings = json.loads(match.group(0))
        return {"mode": "live_ai", "model": "claude-sonnet-4-6", "mappings": mappings}
    except Exception as exc:
        return _fallback(removed, added, f"Live AI analysis failed: {exc}")
