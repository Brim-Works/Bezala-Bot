"""CRUD-endpoints för bezala_vendor_mappings.

Konfigurationen lagras isolerat i den här PR:en — applicerandet i
upload-flödet kommer i ett separat PR (se README/PR-beskrivning)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import bezala_config as service


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


router = APIRouter(
    prefix="/api/bezala-config",
    tags=["bezala-config"],
)


class CreateMappingPayload(BaseModel):
    vendor_pattern: str = Field(..., min_length=1, max_length=200)
    bezala_account_id: int = Field(..., gt=0)
    vat_rate: float = Field(..., ge=0, le=100)
    description_override: str | None = Field(default=None, max_length=500)


class UpdateMappingPayload(BaseModel):
    vendor_pattern: str | None = Field(default=None, max_length=200)
    bezala_account_id: int | None = Field(default=None, gt=0)
    vat_rate: float | None = Field(default=None, ge=0, le=100)
    description_override: str | None = Field(default=None, max_length=500)

    model_config = {"extra": "forbid"}


@router.get("")
def list_mappings_endpoint(
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    rows = service.list_mappings(db)
    return {"mappings": [service.serialize(r) for r in rows]}


@router.post("", status_code=201)
def create_mapping_endpoint(
    payload: CreateMappingPayload,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    try:
        row = service.create_mapping(
            db,
            vendor_pattern=payload.vendor_pattern,
            bezala_account_id=payload.bezala_account_id,
            vat_rate=payload.vat_rate,
            description_override=payload.description_override,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return service.serialize(row)


@router.patch("/{mapping_id}")
def update_mapping_endpoint(
    mapping_id: int,
    payload: UpdateMappingPayload,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    description_override_set = "description_override" in payload.model_fields_set
    try:
        row = service.update_mapping(
            db,
            mapping_id,
            vendor_pattern=payload.vendor_pattern,
            bezala_account_id=payload.bezala_account_id,
            vat_rate=payload.vat_rate,
            description_override=payload.description_override,
            description_override_set=description_override_set,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if row is None:
        raise HTTPException(status_code=404, detail="Mappning finns inte")
    return service.serialize(row)


@router.delete("/{mapping_id}")
def delete_mapping_endpoint(
    mapping_id: int,
    db: Session = Depends(get_db),
    _: None = Depends(require_auth),
):
    if not service.delete_mapping(db, mapping_id):
        raise HTTPException(status_code=404, detail="Mappning finns inte")
    return {"success": True}
