"""C40 — granska, redigera och godkänna kalenderresor (används av MCP).

Hårdregler:
  * Skriver ENDAST till `calendar_trips` / `calendar_trip_days`.
  * Inget skickas till Bezala eller Netvisor.
  * `approve_trip` / `unapprove_trip` byter bara status.
  * Varje redigering sätter status 'edited' (så att refresh aldrig skriver
    över den — se calendar_trip_store.LOCKED_STATUSES).

Alla fel som beror på indata kastas som `TripEditError` med en kort,
tydlig engelsk text (den visas för Claude/Mikko i chatten).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import CalendarTrip, CalendarTripDay
from app.services.calendar_trip_store import _flight_times_by_day, receipt_totals, trip_receipts
from app.services.trip_meals import meals_included
from app.services.trip_english import (
    country_name,
    meals_note,
    non_english_warnings,
)

HELSINKI = ZoneInfo("Europe/Helsinki")
STATUSES = ("suggested", "edited", "approved", "rejected")
FLAG_NEEDS_REVIEW = "needs_review"
FLAG_MISSING_RETURN = "missing_return"
FLAG_DESTINATION_UNCONFIRMED = "destination_unconfirmed"
MAX_TEXT = 500

# ISO 3166-1 alpha-2
COUNTRY_CODES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL
BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV
CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD
GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM
IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK
LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW
MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR
PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS
ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY
UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())

LATE_RETURN_HOUR = 6  # hemma före 06:00 = hemkomsten hör till dagen före

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class TripEditError(ValueError):
    """Ogiltig indata eller okänd resa — texten visas för användaren."""


# ---------- Validering / parsning ----------


def parse_country(value: str | None) -> str | None:
    """'dk' → 'DK', 'DK, NO' → 'DK,NO'. Tom sträng → None (rensa)."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    codes = [c.strip().upper() for c in raw.split(",") if c.strip()]
    bad = [c for c in codes if c not in COUNTRY_CODES]
    if bad:
        raise TripEditError(
            f"Unknown country code: {', '.join(bad)}. "
            "Use ISO 3166-1 alpha-2 codes, e.g. 'DK' or 'DK,NO'."
        )
    out: list[str] = []
    for c in codes:
        if c not in out:
            out.append(c)
    return ",".join(out)


def parse_hhmm(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if not _HHMM_RE.match(raw):
        raise TripEditError(f"{field} must be HH:MM (24h, Helsinki time), got '{value}'.")
    return raw


def parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        raise TripEditError(f"{field} must be a date YYYY-MM-DD, got '{value}'.")


def parse_moment(value: str, field: str) -> datetime:
    """ISO-datum/tid → aware datetime. Utan tidszon = Helsingfors-tid."""
    raw = (value or "").strip()
    if not raw:
        raise TripEditError(f"{field} is empty.")
    try:
        if len(raw) == 10:
            raise TripEditError(
                f"{field} needs a time, e.g. '{raw}T07:25' (Helsinki time)."
            )
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except TripEditError:
        raise
    except ValueError:
        raise TripEditError(
            f"{field} must be ISO 8601, e.g. '2026-08-25T07:25' (Helsinki) "
            f"or '2026-08-25T04:25Z', got '{value}'."
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=HELSINKI)
    return dt


def clean_text(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    raw = value.strip()
    if len(raw) > MAX_TEXT:
        raise TripEditError(f"{field} is too long (max {MAX_TEXT} characters).")
    return raw or None


def _aware(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=HELSINKI)
    return dt


def _times(value: str | None) -> dict | None:
    dt = _aware(value)
    if dt is None:
        return None
    return {
        "utc": dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "helsinki": dt.astimezone(HELSINKI).strftime("%Y-%m-%d %H:%M"),
    }


def _hel_to_utc(d: date, hhmm: str | None) -> str | None:
    if not hhmm:
        return None
    h, m = (int(x) for x in hhmm.split(":"))
    dt = datetime.combine(d, time(h, m), tzinfo=HELSINKI)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# ---------- Läsning ----------


def _get(db: Session, trip_id: int) -> CalendarTrip:
    trip = db.get(CalendarTrip, trip_id)
    if trip is None:
        raise TripEditError(
            f"Trip {trip_id} not found. Call list_trips to see current ids "
            "(suggested trips get new ids after refresh_calendar)."
        )
    return trip


def _days(db: Session, trip: CalendarTrip) -> list[CalendarTripDay]:
    return (
        db.query(CalendarTripDay)
        .filter(CalendarTripDay.trip_id == trip.id)
        .order_by(CalendarTripDay.day_date)
        .all()
    )


def _hel(value: str | None) -> str | None:
    t = _times(value)
    return t["helsinki"] if t else None


def summary(trip: CalendarTrip) -> dict:
    """Kort rad för list_trips. travel_* = resetider (hemifrån → hem, det
    som går till Netvisor); flight_* = flygtiderna (oförändrade)."""
    return {
        "id": trip.id,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "destination": trip.destination,
        "country": trip.country,
        "country_name": country_name(trip.country),
        "purpose": trip.purpose,
        "status": trip.status,
        "rejected_reason": getattr(trip, "rejected_reason", None),
        "flags": list(trip.flags or []),
        "travel_start_helsinki": _hel(trip.travel_start_at),
        "travel_end_helsinki": _hel(trip.travel_end_at),
        "time_source": _time_source(trip),
        "flight_departure_helsinki": _hel(trip.start_at),
        "flight_arrival_helsinki": _hel(trip.end_at),
    }


def _time_source(trip: CalendarTrip) -> str | None:
    from app.services.trip_parking import time_source

    return time_source(trip)


def list_trips(
    db: Session,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    flagged_only: bool = False,
    include_rejected: bool = False,
) -> list[dict]:
    q = db.query(CalendarTrip)
    if not include_rejected and status != "rejected":
        # C46 — avvisade resor döljs som standard.
        q = q.filter(CalendarTrip.status != "rejected")
    if status:
        if status not in STATUSES:
            raise TripEditError(
                f"Unknown status '{status}'. Use one of: {', '.join(STATUSES)}."
            )
        q = q.filter(CalendarTrip.status == status)
    if date_from:
        q = q.filter(CalendarTrip.end_date >= parse_date(date_from, "from"))
    if date_to:
        q = q.filter(CalendarTrip.start_date <= parse_date(date_to, "to"))
    rows = q.order_by(CalendarTrip.start_date).all()
    from app.services.trip_completeness import checklists_for

    from app.services.c56_receipt_allocation import allocate, totals

    lists = checklists_for(db, rows)
    allocation = allocate(db)
    out = []
    for t in rows:
        row = summary(t)
        row["completeness"] = lists[t.id]["completeness"]
        # C56: belopp per valuta (varje kvitto på EN resa, samma biljett en gång).
        row["receipt_totals"] = totals(allocation["by_trip"].get(t.id, []))
        cb = allocation["cancelled_by_trip"].get(t.id, [])
        if cb:
            row["cancelled_not_counted"] = [
                {k: b[k] for k in ("booking_ref", "vendor", "amount", "currency")} for b in cb]
        day_rows = _days(db, t)
        row["free_meals"] = sum(d.free_meals or 0 for d in day_rows)
        out.append(row)
    if flagged_only:
        out = [t for t in out if t["flags"]]
    return out


def _netvisor_text(trip: CalendarTrip, day_rows: list) -> dict:
    from app.services.netvisor_entry import travel_description_info

    info = travel_description_info(trip, day_rows)
    return {
        "netvisor_line_description": info["text"],
        "netvisor_description": info["text"],
        "netvisor_description_flags": info["flags"],
        "netvisor_description_warnings": info["warnings"],
    }


def trip_detail(db: Session, trip: CalendarTrip) -> dict:
    trip_country = trip.country if trip.country and "," not in trip.country else None
    flight_times = _flight_times_by_day(trip.flights or [])
    days = []
    day_rows = _days(db, trip)
    for d in day_rows:
        ft = flight_times.get(d.day_date.isoformat(), {})
        days.append({
            "date": d.day_date.isoformat(),
            "start_time_helsinki": d.start_time,
            "start_time_utc": _hel_to_utc(d.day_date, d.start_time),
            "end_time_helsinki": d.end_time,
            "end_time_utc": _hel_to_utc(d.day_date, d.end_time),
            "flight_departure_helsinki": ft.get("departure"),
            "flight_arrival_helsinki": ft.get("arrival"),
            "airport": d.airport,
            "destination": d.destination,
            "destination_source": d.destination_source,
            "country": d.country or trip_country,
            "country_name": country_name(d.country or trip_country),
            "purpose": d.purpose,
            "free_meals": d.free_meals,
            "free_meal_sources": d.free_meal_sources or [],
            "free_meal_note": d.free_meal_note,
            "meals_source": d.meals_source,
            # C46 — Netvisor-förslaget för dagen: "Inklusive kost" vid 2
            # fria måltider (lunch + middag).
            "netvisor": {
                "meals_included": meals_included(d.free_meals),
                "label": "Inklusive kost" if meals_included(d.free_meals) else None,
            },
            "hotel": (d.hotel or {}).get("name") if d.hotel else None,
            "meetings": [
                {
                    "summary": m.get("summary"),
                    "start": m.get("start_time"),
                    "end": m.get("end_time"),
                    "location": m.get("location"),
                }
                for m in (d.meetings or [])
            ],
        })
    return {
        **summary(trip),
        # Resetider (det som går till Netvisor) och flygtider.
        "travel_start": _times(trip.travel_start_at),
        "travel_end": _times(trip.travel_end_at),
        "flight_departure": _times(trip.start_at),
        "flight_arrival": _times(trip.end_at),
        # C55 - Netvisor-texten (beskrivning = linedescription), utan måltider.
        **_netvisor_text(trip, day_rows),
        # Måltider bara i botens egen vy - exporteras aldrig till Netvisor.
        "meals_note": meals_note(
            (d.day_date.isoformat(), d.free_meals, d.free_meal_sources,
             d.country or trip_country or trip.country)
            for d in day_rows
        ),
        # C44 — förslag som inte skrivs in automatiskt (låsta resor):
        # resetider ur parkeringen, måltider ur kalendern, ombokade flyg.
        "suggestions": trip.suggestions or None,
        "open_return": bool(trip.open_return),
        "flights": [
            {
                "id": f.get("id"),
                "flight_no": f.get("flight_no"),
                "summary": f.get("summary"),
                "from": f.get("origin"),
                "to": f.get("destination_iata") or f.get("destination"),
                "departure": _times(f.get("start")),
                "arrival": _times(f.get("end")),
                **({"rebooked_from": f.get("rebooked_from")} if f.get("rebooked_from") else {}),
            }
            for f in (trip.flights or [])
        ],
        "flight_options": [
            {
                "flight_id": f.get("id"),
                "flight_no": f.get("flight_no"),
                "summary": f.get("summary"),
                "departure": _times(f.get("start")),
                "arrival": _times(f.get("end")),
            }
            for f in (trip.flight_options or [])
        ],
        "days": days,
        "receipts": (receipts := trip_receipts(db, trip, allocation := _allocate(db))),
        "receipt_totals": receipt_totals(receipts),
        # C57 - avbokade och återbetalda bokningar (räknas inte).
        "cancelled_bookings": allocation["cancelled_by_trip"].get(trip.id, []),
        "edited_at": trip.edited_at.isoformat() if trip.edited_at else None,
        "approved_at": trip.approved_at.isoformat() if trip.approved_at else None,
    }


def _allocate(db: Session) -> dict:
    from app.services.c56_receipt_allocation import allocate

    return allocate(db)


def get_trip(db: Session, trip_id: int) -> dict:
    return trip_detail(db, _get(db, trip_id))


# ---------- Skrivning ----------


def _mark_edited(trip: CalendarTrip) -> None:
    if trip.status == "rejected":
        # C46 — en avvisad resa förblir avvisad tills unreject_trip.
        trip.edited_at = datetime.utcnow()
        return
    trip.status = "edited"
    trip.edited_at = datetime.utcnow()
    trip.approved_at = None


def _sync_days(db: Session, trip: CalendarTrip, old_start: date, old_end: date) -> None:
    """Efter ändrat datumintervall: ta bort dagar utanför det nya
    intervallet och lägg till tomma dagar för datum som tillkommit (dagar
    som Mikko tagit bort mitt i resan återskapas inte)."""
    existing = {d.day_date: d for d in _days(db, trip)}
    for day_date, row in existing.items():
        if day_date < trip.start_date or day_date > trip.end_date:
            db.delete(row)
    single_dest = (
        trip.destination if trip.destination and "→" not in trip.destination else None
    )
    d = trip.start_date
    while d <= trip.end_date:
        if d not in existing and (d < old_start or d > old_end):
            db.add(CalendarTripDay(
                trip_id=trip.id, day_date=d, status="edited",
                meetings=[], source_event_ids=[], destination=single_dest,
            ))
        d += timedelta(days=1)
    db.flush()


def _day(db: Session, trip: CalendarTrip, day_date: date) -> CalendarTripDay | None:
    return (
        db.query(CalendarTripDay)
        .filter(
            CalendarTripDay.trip_id == trip.id,
            CalendarTripDay.day_date == day_date,
        )
        .first()
    )


def _warnings(**fields) -> list[str]:
    out: list[str] = []
    for name, value in fields.items():
        if value:
            out += non_english_warnings(name, value)
    return out


def _with_warnings(detail: dict, warnings: list[str]) -> dict:
    if warnings:
        detail["warnings"] = warnings
    return detail


def update_trip(
    db: Session,
    trip_id: int,
    start: str | None = None,
    end: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    purpose: str | None = None,
    selected_flight_id: str | None = None,
) -> dict:
    """start/end = resetiderna (när Mikko lämnar hemmet/kontoret och är
    tillbaka). Flygtiderna (start_at/end_at) ändras bara via
    selected_flight_id."""
    from app.services.trip_detector import home_iatas, travel_times

    trip = _get(db, trip_id)
    if all(v is None for v in (start, end, destination, country, purpose, selected_flight_id)):
        raise TripEditError("Nothing to update — pass at least one field.")

    travel_start = _aware(trip.travel_start_at) or _aware(trip.start_at)
    travel_end = _aware(trip.travel_end_at) or _aware(trip.end_at)
    flags = list(trip.flags or [])
    flights = list(trip.flights or [])

    if selected_flight_id is not None:
        options = trip.flight_options or []
        chosen = next((f for f in options if f.get("id") == selected_flight_id), None)
        if chosen is None:
            ids = ", ".join(str(f.get("id")) for f in options) or "none"
            raise TripEditError(
                f"Flight '{selected_flight_id}' is not an option for trip {trip.id}. "
                f"Options: {ids}."
            )
        others = {f.get("id") for f in options if f.get("id") != selected_flight_id}
        flights = [f for f in flights if f.get("id") not in others]
        if not any(f.get("id") == selected_flight_id for f in flights):
            flights.insert(0, chosen)
        chosen_start = _aware(chosen.get("start"))
        if chosen_start is not None:
            trip.start_at = chosen_start.isoformat()
            # Resans start räknas om från det valda flyget (med marginal).
            travel_start, _ = travel_times(
                chosen_start, chosen.get("origin"), None, home_iatas(),
            )
        flags = [f for f in flags if f != FLAG_NEEDS_REVIEW]

    if start is not None:
        travel_start = parse_moment(start, "start")
    if end is not None:
        travel_end = parse_moment(end, "end")
    if travel_start is not None and travel_end is not None and travel_end <= travel_start:
        raise TripEditError("end must be after start.")

    new_start_date = (
        travel_start.astimezone(HELSINKI).date() if travel_start else trip.start_date
    )
    new_end_date = travel_end.astimezone(HELSINKI).date() if travel_end else trip.end_date
    if new_end_date < new_start_date:
        raise TripEditError("end must be after start.")

    warnings = _warnings(destination=destination, purpose=purpose)
    if destination is not None:
        trip.destination = clean_text(destination, "destination")
    if country is not None:
        trip.country = parse_country(country)
    if purpose is not None:
        trip.purpose = clean_text(purpose, "purpose")

    old_start, old_end = trip.start_date, trip.end_date
    trip.travel_start_at = travel_start.isoformat() if travel_start else None
    trip.travel_end_at = travel_end.isoformat() if travel_end else None
    trip.start_date = new_start_date
    trip.end_date = new_end_date
    if end is not None:
        flags = [f for f in flags if f != FLAG_MISSING_RETURN]
        trip.open_return = False
    if destination is not None and trip.destination:
        flags = [f for f in flags if f != FLAG_DESTINATION_UNCONFIRMED]
    if (destination is not None or country is not None) and "domestic_unconfirmed" in flags:
        flags = [f for f in flags if f != "domestic_unconfirmed"]
    trip.flags = flags
    trip.flights = flights
    _mark_edited(trip)
    db.flush()
    _sync_days(db, trip, old_start, old_end)
    # C44 — dagar som inte satts manuellt följer resans ort/syfte.
    _propagate_to_days(db, trip, destination, purpose)
    if start is not None or end is not None:
        trip.time_source = "manual"
    # Första/sista dagens tider följer resans start/slut (resetider).
    if travel_start is not None and (start is not None or selected_flight_id is not None):
        first = _day(db, trip, trip.start_date)
        if first is not None:
            first.start_time = travel_start.astimezone(HELSINKI).strftime("%H:%M")
    if travel_end is not None and end is not None:
        last = _day(db, trip, trip.end_date)
        if last is not None:
            last.end_time = travel_end.astimezone(HELSINKI).strftime("%H:%M")

    db.commit()
    return _with_warnings(trip_detail(db, trip), warnings)


def _propagate_to_days(db: Session, trip: CalendarTrip, destination: str | None,
                       purpose: str | None) -> None:
    """C44 — update_trip(destination/purpose) uppdaterar dagar som inte satts
    manuellt (destination_source/purpose_source != 'manual'). En ort med
    '→' (flera orter) skrivs inte ut på dagarna."""
    if destination is None and purpose is None:
        return
    single = trip.destination if trip.destination and "→" not in trip.destination else None
    for d in _days(db, trip):
        if destination is not None and single and d.destination_source != "manual":
            d.destination = single
            d.destination_source = "trip"
            if trip.country and "," not in trip.country:
                d.country = trip.country
        if purpose is not None and d.purpose_source != "manual":
            d.purpose = trip.purpose
    db.flush()


def update_trip_day(
    db: Session,
    trip_id: int,
    day: str,
    start_time: str | None = None,
    end_time: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    hotel: str | None = None,
    purpose: str | None = None,
    free_meals: int | None = None,
    free_meal_sources: list[str] | None = None,
    free_meal_note: str | None = None,
) -> dict:
    trip = _get(db, trip_id)
    day_date = parse_date(day, "date")
    row = _day(db, trip, day_date)
    if row is None:
        raise TripEditError(
            f"Trip {trip.id} has no day {day_date.isoformat()}. Use add_trip_day."
        )
    if all(v is None for v in (start_time, end_time, destination, country, hotel, purpose,
                               free_meals, free_meal_sources, free_meal_note)):
        raise TripEditError("Nothing to update — pass at least one field.")
    _apply_day_fields(row, start_time, end_time, destination, country, hotel, purpose)
    _apply_meals(row, trip, free_meals, free_meal_sources, free_meal_note)
    _after_day_edit(db, trip)
    _mark_edited(trip)
    db.commit()
    return _with_warnings(
        trip_detail(db, trip), _warnings(destination=destination, purpose=purpose),
    )


def _after_day_edit(db: Session, trip: CalendarTrip) -> None:
    """Flaggan destination_unconfirmed försvinner när varje dag har en ort.
    Resans destination/land följer dagarnas orter."""
    db.flush()
    rows = _days(db, trip)
    if rows and all(r.destination for r in rows):
        trip.flags = [f for f in (trip.flags or []) if f != FLAG_DESTINATION_UNCONFIRMED]
        cities: list[str] = []
        countries: list[str] = []
        for r in rows:
            if r.destination not in cities:
                cities.append(r.destination)
            if r.country and r.country not in countries:
                countries.append(r.country)
        trip.destination = " → ".join(cities)
        if countries:
            trip.country = ",".join(countries)


def _apply_meals(row, trip, free_meals, free_meal_sources, free_meal_note=None) -> None:
    """C44/C46 — Mikkos måltider (fungerar även på edited-resor). Bara
    källor → antalet räknas ur källorna; bara antal → källorna behålls.
    free_meals 0–2 (lunch och middag, frukost räknas aldrig)."""
    from app.services.trip_meals import MAX_FREE_MEALS, count_meals, validate_sources

    if free_meals is None and free_meal_sources is None and free_meal_note is None:
        return
    if free_meal_note is not None:
        row.free_meal_note = clean_text(free_meal_note, "free_meal_note") or None
    if free_meal_sources is not None:
        if isinstance(free_meal_sources, str):
            free_meal_sources = [p for p in re.split(r"[,\s]+", free_meal_sources) if p]
        try:
            row.free_meal_sources = validate_sources(free_meal_sources)
        except ValueError as exc:
            raise TripEditError(str(exc)) from exc
    if free_meals is not None:
        try:
            n = int(free_meals)
        except (TypeError, ValueError):
            raise TripEditError("free_meals must be 0-2 (lunch and dinner).")
        if not 0 <= n <= MAX_FREE_MEALS:
            raise TripEditError("free_meals must be 0-2 (lunch and dinner; breakfast never counts).")
        row.free_meals = n
    elif free_meal_sources is not None:
        row.free_meals = count_meals(row.free_meal_sources or [], row.country or trip.country)
    row.meals_source = "manual"


def _apply_day_fields(row, start_time, end_time, destination, country, hotel, purpose):
    new_start = row.start_time if start_time is None else parse_hhmm(start_time, "start_time")
    new_end = row.end_time if end_time is None else parse_hhmm(end_time, "end_time")
    if new_start and new_end and new_end <= new_start:
        raise TripEditError("end_time must be after start_time on the same day.")
    new_country = row.country if country is None else parse_country(country)
    new_dest = row.destination if destination is None else clean_text(destination, "destination")
    new_purpose = row.purpose if purpose is None else clean_text(purpose, "purpose")
    new_hotel = row.hotel
    if hotel is not None:
        name = clean_text(hotel, "hotel")
        new_hotel = {"id": None, "name": name} if name else None
    row.start_time, row.end_time = new_start, new_end
    if destination is not None and new_dest != row.destination:
        row.destination_source = "manual"
    if purpose is not None:
        row.purpose_source = "manual"
    row.country, row.destination, row.purpose = new_country, new_dest, new_purpose
    row.hotel = new_hotel
    row.status = "edited"


def add_trip_day(
    db: Session,
    trip_id: int,
    day: str,
    start_time: str | None = None,
    end_time: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    hotel: str | None = None,
    purpose: str | None = None,
) -> dict:
    trip = _get(db, trip_id)
    day_date = parse_date(day, "date")
    if _day(db, trip, day_date) is not None:
        raise TripEditError(
            f"Trip {trip.id} already has {day_date.isoformat()}. Use update_trip_day."
        )
    if day_date < trip.start_date - timedelta(days=31) or day_date > trip.end_date + timedelta(days=31):
        raise TripEditError("Day is more than 31 days outside the trip.")
    row = CalendarTripDay(
        trip_id=trip.id, day_date=day_date, meetings=[], source_event_ids=[],
        status="edited",
    )
    _apply_day_fields(row, start_time, end_time, destination, country, hotel, purpose)
    db.add(row)
    warnings = _warnings(destination=destination, purpose=purpose)
    if day_date < trip.start_date:
        trip.start_date = day_date
        trip.start_at = None
        trip.travel_start_at = None
    if day_date > trip.end_date:
        trip.end_date = day_date
        trip.end_at = None
        trip.travel_end_at = None
    _after_day_edit(db, trip)
    _mark_edited(trip)
    db.commit()
    return _with_warnings(trip_detail(db, trip), warnings)


def remove_trip_day(db: Session, trip_id: int, day: str) -> dict:
    trip = _get(db, trip_id)
    day_date = parse_date(day, "date")
    row = _day(db, trip, day_date)
    if row is None:
        raise TripEditError(f"Trip {trip.id} has no day {day_date.isoformat()}.")
    remaining = [d.day_date for d in _days(db, trip) if d.day_date != day_date]
    if not remaining:
        raise TripEditError("Cannot remove the only day of a trip.")
    db.delete(row)
    if day_date == trip.start_date:
        trip.start_date = min(remaining)
        trip.start_at = None
        trip.travel_start_at = None
    if day_date == trip.end_date:
        new_end = max(remaining)
        trip.end_date = new_end
        # C44 — hemkomst efter midnatt (13 okt: hemma 00:17 den 14:e) hör
        # till dagen före. Tar man bort den sista "dagen" ligger sluttiden
        # kvar — den nollställs bara när hemkomsten verkligen var den dagen.
        home = _aware(trip.travel_end_at)
        home_local = home.astimezone(HELSINKI) if home else None
        after_midnight = (
            home_local is not None
            and home_local.date() == day_date
            and new_end == day_date - timedelta(days=1)
            and home_local.hour < LATE_RETURN_HOUR
        )
        if not after_midnight:
            trip.end_at = None
            trip.travel_end_at = None
    _mark_edited(trip)
    db.commit()
    return trip_detail(db, trip)


def approve_trip(db: Session, trip_id: int) -> dict:
    """Bara statusbyte. Inget skickas någonstans."""
    trip = _get(db, trip_id)
    trip.status = "approved"
    trip.approved_at = datetime.utcnow()
    db.commit()
    return trip_detail(db, trip)


def unapprove_trip(db: Session, trip_id: int) -> dict:
    """Bara statusbyte: tillbaka till 'edited' (redigerad) eller 'suggested'."""
    trip = _get(db, trip_id)
    if trip.status != "approved":
        raise TripEditError(f"Trip {trip.id} is not approved (status: {trip.status}).")
    trip.status = "edited" if trip.edited_at else "suggested"
    trip.approved_at = None
    db.commit()
    return trip_detail(db, trip)


def reject_trip(db: Session, trip_id: int, reason: str | None = None) -> dict:
    """C46 — Mikko avvisar resan (t.ex. privat resa eller felupptäckt).
    Bara statusbyte. refresh_calendar återskapar den aldrig, list_trips
    döljer den och list_missing ignorerar den."""
    trip = _get(db, trip_id)
    trip.status = "rejected"
    trip.rejected_at = datetime.utcnow()
    trip.rejected_reason = (clean_text(reason, "reason") or None) if reason is not None else None
    trip.approved_at = None
    db.commit()
    return trip_detail(db, trip)


def unreject_trip(db: Session, trip_id: int) -> dict:
    """C46 — ångra reject_trip: tillbaka till 'edited' (om resan redigerats)
    annars 'suggested'."""
    trip = _get(db, trip_id)
    if trip.status != "rejected":
        raise TripEditError(f"Trip {trip.id} is not rejected (status: {trip.status}).")
    trip.status = "edited" if trip.edited_at else "suggested"
    trip.rejected_at = None
    trip.rejected_reason = None
    db.commit()
    return trip_detail(db, trip)
