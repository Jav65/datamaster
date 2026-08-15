"""Public request and response models for the dummy BP Batam API."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class LandRecord(BaseModel):
    nib: str = Field(description="Thirteen-digit Indonesian Business Identification Number")
    plot: str
    area_square_metres: int = Field(gt=0)
    allocation_status: str
    last_verified: date


class MasterlistItem(BaseModel):
    hs_code: str
    description: str
    annual_quota: str


class ImportMasterlist(BaseModel):
    nib: str
    items: list[MasterlistItem]
    approved_until: date


class UwtoStatus(BaseModel):
    nib: str
    status: str
    paid_through_year: int | None


class PermitValidationRequest(BaseModel):
    nib: str
    activity_code: str
    warehouse_plot: str


class PermitValidationResult(BaseModel):
    eligible: bool
    checks: list[str]
    reason: str

