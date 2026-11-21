"""C44 — exakta resetider ur Moovy/Finavia-parkeringen.

Parkeringskvittot har infart + varaktighet ("05.03.2026 05:51, 16h 49min
10s"). Ett kvitto vars parkering täcker resans HEL-avgång och HEL-landning
hör till resan: det uppfyller parkeringsförväntan (C42) och ger resetiderna

    start = infart − TRIP_HOME_DRIVE_MIN
    slut  = utfart + TRIP_HOME_DRIVE_MIN

time_source = 'parking'. Gäller resor med status 'suggested'. För
edited/approved skrivs inget — tiderna sparas bara som förslag
(`suggestions['travel_times']`) och visas i get_trip.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import CalendarTrip, CalendarTripDay, ProcessedMessage

logger = logging.getLogger(__name__)

HELSINKI = ZoneInfo("Europe/Helsinki")
# Hur långt före avgång bilen får ha parkerats / efter landning hämtats.
MAX_PARK_BEFORE = timedelta(hours=12)
MAX_PICKUP_AFTER = timedelta(hours=12)


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=HELSINKI)


def hel_flight_window(trip: CalendarTrip) -> tuple[datetime | None, datetime | None]:
    """(avgång från HEL, landning på HEL) för resan."""
    from app.services.trip_detector import home_iatas

    homes = set(home_iatas())
    flights = trip.flights or []
    # Låsta resor: ombokade flyg (förslag) gäller före kalenderns gamla.
    sugg_flights = (trip.suggestions or {}).get("flights")
    if trip.status != "suggested" and sugg_flights:
        flights = sugg_flights
    dep = None
    if flights:
        # Resans första flyg utgår hemifrån när avreseorten saknas (äldre
        # kalenderevent utan IATA i platsen) — samma regel som travel_times.
        first = flights[0]
        origin = (first.get("origin") or "").upper()
        if not origin or origin in homes:
            dep = _dt(first.get("start"))
    arr = None
    for f in flights:
        if (f.get("destination_iata") or "").upper() in homes:
            arr = _dt(f.get("end"))
    return dep, arr


class ParkingReceipt:
    __slots__ = ("message_id", "vendor", "amount", "currency", "entry", "exit")

    def __init__(self, m: ProcessedMessage):
        self.message_id = m.message_id
        self.vendor = m.vendor
        self.amount = m.amount
        self.currency = m.currency
        self.entry = _dt(m.parking_entry_at)
        self.exit = _dt(m.parking_exit_at)


def load_parking(db: Session) -> list[ParkingReceipt]:
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.parking_entry_at.isnot(None))
        .filter(ProcessedMessage.duplicate_of.is_(None))
        .all()
    )
    return [p for p in (ParkingReceipt(m) for m in rows) if p.entry and p.exit]


def parking_for_trip(trip: CalendarTrip, parking: list[ParkingReceipt]) -> ParkingReceipt | None:
    """Parkeringen som täcker resans HEL-flygtider (infart före avgång,
    utfart efter landning)."""
    dep, arr = hel_flight_window(trip)
    if dep is None or arr is None:
        return None
    best = None
    for p in parking:
        if not (dep - MAX_PARK_BEFORE <= p.entry <= dep):
            continue
        if not (arr <= p.exit <= arr + MAX_PICKUP_AFTER):
            continue
        slack = (dep - p.entry) + (p.exit - arr)
        if best is None or slack < best[0]:
            best = (slack, p)
    return best[1] if best else None


def parking_travel_times(p: ParkingReceipt) -> tuple[datetime, datetime]:
    from app.services.trip_detector import travel_margins

    _, drive, _ = travel_margins()
    start = (p.entry - timedelta(minutes=drive)).astimezone(HELSINKI).replace(second=0, microsecond=0)
    end = (p.exit + timedelta(minutes=drive)).astimezone(HELSINKI).replace(second=0, microsecond=0)
    return start, end


def _set_days(db: Session, trip: CalendarTrip, start: datetime, end: datetime) -> None:
    """Första/sista dagens tider + datumintervall följer resetiderna."""
    days = {
        d.day_date: d
        for d in db.query(CalendarTripDay).filter(CalendarTripDay.trip_id == trip.id).all()
    }
    new_start, new_end = start.date(), end.date()
    for dd, row in list(days.items()):
        if dd < new_start or dd > new_end:
            if not row.meetings and not row.hotel:
                db.delete(row)
                days.pop(dd)
    d = new_start
    template = days.get(trip.end_date) or days.get(trip.start_date)
    while d <= new_end:
        if d not in days:
            row = CalendarTripDay(
                trip_id=trip.id, day_date=d, status="suggested", meetings=[],
                source_event_ids=[],
                destination=template.destination if template else trip.destination,
                country=template.country if template else trip.country,
                destination_source="carried",
                free_meals=0, free_meal_sources=[], meals_source="calendar",
            )
            db.add(row)
            days[d] = row
        d += timedelta(days=1)
    for row in days.values():
        if row.day_date == new_start:
            row.start_time = start.strftime("%H:%M")
        elif row.start_time and row.day_date != new_start:
            row.start_time = None
        if row.day_date == new_end:
            row.end_time = end.strftime("%H:%M")
        elif row.end_time and row.day_date != new_end:
            row.end_time = None
    trip.start_date = min(days) if days else new_start
    trip.end_date = max(days) if days else new_end


def apply_parking_times(db: Session) -> dict:
    """Sätt resetider ur parkeringen på 'suggested'-resor; spara förslag på
    låsta resor. Committar. Returnerar statistik."""
    parking = load_parking(db)
    applied = suggested = 0
    for trip in db.query(CalendarTrip).all():
        p = parking_for_trip(trip, parking)
        sugg = dict(trip.suggestions or {})
        if p is None:
            if "travel_times" in sugg:
                sugg.pop("travel_times")
                trip.suggestions = sugg or None
            if trip.status == "suggested" and trip.time_source == "parking":
                trip.time_source = "flight_default"
            continue
        start, end = parking_travel_times(p)
        info = {
            "start": start.isoformat(), "end": end.isoformat(), "source": "parking",
            "message_id": p.message_id,
            "parking_entry": p.entry.astimezone(HELSINKI).isoformat(),
            "parking_exit": p.exit.astimezone(HELSINKI).isoformat(),
        }
        if trip.status == "suggested":
            trip.travel_start_at = start.isoformat()
            trip.travel_end_at = end.isoformat()
            trip.time_source = "parking"
            sugg.pop("travel_times", None)
            trip.suggestions = sugg or None
            _set_days(db, trip, start, end)
            applied += 1
        else:
            sugg["travel_times"] = info
            trip.suggestions = sugg
            suggested += 1
    db.commit()
    logger.info("[c44-parking] resetider ur parkering: %d satta, %d förslag på låsta resor",
                applied, suggested)
    return {"applied": applied, "suggested_on_locked": suggested, "receipts": len(parking)}


def time_source(trip: CalendarTrip) -> str | None:
    """time_source för visning. Låsta resor: 'parking' om tiderna stämmer
    med parkeringsförslaget, annars det sparade värdet eller 'manual'."""
    if trip.status == "suggested":
        return trip.time_source
    tt = (trip.suggestions or {}).get("travel_times")
    if tt and _dt(tt.get("start")) == _dt(trip.travel_start_at) and _dt(tt.get("end")) == _dt(trip.travel_end_at):
        return "parking"
    if trip.edited_at is not None:
        return "manual"
    return trip.time_source

