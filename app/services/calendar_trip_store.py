"""Resor R1 — spara/läsa kalenderbaserade reseförslag.

Endast lokala tabeller (`calendar_trips`, `calendar_trip_days`) skrivs.
Ingenting skickas till Bezala eller Netvisor.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models import (
    CalendarTrip,
    CalendarTripDay,
    ProcessedMessage,
    Trip,
    TripMessage,
)

logger = logging.getLogger(__name__)

LOCKED_STATUSES = ("edited", "approved")


def _d(value: str) -> date:
    return date.fromisoformat(value)


def persist_detected(db: Session, detected: dict) -> dict:
    """Ersätt alla 'suggested'-resor med nya förslag.

    Förslag som överlappar en redigerad/godkänd resa hoppas över så att
    användarens ändringar (R2) aldrig skrivs över.
    """
    old = db.query(CalendarTrip).filter(CalendarTrip.status == "suggested").all()
    old_ids = [t.id for t in old]
    if old_ids:
        db.query(CalendarTripDay).filter(
            CalendarTripDay.trip_id.in_(old_ids)
        ).delete(synchronize_session=False)
        db.query(CalendarTrip).filter(
            CalendarTrip.id.in_(old_ids)
        ).delete(synchronize_session=False)

    locked = (
        db.query(CalendarTrip)
        .filter(CalendarTrip.status.in_(LOCKED_STATUSES))
        .all()
    )

    created = 0
    skipped_locked = 0
    for t in detected.get("trips", []):
        start, end = _d(t["start_date"]), _d(t["end_date"])
        if any(lt.start_date <= end and lt.end_date >= start for lt in locked):
            skipped_locked += 1
            continue
        row = CalendarTrip(
            start_date=start,
            end_date=end,
            start_at=t.get("start_at"),
            end_at=t.get("end_at"),
            destination=t.get("destination"),
            open_return=bool(t.get("open_return")),
            status="suggested",
            source_event_ids=t.get("source_event_ids") or [],
            flights=t.get("flights") or [],
            hotels=t.get("hotels") or [],
            flags=t.get("flags") or [],
            flight_options=t.get("flight_options") or [],
            country=t.get("country"),
        )
        db.add(row)
        db.flush()
        for day in t.get("days", []):
            db.add(CalendarTripDay(
                trip_id=row.id,
                day_date=_d(day["date"]),
                start_time=day.get("start_time"),
                end_time=day.get("end_time"),
                destination=day.get("destination"),
                meetings=day.get("meetings") or [],
                hotel=day.get("hotel"),
                source_event_ids=day.get("source_event_ids") or [],
                status="suggested",
            ))
        created += 1

    db.commit()
    logger.info(
        "Calendar trips: ersatte %d förslag med %d nya (%d krockade med låsta)",
        len(old_ids), created, skipped_locked,
    )
    return {
        "replaced": len(old_ids),
        "created": created,
        "skipped_locked": skipped_locked,
    }


def _linked_receipts(db: Session, start: date, end: date) -> list[dict]:
    """Kvitton redan kopplade (via trip_messages) till kvittoresor som
    överlappar datumintervallet."""
    rows = (
        db.query(TripMessage, ProcessedMessage, Trip)
        .join(Trip, Trip.id == TripMessage.trip_id)
        .join(ProcessedMessage, ProcessedMessage.message_id == TripMessage.message_id)
        .filter(
            TripMessage.removed_at.is_(None),
            ProcessedMessage.deleted_at.is_(None),
            Trip.status.in_(("suggested", "active")),
            Trip.start_date <= end,
            Trip.end_date >= start,
        )
        .all()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for _tm, msg, trip in rows:
        if msg.message_id in seen:
            continue
        seen.add(msg.message_id)
        out.append({
            "message_id": msg.message_id,
            "vendor": msg.vendor,
            "amount": msg.amount,
            "currency": msg.currency,
            "receipt_date": msg.receipt_date,
            "trip_id": trip.id,
        })
    out.sort(key=lambda r: r.get("receipt_date") or "")
    return out


def serialize_trip(db: Session, trip: CalendarTrip) -> dict:
    days = (
        db.query(CalendarTripDay)
        .filter(CalendarTripDay.trip_id == trip.id)
        .order_by(CalendarTripDay.day_date)
        .all()
    )
    return {
        "id": trip.id,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "start_at": trip.start_at,
        "end_at": trip.end_at,
        "destination": trip.destination,
        "open_return": bool(trip.open_return),
        "country": trip.country,
        "flags": trip.flags or [],
        "flight_options": trip.flight_options or [],
        "status": trip.status,
        "flights": trip.flights or [],
        "hotels": trip.hotels or [],
        "source_event_ids": trip.source_event_ids or [],
        "days": [
            {
                "date": d.day_date.isoformat(),
                "start_time": d.start_time,
                "end_time": d.end_time,
                "destination": d.destination,
                "meetings": d.meetings or [],
                "hotel": d.hotel,
                "source_event_ids": d.source_event_ids or [],
            }
            for d in days
        ],
        "receipts": _linked_receipts(db, trip.start_date, trip.end_date),
    }


def list_trips(db: Session) -> list[dict]:
    rows = (
        db.query(CalendarTrip)
        .order_by(CalendarTrip.start_date.desc())
        .all()
    )
    return [serialize_trip(db, t) for t in rows]
