"""Deterministic mock government APIs, including observable OSS v1/v2 drift."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.state_store import STORE, StateStore

app = FastAPI(title="Mock Government APIs")

CITIZENS = [
    {
        "name": "john doe",
        "phone": "+62838292938",
        "nik": "2171012507890001",
        "dob": "1989-07-25",
        "registered_address": "Jl. Engku Putri No. 12, Batam Center",
        "npwp": "09.254.294.3-217.000",
        "npwp_last_verified": "2026-03-14",
        "blood_type": "O+",
        "allergies": ["amoxicillin"],
        "last_checkup": {
            "facility": "Klinik Medika Batu Aji",
            "date": "2026-05-02",
            "notes": "Hypertension follow-up",
        },
        "nib": "1209260012345",
        "company_name": "PT Selat Niaga Makmur",
        "deed_number": "AHU-0045821.AH.01.01.2026",
        "sk_kemenkumham": "AHU-0045821.AH.01.01.TAHUN 2026",
        "notary": "Ratna Dewi, S.H., M.Kn. (Batam)",
        "deed_date": "2026-02-11",
        "kbli": ["46900 — General trading", "52101 — Warehousing"],
        "risk_level": "Medium-Low",
        "masterlist": [
            "Steel coils (HS 7208) — 500t/yr",
            "Forklift spare parts (HS 8431) — 200 units/yr",
        ],
        "uwto_status": "Paid through 2027",
        "plot": "Kabil Industrial Estate Blok C-4 (2,400 m²)",
    },
    {
        "name": "siti rahma",
        "phone": "+628117701234",
        "nik": "2171045508920002",
        "dob": "1992-08-15",
        "registered_address": "Perum Tiban Indah Blok F2 No. 8, Sekupang",
        "npwp": "81.442.108.9-217.000",
        "npwp_last_verified": "2025-11-30",
        "blood_type": "B-",
        "allergies": [],
        "last_checkup": {"facility": "RSUD Embung Fatimah", "date": "2026-01-19", "notes": "Annual check"},
        "nib": "0906230098776",
        "company_name": "CV Rahma Boga Lestari",
        "deed_number": "AHU-0031177.AH.01.14.2023",
        "sk_kemenkumham": "AHU-0031177.AH.01.14.TAHUN 2023",
        "notary": "Hasan Basri, S.H. (Batam)",
        "deed_date": "2023-06-09",
        "kbli": ["56101 — Restaurant"],
        "risk_level": "Low",
        "masterlist": [],
        "uwto_status": "N/A (leased shophouse)",
        "plot": "-",
    },
    {
        "name": "budi santoso",
        "phone": "+62811770909",
        "nik": "2171010101850003",
        "dob": "1985-01-01",
        "registered_address": "Jl. Duyung No. 3, Batu Ampar",
        "npwp": None,
        "npwp_last_verified": None,
        "blood_type": "A+",
        "allergies": ["seafood (shellfish)"],
        "last_checkup": {"facility": "Puskesmas Batu Ampar", "date": "2025-09-08", "notes": "Work injury"},
        "nib": None,
        "company_name": None,
        "deed_number": None,
        "sk_kemenkumham": None,
        "notary": None,
        "deed_date": None,
        "kbli": [],
        "risk_level": None,
        "masterlist": [],
        "uwto_status": None,
        "plot": None,
    },
]


def _digits(value: str | None) -> str:
    return "".join(character for character in (value or "") if character.isdigit())


def _by_nik(nik: str) -> dict[str, Any] | None:
    normalized = _digits(nik)
    return next((citizen for citizen in CITIZENS if citizen["nik"] == normalized), None)


def _by_nib(nib: str) -> dict[str, Any] | None:
    normalized = _digits(nib)
    return next((citizen for citizen in CITIZENS if citizen["nib"] == normalized), None)


def government_response(
    path: str,
    params: dict[str, Any],
    *,
    store: StateStore = STORE,
) -> tuple[int, dict[str, Any]]:
    """Pure dispatcher shared by HTTP routes and deterministic integration tests."""
    if path == "/dukcapil/getNIK":
        query = str(params.get("name", "")).strip().lower()
        citizen = next((item for item in CITIZENS if query in item["name"] or item["name"] in query), None)
        if not citizen:
            return 404, {"error": "No citizen matches that name"}
        if not _digits(str(params.get("phone", ""))).endswith(_digits(citizen["phone"])[-8:]):
            return 409, {"error": "Name found but phone does not match registration"}
        return 200, {
            "nik": citizen["nik"],
            "registered_address": citizen["registered_address"],
            "dob": citizen["dob"],
        }

    if path == "/djp/getNPWP":
        citizen = _by_nik(str(params.get("nik", "")))
        if not citizen:
            return 404, {"error": "NIK not found"}
        return 200, {"npwp": citizen["npwp"], "last_verified": citizen["npwp_last_verified"]}

    if path == "/satusehat/getRecord":
        citizen = _by_nik(str(params.get("nik", "")))
        if not citizen:
            return 404, {"error": "NIK not found in SATUSEHAT index"}
        return 200, {
            "blood_type": citizen["blood_type"],
            "allergies": citizen["allergies"],
            "last_checkup": citizen["last_checkup"],
        }

    if path in {"/oss/getNIB", "/oss/business-by-director"}:
        version = store.demo()["oss_version"]
        if path == "/oss/getNIB" and version != 1:
            return 410, {"error": "OSS v1 operation was removed in demo-oss-v2"}
        if path == "/oss/business-by-director" and version != 2:
            return 404, {"error": "OSS v2 operation is not active"}
        citizen = _by_nik(str(params.get("nik", "")))
        if not citizen:
            return 404, {"error": "NIK not found"}
        if version == 1:
            return 200, {
                "nib": citizen["nib"],
                "company_name": citizen["company_name"],
                "kbli": citizen["kbli"],
                "risk_level": citizen["risk_level"],
            }
        return 200, {
            "business_identification_number": citizen["nib"],
            "legal_name": citizen["company_name"],
            "activity_codes": citizen["kbli"],
            "risk_classification": citizen["risk_level"],
        }

    if path == "/bpbatam/getMasterlist":
        citizen = _by_nib(str(params.get("nib", "")))
        if not citizen:
            return 404, {"error": "NIB not registered with BP Batam"}
        return 200, {
            "masterlist_items": citizen["masterlist"],
            "uwto_status": citizen["uwto_status"],
            "plot": citizen["plot"],
            "prior_workflow": "BP Batam land allocation",
            "last_verified": "2026-03-10",
        }

    if path == "/ahu/getDeed":
        citizen = _by_nik(str(params.get("nik", "")))
        if not citizen:
            return 404, {"error": "NIK not found"}
        return 200, {
            "deed_number": citizen["deed_number"],
            "sk_kemenkumham": citizen["sk_kemenkumham"],
            "notary": citizen["notary"],
            "deed_date": citizen["deed_date"],
            "company_name": citizen["company_name"],
        }

    return 404, {"error": f"Unknown mock path: {path}"}


def local_requester(method: str, url: str, params: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Requester injection for tests; production demo resolution still uses HTTP."""
    if method != "GET":
        return 405, {"error": "Method not allowed"}
    return government_response(urlparse(url).path, params)


def _as_response(result: tuple[int, dict[str, Any]]):
    status, body = result
    return body if status < 400 else JSONResponse(status_code=status, content=body)


@app.get("/dukcapil/getNIK")
def dukcapil_get_nik(name: str, phone: str):
    return _as_response(government_response("/dukcapil/getNIK", {"name": name, "phone": phone}))


@app.get("/djp/getNPWP")
def djp_get_npwp(nik: str):
    return _as_response(government_response("/djp/getNPWP", {"nik": nik}))


@app.get("/satusehat/getRecord")
def satusehat_get_record(nik: str):
    return _as_response(government_response("/satusehat/getRecord", {"nik": nik}))


@app.get("/oss/getNIB")
def oss_get_nib(nik: str):
    return _as_response(government_response("/oss/getNIB", {"nik": nik}))


@app.get("/oss/business-by-director")
def oss_business_by_director(nik: str):
    return _as_response(government_response("/oss/business-by-director", {"nik": nik}))


@app.get("/bpbatam/getMasterlist")
def bpbatam_get_masterlist(nib: str):
    return _as_response(government_response("/bpbatam/getMasterlist", {"nib": nib}))


@app.get("/ahu/getDeed")
def ahu_get_deed(nik: str):
    return _as_response(government_response("/ahu/getDeed", {"nik": nik}))


@app.get("/demo/oss/version")
def get_oss_version():
    return STORE.demo()


@app.post("/demo/oss/version/{version}")
def set_oss_version(version: int):
    if version not in {1, 2}:
        return JSONResponse(status_code=422, content={"error": "version must be 1 or 2"})
    state = STORE.demo()
    state["oss_version"] = version
    STORE.save_demo(state)
    return state
