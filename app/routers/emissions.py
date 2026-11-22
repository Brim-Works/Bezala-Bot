"""C53 - km och energi per resa (Resor-vyn + export för Admin).

Bara bottens egen tabell `trip_legs`. Inga skrivningar till Bezala/Netvisor.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import trip_legs
from app.services.calendar_trip_edit import TripEditError


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


router = APIRouter(prefix="/api/emissions", dependencies=[Depends(require_auth)])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TripEditError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 400,
                            detail=str(exc)) from exc


@router.get("/summary")
def summary(year: int = 2026, db: Session = Depends(get_db)):
    return trip_legs.get_emissions_summary(db, year)


@router.get("/trips/{trip_id}/legs")
def legs(trip_id: int, db: Session = Depends(get_db)):
    return _guard(trip_legs.get_trip_legs, db, trip_id)


class LegPatch(BaseModel):
    km: float | None = None
    mode: str | None = None
    date: str | None = None
    from_place: str | None = None
    to_place: str | None = None
    energy_type: str | None = None
    energy_amount: float | None = None
    energy_unit: str | None = None
    source: str | None = None
    is_estimate: bool | None = None
    note: str | None = None
    flight_category: str | None = None
    excluded: bool | None = None


@router.patch("/legs/{leg_id}")
def patch_leg(leg_id: int, payload: LegPatch, db: Session = Depends(get_db)):
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    data["date_"] = data.pop("date")
    return _guard(trip_legs.update_trip_leg, db, leg_id, **data)


@router.get("/export.csv")
def export_csv(year: int = 2026, db: Session = Depends(get_db)):
    return Response(
        content=trip_legs.export_csv(db, year), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="trip-legs-{year}.csv"'},
    )


@router.get("/export.json")
def export_json(year: int = 2026, db: Session = Depends(get_db)):
    rows = trip_legs.export_rows(db, year)
    return Response(
        content=json.dumps({"year": year, "count": len(rows), "rows": rows},
                           ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="trip-legs-{year}.json"'},
    )
