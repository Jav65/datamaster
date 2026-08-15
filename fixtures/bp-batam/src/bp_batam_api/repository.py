"""In-memory fixture data; a real service would read from PostgreSQL."""

from __future__ import annotations

from datetime import date


LAND_RECORDS = {
    "1209260012345": {
        "nib": "1209260012345",
        "plot": "Kabil Industrial Estate Blok C-4",
        "area_square_metres": 2400,
        "allocation_status": "active",
        "last_verified": date(2026, 3, 10),
    }
}

MASTERLISTS = {
    "1209260012345": {
        "nib": "1209260012345",
        "items": [
            {
                "hs_code": "7208",
                "description": "Steel coils",
                "annual_quota": "500 tonnes",
            },
            {
                "hs_code": "8431",
                "description": "Forklift spare parts",
                "annual_quota": "200 units",
            },
        ],
        "approved_until": date(2027, 12, 31),
    }
}

UWTO_STATUSES = {
    "1209260012345": {
        "nib": "1209260012345",
        "status": "paid",
        "paid_through_year": 2027,
    }
}


def find_land_record(nib: str) -> dict | None:
    return LAND_RECORDS.get(nib)


def find_masterlist(nib: str) -> dict | None:
    return MASTERLISTS.get(nib)


def find_uwto_status(nib: str) -> dict | None:
    return UWTO_STATUSES.get(nib)

