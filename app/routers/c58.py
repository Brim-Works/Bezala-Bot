"""C58 - hotellnätter utan kvitto: Mikkos val (sparas i bottens tabell) och
tips (bara läsning). Inget skickas till Bezala eller Netvisor."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import c58_hotel_nights as c58
from app.services.calendar_trip_edit import TripEditError
from app.services.trip_completeness import set_hotel_night_choice


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


router = APIRouter(prefix="/api/c58", dependencies=[Depends(require_auth)])


class NightChoice(BaseModel):
    night: str
    choice: str  # no_hotel | handled_in_bezala | invoiced_to_company | receipt_coming | undo
    note: str | None = None


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except TripEditError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 400,
                            detail=str(exc)) from exc


@router.post("/calendar-trips/{trip_id}/hotel-night")
def post_hotel_night(trip_id: int, body: NightChoice, db: Session = Depends(get_db)):
    return _guard(set_hotel_night_choice, db, trip_id, body.night, body.choice, body.note)


@router.get("/calendar-trips/{trip_id}/hotel-tips")
def get_hotel_tips(trip_id: int, bezala: bool = True, db: Session = Depends(get_db)):
    return _guard(c58.hotel_night_tips, db, trip_id, use_bezala=bezala)
