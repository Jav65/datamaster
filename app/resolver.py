"""Stable canonical DataMaster resolver used by the permit backend."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.policy import authorize
from app.state_store import STORE, StateStore

MOCK_GOVERNMENT_BASE_URL = "http://127.0.0.1:9001"
Requester = Callable[[str, str, dict[str, Any]], tuple[int, dict[str, Any]]]


@dataclass
class ResolutionError(Exception):
    message: str
    status_code: int = 502
    service: str | None = None

    def __str__(self) -> str:
        return self.message


def http_requester(method: str, url: str, params: dict[str, Any]) -> tuple[int, dict]:
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.request(method, url, params=params)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:500]}
        return response.status_code, body
    except httpx.HTTPError as exc:
        raise ResolutionError(f"Authoritative service request failed: {exc}") from exc


def _extract(body: dict[str, Any], path: str) -> Any:
    value: Any = body
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _service_call(
    service: str,
    operation: str,
    endpoint: str,
    params: dict[str, Any],
    trace: list[dict[str, Any]],
    requester: Requester,
) -> dict[str, Any]:
    status, body = requester("GET", MOCK_GOVERNMENT_BASE_URL + endpoint, params)
    trace.append(
        {
            "step": len(trace) + 1,
            "service": service,
            "operation": operation,
            "status": status,
        }
    )
    if status >= 400:
        message = body.get("error") or body.get("detail") or f"HTTP {status}"
        if service == "oss" and status in {404, 410}:
            raise ResolutionError(
                "OSS changed its contract. DataMaster blocked an unsafe automatic remap; "
                "review the pending change before retrying.",
                status_code=503,
                service="oss",
            )
        raise ResolutionError(f"{service} returned {status}: {message}", service=service)
    return body


def resolve(
    subject: dict[str, str],
    fields: list[str],
    purpose: str,
    *,
    store: StateStore = STORE,
    requester: Requester = http_requester,
) -> dict[str, Any]:
    """Resolve canonical fields and attach provenance for every requested field."""
    policy = authorize(purpose, fields)
    registry = store.registry()
    concepts = registry["concepts"]
    unknown = [field for field in fields if field not in concepts]
    if unknown:
        raise ResolutionError(f"Unknown canonical concepts: {', '.join(unknown)}", 422)

    data: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    trace: list[dict[str, Any]] = []
    bodies: dict[str, dict[str, Any]] = {}
    called_services: list[str] = []

    for field in fields:
        if concepts[field].get("manual"):
            data[field] = None
            provenance[field] = {
                "source": None,
                "verified": False,
                "status": "manual_required",
            }

    automatic_fields = [field for field in fields if not concepts[field].get("manual")]
    if not automatic_fields:
        return {
            "data": data,
            "provenance": provenance,
            "called_services": [],
            "blocked_services": list(policy["blocked_services"]),
            "trace": trace,
            "policy": {"purpose": purpose, "label": policy["label"], "enforced": True},
            "registry_revision": registry["revision"],
        }

    name, phone = subject.get("name", "").strip(), subject.get("phone", "").strip()
    if not name or not phone:
        raise ResolutionError("subject.name and subject.phone are required", 422)

    dukcapil = concepts["person.nik"]
    bodies["dukcapil"] = _service_call(
        "dukcapil",
        dukcapil["operation"],
        dukcapil["endpoint"],
        {"name": name, "phone": phone},
        trace,
        requester,
    )
    called_services.append("dukcapil")
    nik = _extract(bodies["dukcapil"], dukcapil["response_path"])

    required_services = {
        concepts[field]["authoritative_service"] for field in automatic_fields
    }
    needs_oss = "oss" in required_services or "bpbatam" in required_services
    if needs_oss:
        oss = concepts["business.nib"]
        bodies["oss"] = _service_call(
            "oss",
            oss["operation"],
            oss["endpoint"],
            {"nik": nik},
            trace,
            requester,
        )
        called_services.append("oss")

    if "djp" in required_services:
        djp = concepts["business.npwp"]
        bodies["djp"] = _service_call(
            "djp", djp["operation"], djp["endpoint"], {"nik": nik}, trace, requester
        )
        called_services.append("djp")

    if "ahu" in required_services:
        ahu = concepts["business.company_deed"]
        bodies["ahu"] = _service_call(
            "ahu", ahu["operation"], ahu["endpoint"], {"nik": nik}, trace, requester
        )
        called_services.append("ahu")

    if "bpbatam" in required_services:
        nib_config = concepts["business.nib"]
        nib = _extract(bodies["oss"], nib_config["response_path"])
        bpbatam = concepts["bpbatam.land_record"]
        bodies["bpbatam"] = _service_call(
            "bpbatam",
            bpbatam["operation"],
            bpbatam["endpoint"],
            {"nib": nib},
            trace,
            requester,
        )
        called_services.append("bpbatam")

    for field in automatic_fields:
        config = concepts[field]
        service = config["authoritative_service"]
        body = bodies[service]
        data[field] = _extract(body, config["response_path"])
        verified_at = (
            _extract(body, config["last_verified_path"])
            if config.get("last_verified_path")
            else config.get("last_verified")
        )
        provenance[field] = {
            "source": registry["services"][service]["name"],
            "service": service,
            "verified": data[field] is not None,
            "last_verified": verified_at,
            "operation": config["operation"],
        }

    return {
        "data": data,
        "provenance": provenance,
        "called_services": called_services,
        "blocked_services": list(policy["blocked_services"]),
        "trace": trace,
        "policy": {"purpose": purpose, "label": policy["label"], "enforced": True},
        "registry_revision": registry["revision"],
    }
