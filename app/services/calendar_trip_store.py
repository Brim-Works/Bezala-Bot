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


def _flight_keys(flights) -> set[str]:
    out = set()
    for f in flights or []:
        key = f.get("id") or "|".join(
            str(f.get(k) or "") for k in ("flight_number", "start", "origin", "destination")
        )
        if key.strip("|"):
            out.add(str(key))
    return out


def same_trip(existing: CalendarTrip, detected: dict) -> bool:
    """C46 — samma datum + samma flyg/händelser = samma resa."""
    if existing.start_date != _d(detected["start_date"]) or existing.end_date != _d(detected["end_date"]):
        return False
    old_f, new_f = _flight_keys(existing.flights), _flight_keys(detected.get("flights"))
    if old_f or new_f:
        if old_f & new_f:
            return True
    old_e = set(existing.source_event_ids or [])
    new_e = set(detected.get("source_event_ids") or [])
    if old_e or new_e:
        return bool(old_e & new_e)
    return not (old_f or new_f)


def persist_detected(db: Session, detected: dict) -> dict:
    """Ersätt alla 'suggested'-resor med nya förslag.

    Förslag som överlappar en redigerad/godkänd resa hoppas över så att
    användarens ändringar (R2) aldrig skrivs över.
    """
    from app.services.trip_completeness import (
        delete_for_trips, restore_user_marks, snapshot_user_marks,
    )

    old = db.query(CalendarTrip).filter(CalendarTrip.status == "suggested").all()
    old_ids = [t.id for t in old]
    # C42 — Mikkos markeringar (t.ex. not_applicable) följer med till de
    # nya förslagen (nya id:n efter refresh).
    user_marks = snapshot_user_marks(db, old_ids)
    delete_for_trips(db, old_ids)
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

    # C46 — avvisade resor rörs aldrig och samma resa återskapas aldrig.
    rejected = (
        db.query(CalendarTrip).filter(CalendarTrip.status == "rejected").all()
    )

    created = 0
    skipped_locked = 0
    skipped_rejected = 0
    new_rows: list[CalendarTrip] = []
    for lt in locked:
        # C44 — förslag räknas om varje refresh (skrivs aldrig in i resan).
        sugg = dict(lt.suggestions or {})
        sugg.pop("free_meals", None)
        sugg.pop("flights", None)
        lt.suggestions = sugg or None
    for t in detected.get("trips", []):
        start, end = _d(t["start_date"]), _d(t["end_date"])
        if any(same_trip(rt, t) for rt in rejected):
            skipped_rejected += 1
            continue
        hit = [lt for lt in locked if lt.start_date <= end and lt.end_date >= start]
        if hit:
            skipped_locked += 1
            _store_locked_suggestions(hit, t)
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
            purpose=t.get("purpose"),
            travel_start_at=t.get("travel_start_at"),
            travel_end_at=t.get("travel_end_at"),
            time_source="flight_default" if t.get("travel_start_at") else None,
        )
        db.add(row)
        db.flush()
        new_rows.append(row)
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
                country=day.get("country"),
                purpose=day.get("purpose"),
                airport=day.get("airport"),
                destination_source=day.get("destination_source"),
                free_meals=day.get("free_meals"),
                free_meal_sources=day.get("free_meal_sources"),
                meals_source=day.get("meals_source"),
            ))
        created += 1

    restored = restore_user_marks(db, user_marks, new_rows)
    db.commit()
    logger.info(
        "Calendar trips: ersatte %d förslag med %d nya (%d krockade med låsta)",
        len(old_ids), created, skipped_locked,
    )
    return {
        "replaced": len(old_ids),
        "created": created,
        "skipped_locked": skipped_locked,
        "skipped_rejected": skipped_rejected,
        "restored_marks": restored,
    }


def _store_locked_suggestions(locked: list[CalendarTrip], detected: dict) -> None:
    """C44 — för låsta resor (edited/approved): spara detektorns måltider och
    ombokade flyg som FÖRSLAG. Resan själv ändras inte."""
    meals = {
        d["date"]: {
            "free_meals": d.get("free_meals"),
            "free_meal_sources": d.get("free_meal_sources") or [],
        }
        for d in detected.get("days", [])
    }
    rebooked = [f for f in detected.get("flights", []) if f.get("rebooked_from")]
    for lt in locked:
        sugg = dict(lt.suggestions or {})
        own = {k: v for k, v in meals.items()
               if lt.start_date.isoformat() <= k <= lt.end_date.isoformat()}
        if own:
            sugg["free_meals"] = {**(sugg.get("free_meals") or {}), **own}
        if rebooked:
            sugg["flights"] = detected.get("flights")
        lt.suggestions = sugg or None


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


def _flight_times_by_day(flights: list[dict]) -> dict[str, dict]:
    """{'YYYY-MM-DD': {'departure': 'HH:MM', 'arrival': 'HH:MM'}} i
    Helsingfors-tid — flygtiderna visas i mindre text i UI."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    hel = ZoneInfo("Europe/Helsinki")
    out: dict[str, dict] = {}
    for f in flights or []:
        for key, field in (("departure", "start"), ("arrival", "end")):
            raw = f.get(field)
            if not raw:
                continue
            try:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt.tzinfo is not None:
                dt = dt.astimezone(hel)
            slot = out.setdefault(dt.date().isoformat(), {})
            # Första avgången / sista ankomsten den dagen.
            if key == "departure":
                slot.setdefault(key, dt.strftime("%H:%M"))
            else:
                slot[key] = dt.strftime("%H:%M")
    return out


def serialize_trip(db: Session, trip: CalendarTrip, checklist: dict | None = None) -> dict:
    from app.services.trip_english import country_name

    flight_times = _flight_times_by_day(trip.flights or [])
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
        "country_name": country_name(trip.country),
        "purpose": trip.purpose,
        "travel_start_at": trip.travel_start_at,
        "travel_end_at": trip.travel_end_at,
        "time_source": trip.time_source,
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
                "country": d.country,
                "country_name": country_name(d.country),
                "airport": d.airport,
                "destination_source": d.destination_source,
                "flight_departure": flight_times.get(d.day_date.isoformat(), {}).get("departure"),
                "flight_arrival": flight_times.get(d.day_date.isoformat(), {}).get("arrival"),
                "purpose": d.purpose,
                "free_meals": d.free_meals,
                "free_meal_sources": d.free_meal_sources or [],
                "source_event_ids": d.source_event_ids or [],
            }
            for d in days
        ],
        "receipts": _linked_receipts(db, trip.start_date, trip.end_date),
        # C42 — checklista (read-only i UI): flyg/hotell/parkering/lokal.
        "checklist": checklist,
        "completeness": (checklist or {}).get("completeness"),
    }


def list_trips(db: Session) -> list[dict]:
    from app.services.trip_completeness import checklists_for

    rows = (
        db.query(CalendarTrip)
        .order_by(CalendarTrip.start_date.desc())
        .all()
    )
    try:
        lists = checklists_for(db, rows)
    except Exception:  # noqa: BLE001 — checklistan får aldrig fälla resvyn
        db.rollback()
        logger.exception("C42: checklistor kunde inte räknas")
        lists = {}
    return [serialize_trip(db, t, lists.get(t.id)) for t in rows]


def refresh_from_calendar(db: Session) -> dict:
    """Läs Google Calendar (read-only), kör detektorn och spara förslagen.

    Resor med status 'edited'/'approved' rörs aldrig (persist_detected).
    Returnerar {'status': 'ok'|'consent_required'|'not_configured'|
    'api_disabled'|'error', ...}.
    """
    from app.services.calendar_client import CalendarAuthError, fetch_events
    from app.services.trip_detector import detect_trips

    try:
        events = fetch_events()
    except CalendarAuthError as exc:
        logger.warning("Calendar refresh: %s (%s)", exc.reason, exc)
        return {"status": exc.reason, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Calendar refresh misslyckades")
        return {"status": "error", "detail": str(exc)}

    from app.services import receipt_details as rd
    from app.services.trip_parking import apply_parking_times

    try:
        details = rd.enrich_receipts(db)
        dupes = rd.mark_duplicates(db)
    except Exception:  # noqa: BLE001 — detaljerna får aldrig fälla refresh
        db.rollback()
        logger.exception("C44: kvittodetaljer/dubbletter misslyckades")
        details, dupes = {"status": "error"}, {}
    detected = detect_trips(
        events, receipts_by_date=receipt_texts_by_date(db),
        rebookings=rebooking_segments(db),
    )
    saved = persist_detected(db, detected)
    try:
        saved["parking_times"] = apply_parking_times(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("C44: parkeringstider misslyckades")
    saved["receipt_details"] = details
    saved["duplicates"] = dupes
    logger.info(
        "Calendar refresh OK: stats=%s saved=%s ungrouped=%s",
        detected["stats"], saved,
        [(u["date"], u["summary"], u["reason"]) for u in detected["ungrouped"]],
    )
    return {
        "status": "ok",
        "stats": detected["stats"],
        "saved": saved,
        "ungrouped": detected["ungrouped"],
    }


# ---------- C44 ----------


def rebooking_segments(db: Session) -> list[dict]:
    """Flygsegment ur Finnairs ombokningsmail ("Bekräftelse på din
    bokningsändring" / change_fee), nyaste först."""
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.flight_segments.isnot(None))
        .order_by(ProcessedMessage.received_at.desc())
        .all()
    )
    out: list[dict] = []
    for m in rows:
        subj = (m.subject or "").lower()
        if m.kind != "change_fee" and not any(
            w in subj for w in ("bokningsändring", "booking change", "varauksen muutos")
        ):
            continue
        for seg in m.flight_segments or []:
            out.append({**seg, "message_id": m.message_id})
    return out


# ---------- C41 ----------


def receipt_texts_by_date(db: Session) -> dict[str, list[str]]:
    """Kvitton i boten per datum (text att leta ortnamn i): leverantör,
    ämne, sammanfattning, filnamn. Sista ledtråden för arbetsort."""
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None),
                ProcessedMessage.receipt_date.isnot(None))
        .all()
    )
    out: dict[str, list[str]] = {}
    for m in rows:
        key = str(m.receipt_date or "")[:10]
        if len(key) != 10:
            continue
        text = " ".join(
            str(v) for v in (m.vendor, m.subject, m.summary, m.file_name,
                             getattr(m, "ai_description_en", None)) if v
        )
        if text:
            out.setdefault(key, []).append(text)
    return out


def migrate_c41(db: Session) -> dict:
    """Idempotent, körs vid uppstart.

    * Svenska stadsnamn → engelska (Köpenhamn → Copenhagen) på alla rader.
      Syftet rörs aldrig här.
    * 'suggested'-resor utan resetider får resetider beräknade ur
      flygtiderna (marginal på hemänden). edited/approved räknas aldrig om.
    * Rapporterar (loggar) edited/approved-resor vars syfte inte ser
      engelskt ut — de ändras inte automatiskt.
    """
    from datetime import datetime as _dt

    from app.services.trip_detector import home_iatas, travel_times
    from app.services.trip_english import destination_to_english, non_english_warnings

    renamed = recalculated = 0
    non_english: list[dict] = []
    homes = home_iatas()
    for trip in db.query(CalendarTrip).all():
        new = destination_to_english(trip.destination)
        if new != trip.destination:
            trip.destination = new
            renamed += 1
        days = (
            db.query(CalendarTripDay)
            .filter(CalendarTripDay.trip_id == trip.id)
            .order_by(CalendarTripDay.day_date)
            .all()
        )
        for d in days:
            new = destination_to_english(d.destination)
            if new != d.destination:
                d.destination = new
                renamed += 1
        if trip.status in LOCKED_STATUSES:
            texts = [trip.purpose] + [d.purpose for d in days]
            if any(non_english_warnings("purpose", t) for t in texts if t):
                non_english.append({
                    "id": trip.id, "status": trip.status,
                    "start_date": trip.start_date.isoformat(),
                    "purpose": trip.purpose,
                })
            continue
        if trip.status != "suggested" or trip.travel_start_at or not trip.start_at:
            continue
        flights = trip.flights or []
        origin = flights[0].get("origin") if flights else None

        def _p(v):
            return _dt.fromisoformat(v.replace("Z", "+00:00")) if v else None

        start, end = travel_times(_p(trip.start_at), origin, _p(trip.end_at), homes)
        trip.travel_start_at = start.isoformat() if start else None
        trip.travel_end_at = end.isoformat() if end else None
        if days and start is not None:
            days[0].start_time = start.strftime("%H:%M")
        if days and end is not None:
            days[-1].end_time = end.strftime("%H:%M")
        recalculated += 1
    db.commit()
    if non_english:
        logger.warning("C41: låsta resor med syfte som inte ser engelskt ut: %s", non_english)
    logger.info("C41-migrering: %d namn → engelska, %d resor fick resetider",
                renamed, recalculated)
    return {"renamed": renamed, "recalculated": recalculated,
            "locked_non_english_purpose": non_english}
