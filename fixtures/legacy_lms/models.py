"""Internal model types used by the legacy LMS repository."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LandRecord:
    nib: str
    company_name: str
    allocation_reference: str
    plot: str
    status: str
    last_verified: str

    def to_dict(self) -> dict:
        return asdict(self)
