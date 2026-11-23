"""C58 - engångsjobb: Mikkos egna svar för två hotellnätter.

Mikko har själv svarat (2026-11-23) att båda nätterna betalades direkt av
företaget. Inget annat förifylls. Bara bottens egen tabell ändras.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models import CalendarTrip, MaintenanceTask

logger = logging.getLogger(__name__)

TASK = "c58_hotel_answers_v1"
ANSWERS = (
    # (natt, resans id då Mikko svarade, anteckning)
    ("2026-01-21", 130, "Riverside Hotel Ängelholm - betald direkt av företaget (Mikko 2026-11-23)"),
    ("2026-04-22", 135, "Hotel Terazza - betald direkt av företaget (Mikko 2026-11-23)"),
)


def _trip_for_night(db: Session, night: date, trip_id: int) -> CalendarTrip | None:
    t = db.get(CalendarTrip, trip_id)
    if t is not None and t.status != "rejected" and t.start_date <= night < t.end_date:
        return t
    # Resans id kan ha bytts vid kalender-refresh: samma natt räcker.
    return (db.query(CalendarTrip)
            .filter(CalendarTrip.status != "rejected",
                    CalendarTrip.start_date <= night, CalendarTrip.end_date > night)
            .order_by(CalendarTrip.id).first())


def apply_mikko_answers(db: Session, *, force: bool = False) -> list[dict] | None:
    from app.services.trip_completeness import set_hotel_night_choice

    if not force and db.query(MaintenanceTask).filter(MaintenanceTask.name == TASK).first():
        return None
    out = []
    for night_s, trip_id, note in ANSWERS:
        night = date.fromisoformat(night_s)
        trip = _trip_for_night(db, night, trip_id)
        if trip is None:
            out.append({"night": night_s, "status": "no_trip"})
            continue
        try:
            cl = set_hotel_night_choice(db, trip.id, night_s, "invoiced_to_company", note)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            out.append({"night": night_s, "trip_id": trip.id, "status": f"error: {exc}"})
            continue
        row = next((i for i in cl["items"] if i["kind"] == "hotel" and i["slot"] == night_s), {})
        out.append({"night": night_s, "trip_id": trip.id, "status": row.get("resolution")})
    if all(r["status"] == "invoiced_to_company" for r in out):
        db.add(MaintenanceTask(name=TASK))
        db.commit()
    logger.info("C58: Mikkos hotellsvar: %s", out)
    return out
