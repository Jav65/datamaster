"""Internal repository module from a hypothetical legacy BP Batam LMS."""

from __future__ import annotations

from fixtures.legacy_lms.models import LandRecord

_RECORDS = {
    "1209260012345": LandRecord(
        nib="1209260012345",
        company_name="PT Selat Niaga Makmur",
        allocation_reference="LMS-BA-2026-00417",
        plot="Kabil Industrial Estate Blok C-4 (2,400 m²)",
        status="Active — UWTO paid through 2027",
        last_verified="2026-03-10",
    )
}


def find_land_record(nib: str) -> dict | None:
    """Return the verified land-allocation record for one business NIB."""
    record = _RECORDS.get("".join(character for character in nib if character.isdigit()))
    return record.to_dict() if record else None
