"""HTTP boundary for the dummy BP Batam service."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from bp_batam_api.models import (
    ImportMasterlist,
    LandRecord,
    PermitValidationRequest,
    PermitValidationResult,
    UwtoStatus,
)
from bp_batam_api.repository import (
    find_land_record,
    find_masterlist,
    find_uwto_status,
)

SERVICE_METADATA = {
    "name": "BP Batam",
    "hostname": "api.bpbatam.local",
}

app = FastAPI(
    title="BP Batam Service API",
    version="1.4.0",
    description="Land, import, and permit data for BP Batam workflows.",
)


@app.get(
    "/api/v1/land-records/{nib}",
    response_model=LandRecord,
    summary="Get land record",
    description="Retrieve the active BP Batam land-allocation record associated with a business NIB.",
)
def get_land_record(nib: str) -> LandRecord:
    record = find_land_record(nib)
    if record is None:
        raise HTTPException(status_code=404, detail="Land record not found")
    return LandRecord.model_validate(record)


@app.get(
    "/api/v1/masterlists/{nib}",
    response_model=ImportMasterlist,
    summary="Get import masterlist",
    description="Retrieve the approved duty-free import masterlist and annual quotas for a business NIB.",
)
def get_import_masterlist(nib: str) -> ImportMasterlist:
    masterlist = find_masterlist(nib)
    if masterlist is None:
        raise HTTPException(status_code=404, detail="Import masterlist not found")
    return ImportMasterlist.model_validate(masterlist)


@app.get(
    "/api/v1/uwto/{nib}/status",
    response_model=UwtoStatus,
    summary="Get UWTO status",
    description="Retrieve the current UWTO payment status for a BP Batam land holder identified by NIB.",
)
def get_uwto_status(nib: str) -> UwtoStatus:
    status = find_uwto_status(nib)
    if status is None:
        raise HTTPException(status_code=404, detail="UWTO status not found")
    return UwtoStatus.model_validate(status)


@app.post(
    "/api/v1/permit-validations",
    response_model=PermitValidationResult,
    status_code=200,
    summary="Validate permit prerequisites",
    description="Validate that a business activity and warehouse plot satisfy the known BP Batam permit prerequisites.",
)
def validate_permit(body: PermitValidationRequest) -> PermitValidationResult:
    land = find_land_record(body.nib)
    checks = ["business_nib_supplied", "activity_code_supplied", "warehouse_plot_supplied"]
    eligible = bool(land and body.activity_code and land["plot"] == body.warehouse_plot)
    return PermitValidationResult(
        eligible=eligible,
        checks=checks,
        reason="Known prerequisites matched" if eligible else "One or more prerequisites did not match",
    )

