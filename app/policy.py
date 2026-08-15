"""Deterministic purpose policy: the LLM is never an authorization boundary."""

from __future__ import annotations

from dataclasses import dataclass

PERMIT_PURPOSE = "bpbatam.iuk_logistik.application"

PURPOSE_POLICIES = {
    PERMIT_PURPOSE: {
        "label": "BP Batam logistics permit",
        "allowed": {
            "person.nik",
            "person.date_of_birth",
            "person.registered_address",
            "person.email",
            "business.nib",
            "business.company_name",
            "business.npwp",
            "business.company_deed",
            "business.sk_kemenkumham",
            "business.notary",
            "business.risk_level",
            "business.kbli",
            "bpbatam.land_record",
            "application.warehouse_evidence",
            "application.warehouse_plan",
            "application.logistics_purpose",
        },
        "forbidden_prefixes": ("health.",),
        "blocked_services": ("satusehat",),
    }
}


@dataclass
class PolicyDenied(Exception):
    purpose: str
    denied_fields: list[str]

    def __str__(self) -> str:
        return f"Purpose '{self.purpose}' may not access: {', '.join(self.denied_fields)}"


def authorize(purpose: str, fields: list[str]) -> dict:
    policy = PURPOSE_POLICIES.get(purpose)
    if policy is None:
        raise PolicyDenied(purpose, fields)
    denied = [field for field in fields if field not in policy["allowed"]]
    if denied:
        raise PolicyDenied(purpose, denied)
    return policy
