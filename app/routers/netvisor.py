"""C49 - Netvisor-underlag i Resor-vyn (läsning + nedladdning).

Inga skrivningar till Netvisor: det finns ingen endpoint som skickar.
NetvisorClient (avstängd) anropas inte härifrån.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

import app.models_netvisor  # noqa: F401  (registrerar tabellen före create_all)
from app.db import get_db
from app.services.calendar_trip_edit import TripEditError


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


router = APIRouter(prefix="/api/netvisor", dependencies=[Depends(require_auth)])


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except TripEditError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 400,
                            detail=str(exc)) from exc


@router.get("/overview")
def get_overview(year: int = 2026, ready_only: bool = False, db: Session = Depends(get_db)):
    from app.services.netvisor_status import overview

    return overview(db, year=year, ready_only=ready_only)


@router.get("/status")
def get_status(year: int = 2026, db: Session = Depends(get_db)):
    from app.services.netvisor_status import list_status

    trips = list_status(db, year)
    return {"year": year, "count": len(trips), "trips": trips}


@router.get("/descriptions")
def get_descriptions(year: int = 2026, db: Session = Depends(get_db)):
    from app.services.netvisor_status import preview_descriptions

    return preview_descriptions(db, year)


@router.get("/trips/{trip_id}/entry")
def get_entry(trip_id: int, db: Session = Depends(get_db)):
    from app.services.netvisor_entry import build_entry

    return _guard(build_entry, db, trip_id)


@router.get("/trips/{trip_id}/payload")
def get_payload(trip_id: int, db: Session = Depends(get_db)):
    from app.services.netvisor_payload import preview

    return _guard(preview, db, trip_id)


@router.get("/trips/{trip_id}/payload.xml")
def download_xml(trip_id: int, db: Session = Depends(get_db)):
    from app.services.netvisor_payload import preview

    out = _guard(preview, db, trip_id)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?>\n' + out["xml"] + "\n",
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="tripexpense-trip-{trip_id}.xml"'},
    )


@router.get("/trips/{trip_id}/payload.json")
def download_json(trip_id: int, db: Session = Depends(get_db)):
    from app.services.netvisor_payload import preview

    out = _guard(preview, db, trip_id)
    body = {
        "trip_id": trip_id,
        "resource": out["resource"],
        "payload": out["json"],
        "warnings": out["warnings"],
        "uncertain_fields": out["uncertain_fields"],
    }
    return Response(
        content=json.dumps(body, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="tripexpense-trip-{trip_id}.json"'},
    )
