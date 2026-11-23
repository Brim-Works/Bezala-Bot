"""C56 - kompakt Resor-vy: förslag efter städning + Godkänn-knapp.

Godkänn ändrar bara bottens egen status (calendar_trips.status) - inget
skickas till Bezala eller Netvisor. Sammanslagning av dubbletter finns bara
som MCP-verktyg (dry-run först, sedan på Mikkos ja), inte här.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import c56_trip_suggestions as c56
from app.services.calendar_trip_edit import TripEditError, approve_trip, unapprove_trip


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


router = APIRouter(prefix="/api/c56", dependencies=[Depends(require_auth)])


def _guard(fn, *args):
    try:
        return fn(*args)
    except TripEditError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 400,
                            detail=str(exc)) from exc


@router.get("/suggestions")
def get_suggestions(db: Session = Depends(get_db)):
    """Förslagen med täckning/ålder/dubbletter. Ren läsning."""
    return c56.analyse(db)


@router.post("/calendar-trips/{trip_id}/approve")
def post_approve(trip_id: int, db: Session = Depends(get_db)):
    return _guard(approve_trip, db, trip_id)


@router.post("/calendar-trips/{trip_id}/unapprove")
def post_unapprove(trip_id: int, db: Session = Depends(get_db)):
    return _guard(unapprove_trip, db, trip_id)
