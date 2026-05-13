"""Bezala config-admin — vendor→account+VAT-mappningar.

Default-mappningar seedas idempotent en gång per deploy (kontrolleras
via MaintenanceTask). Bug-bakgrund: Bezala använder VAT 14% (id 864) som
default för Parkering (konto 67113), men finsk parkering är 25.5%.
Tills upload-flödet börjar konsultera den här tabellen är detta bara
en konfigurationsyta — applicerandet kommer i ett separat PR.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import BezalaVendorMapping, MaintenanceTask

logger = logging.getLogger(__name__)


SEED_TASK_NAME = "seed_bezala_vendor_mappings_v2"


# (vendor_pattern, bezala_account_id, vat_rate, description_override)
#
# AI-verktyg (Lovable, Anthropic, Cursor) bokförs på "AI työkalut" (166648)
# med VAT 0% — alla tre fakturerar Mikko som EU-OSS-registrerade leverantörer
# vilket innebär omvänd skattskyldighet ("Purchases Abroad (EU)") i Bezala.
# description_override=None → ai_description_en flödar genom (C8).
DEFAULT_MAPPINGS: tuple[tuple[str, int, str, str | None], ...] = (
    (
        "moovy",
        67113,
        "25.50",
        "Parking at Helsinki-Vantaa Airport P2",
    ),
    (
        "finavia",
        67113,
        "25.50",
        "Parking at Helsinki-Vantaa Airport P2",
    ),
    (
        "lovable",
        166648,
        "0.00",
        None,
    ),
    (
        "anthropic",
        166648,
        "0.00",
        None,
    ),
    (
        "cursor",
        166648,
        "0.00",
        None,
    ),
)


def _normalize_pattern(pattern: str) -> str:
    return (pattern or "").strip().lower()


def _coerce_vat_rate(value) -> Decimal:
    if value is None:
        raise ValueError("vat_rate saknas")
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"vat_rate måste vara numerisk: {value!r}") from exc
    if rate < Decimal("0") or rate > Decimal("100"):
        raise ValueError("vat_rate måste vara mellan 0 och 100")
    return rate.quantize(Decimal("0.01"))


def serialize(row: BezalaVendorMapping) -> dict:
    return {
        "id": row.id,
        "vendor_pattern": row.vendor_pattern,
        "bezala_account_id": row.bezala_account_id,
        "vat_rate": str(row.vat_rate) if row.vat_rate is not None else None,
        "description_override": row.description_override,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_mappings(db: Session) -> list[BezalaVendorMapping]:
    return (
        db.query(BezalaVendorMapping)
        .order_by(BezalaVendorMapping.vendor_pattern)
        .all()
    )


def get_mapping(db: Session, mapping_id: int) -> BezalaVendorMapping | None:
    return (
        db.query(BezalaVendorMapping)
        .filter(BezalaVendorMapping.id == mapping_id)
        .first()
    )


def create_mapping(
    db: Session,
    *,
    vendor_pattern: str,
    bezala_account_id: int,
    vat_rate,
    description_override: str | None = None,
) -> BezalaVendorMapping:
    norm = _normalize_pattern(vendor_pattern)
    if not norm:
        raise ValueError("vendor_pattern saknas")
    if not isinstance(bezala_account_id, int) or bezala_account_id <= 0:
        raise ValueError("bezala_account_id måste vara ett positivt heltal")
    rate = _coerce_vat_rate(vat_rate)

    existing = (
        db.query(BezalaVendorMapping)
        .filter(BezalaVendorMapping.vendor_pattern == norm)
        .first()
    )
    if existing is not None:
        raise ValueError(f"Mappning för '{norm}' finns redan")

    row = BezalaVendorMapping(
        vendor_pattern=norm,
        bezala_account_id=bezala_account_id,
        vat_rate=rate,
        description_override=(description_override or "").strip() or None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def update_mapping(
    db: Session,
    mapping_id: int,
    *,
    vendor_pattern: str | None = None,
    bezala_account_id: int | None = None,
    vat_rate=None,
    description_override: str | None = None,
    description_override_set: bool = False,
) -> BezalaVendorMapping | None:
    row = get_mapping(db, mapping_id)
    if row is None:
        return None

    if vendor_pattern is not None:
        norm = _normalize_pattern(vendor_pattern)
        if not norm:
            raise ValueError("vendor_pattern får inte vara tomt")
        if norm != row.vendor_pattern:
            collision = (
                db.query(BezalaVendorMapping)
                .filter(BezalaVendorMapping.vendor_pattern == norm)
                .first()
            )
            if collision is not None and collision.id != row.id:
                raise ValueError(f"Mappning för '{norm}' finns redan")
            row.vendor_pattern = norm

    if bezala_account_id is not None:
        if not isinstance(bezala_account_id, int) or bezala_account_id <= 0:
            raise ValueError(
                "bezala_account_id måste vara ett positivt heltal",
            )
        row.bezala_account_id = bezala_account_id

    if vat_rate is not None:
        row.vat_rate = _coerce_vat_rate(vat_rate)

    if description_override_set:
        cleaned = (description_override or "").strip() or None
        row.description_override = cleaned

    db.commit()
    db.refresh(row)
    return row


def delete_mapping(db: Session, mapping_id: int) -> bool:
    row = get_mapping(db, mapping_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def seed_default_mappings(db: Session) -> int:
    """Idempotent. Markerar via MaintenanceTask när den första körningen
    skett. Vidare körningar skippar — användarens egna ändringar bevaras."""
    existing_task = (
        db.query(MaintenanceTask)
        .filter(MaintenanceTask.name == SEED_TASK_NAME)
        .first()
    )
    if existing_task is not None:
        return 0

    existing_patterns = {
        m.vendor_pattern for m in db.query(BezalaVendorMapping).all()
    }
    added = 0
    for pattern, account_id, vat_rate, description in DEFAULT_MAPPINGS:
        norm = _normalize_pattern(pattern)
        if not norm or norm in existing_patterns:
            continue
        db.add(BezalaVendorMapping(
            vendor_pattern=norm,
            bezala_account_id=account_id,
            vat_rate=Decimal(vat_rate),
            description_override=description,
        ))
        added += 1
    db.add(MaintenanceTask(name=SEED_TASK_NAME))
    db.commit()
    logger.info("bezala_vendor_mappings seedad — %d nya rader", added)
    return added


def find_mapping_for_vendor(
    vendor: str | None, mappings: Iterable[BezalaVendorMapping],
) -> BezalaVendorMapping | None:
    if not vendor:
        return None
    needle = vendor.lower()
    for m in mappings:
        if m.vendor_pattern and m.vendor_pattern in needle:
            return m
    return None
