"""Local resident records created through the Disdukcapil demo form.

This JSON-backed workflow exists only for the self-contained demonstration. A
production system would keep each agency's data in that agency's own secured
database and would never copy a combined resident profile into DataMaster.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.state_store import STORE, StateStore


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def list_demo_resident_records(*, store: StateStore = STORE) -> list[dict[str, Any]]:
    """Return copies of records submitted since the last demo reset."""

    return [dict(record) for record in store.demo().get("disdukcapil_records", [])]


def register_demo_resident(
    submitted: dict[str, Any],
    *,
    store: StateStore = STORE,
) -> dict[str, Any]:
    """Validate-normalize and upsert one local cross-agency demo record."""

    record = {
        **submitted,
        "name": str(submitted["name"]).strip().lower(),
        "phone": "+" + _digits(str(submitted["phone"])),
        "nik": _digits(str(submitted["nik"])),
        "nib": _digits(str(submitted["nib"])),
        "kbli": [
            str(item).strip()
            for item in submitted.get("kbli", [])
            if str(item).strip()
        ],
        "npwp_last_verified": datetime.now(timezone.utc).date().isoformat(),
        "deed_date": None,
        "blood_type": None,
        "allergies": [],
        "last_checkup": None,
        "masterlist": [],
        "uwto_status": None,
        "plot": None,
        "registered_at": datetime.now(timezone.utc).isoformat(),
    }

    state = store.demo()
    records = [
        existing
        for existing in state.get("disdukcapil_records", [])
        if existing.get("nik") != record["nik"]
    ]
    records.append(record)
    state["disdukcapil_records"] = records
    store.save_demo(state)
    return dict(record)
