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
from app.services.calendar_trip_store import _linked_receipts

HELSINKI = ZoneInfo("Europe/Helsinki")
STATUSES = ("suggested", "edited", "approved")
FLAG_NEEDS_REVIEW = "needs_review"
FLAG_MISSING_RETURN = "missing_return"
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


def summary(trip: CalendarTrip) -> dict:
    return {
        "id": trip.id,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "destination": trip.destination,
        "country": trip.country,
        "purpose": trip.purpose,
        "status": trip.status,
        "flags": list(trip.flags or []),
    }


def list_trips(
    db: Session,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    flagged_only: bool = False,
) -> list[dict]:
    q = db.query(CalendarTrip)
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
    out = [summary(t) for t in rows]
    if flagged_only:
        out = [t for t in out if t["flags"]]
    return out


def trip_detail(db: Session, trip: CalendarTrip) -> dict:
    trip_country = trip.country if trip.country and "," not in trip.country else None
    days = []
    for d in _days(db, trip):
        days.append({
            "date": d.day_date.isoformat(),
            "start_time_helsinki": d.start_time,
            "start_time_utc": _hel_to_utc(d.day_date, d.start_time),
            "end_time_helsinki": d.end_time,
            "end_time_utc": _hel_to_utc(d.day_date, d.end_time),
            "destination": d.destination,
            "country": d.country or trip_country,
            "purpose": d.purpose,
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
        "start": _times(trip.start_at),
        "end": _times(trip.end_at),
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
        "receipts": _linked_receipts(db, trip.start_date, trip.end_date),
        "edited_at": trip.edited_at.isoformat() if trip.edited_at else None,
        "approved_at": trip.approved_at.isoformat() if trip.approved_at else None,
    }


def get_trip(db: Session, trip_id: int) -> dict:
    return trip_detail(db, _get(db, trip_id))


# ---------- Skrivning ----------


def _mark_edited(trip: CalendarTrip) -> None:
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
    trip = _get(db, trip_id)
    if all(v is None for v in (start, end, destination, country, purpose, selected_flight_id)):
        raise TripEditError("Nothing to update — pass at least one field.")

    start_dt = _aware(trip.start_at)
    end_dt = _aware(trip.end_at)
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
        start_dt = _aware(chosen.get("start")) or start_dt
        flags = [f for f in flags if f != FLAG_NEEDS_REVIEW]

    if start is not None:
        start_dt = parse_moment(start, "start")
    if end is not None:
        end_dt = parse_moment(end, "end")
    if start_dt is not None and end_dt is not None and end_dt <= start_dt:
        raise TripEditError("end must be after start.")

    new_start_date = start_dt.astimezone(HELSINKI).date() if start_dt else trip.start_date
    new_end_date = end_dt.astimezone(HELSINKI).date() if end_dt else trip.end_date
    if new_end_date < new_start_date:
        raise TripEditError("end must be after start.")

    if destination is not None:
        trip.destination = clean_text(destination, "destination")
    if country is not None:
        trip.country = parse_country(country)
    if purpose is not None:
        trip.purpose = clean_text(purpose, "purpose")

    old_start, old_end = trip.start_date, trip.end_date
    trip.start_at = start_dt.isoformat() if start_dt else None
    trip.end_at = end_dt.isoformat() if end_dt else None
    trip.start_date = new_start_date
    trip.end_date = new_end_date
    if end is not None:
        flags = [f for f in flags if f != FLAG_MISSING_RETURN]
        trip.open_return = False
    trip.flags = flags
    trip.flights = flights
    _mark_edited(trip)
    db.flush()
    _sync_days(db, trip, old_start, old_end)

    # Avresa-/hemkomsttid på första/sista dagen följer resans start/slut.
    if start_dt is not None:
        first = _day(db, trip, trip.start_date)
        if first is not None:
            first.start_time = start_dt.astimezone(HELSINKI).strftime("%H:%M")
    if end_dt is not None and (end is not None):
        last = _day(db, trip, trip.end_date)
        if last is not None:
            last.end_time = end_dt.astimezone(HELSINKI).strftime("%H:%M")

    db.commit()
    return trip_detail(db, trip)


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
) -> dict:
    trip = _get(db, trip_id)
    day_date = parse_date(day, "date")
    row = _day(db, trip, day_date)
    if row is None:
        raise TripEditError(
            f"Trip {trip.id} has no day {day_date.isoformat()}. Use add_trip_day."
        )
    if all(v is None for v in (start_time, end_time, destination, country, hotel, purpose)):
        raise TripEditError("Nothing to update — pass at least one field.")
    _apply_day_fields(row, start_time, end_time, destination, country, hotel, purpose)
    _mark_edited(trip)
    db.commit()
    return trip_detail(db, trip)


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
    if day_date < trip.start_date:
        trip.start_date = day_date
        trip.start_at = None
    if day_date > trip.end_date:
        trip.end_date = day_date
        trip.end_at = None
    _mark_edited(trip)
    db.commit()
    return trip_detail(db, trip)


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
    if day_date == trip.end_date:
        trip.end_date = max(remaining)
        trip.end_at = None
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
