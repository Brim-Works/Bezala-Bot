"""C42 — fullständighetskontroll för kalenderresor och kortbetalningar.

Del 1: per kalenderresa räknas förväntade utlägg ut (flyg, en hotellpost per
natt, parkering HEL, lokaltransport) och matchas mot kvitton i boten
(`processed_messages`). Resultatet lagras i `trip_expectations` och räknas
om vid varje läsning, så ett kvitto som kommer in efteråt flyttar posten
från 'missing' till 'found'. Poster som Mikko satt själv (set_by='user',
t.ex. 'not_applicable') skrivs aldrig över.

Del 2: Bezala-kortrader utan kvitto (GET /missing_receipts — endast
läsning) speglas i `card_lines`, med ålder, källförslag och koppling till
resa när datumet ligger inom en resa.

Hårdregler: inga skrivningar till Bezala eller Netvisor, inga utkast.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import (
    CalendarTrip,
    CalendarTripDay,
    CardLine,
    ProcessedMessage,
    TripExpectation,
)

logger = logging.getLogger(__name__)

KINDS = ("flight", "hotel", "parking", "local")
STATUSES = ("found", "missing", "not_applicable", "optional")
# Det Mikko kan sätta via set_expectation_status. 'auto' tar bort hans
# markering så att omräkningen bestämmer igen.
USER_STATUSES = ("not_applicable", "found", "auto")
CARD_LINE_STATUSES = ("missing", "receipt_not_available", "private_expense")
# C43 — statusar Mikko satt som betyder "hanterad, larma inte".
HANDLED_CARD_LINE_STATUSES = ("receipt_not_available", "private_expense")

# Flygplatser långt från stan — lokaltransport är rimlig även när
# arbetsorten är samma stad (Arlanda Express, Flytoget).
REMOTE_AIRPORTS = frozenset({"ARN", "OSL"})

# Hur långt före resan ett kvitto får ligga när det uttryckligen nämner
# resan (flightnummer/resdatum) — biljetter köps ofta månader innan.
FLIGHT_LOOKBACK_DAYS = 365
PREPAID_LOOKBACK_DAYS = 90
AFTER_TRIP_DAYS = 3  # Moovy/hotell kan kvittas dagen efter hemkomst

_FLIGHT_WORDS = ("finnair", "amadeus", "norwegian", "airbaltic", "lufthansa",
                 "flysas", "sas scandinavian", "klm", "e-ticket", "eticket")
_HOTEL_WORDS = ("hotel", "hotell", "scandic", "strawberry", "clarion", "radisson",
                "elite", "comfort", "quality", "best western", "marriott", "hilton",
                "booking.com", "hostel", "first hotel", "nordic choice")
_PARKING_WORDS = ("moovy", "finavia", "aimo", "europark", "parkman", "easypark",
                  "parking", "parkering", "pysäköinti", "p-hel")
_LOCAL_WORDS = ("skanetrafiken", "skånetrafiken", "sj ", "sj.se", "flytoget",
                "arlanda express", "atrain", "uber", "bolt", "taxi", "vy.no",
                "öresundståg", "oresundstag", "dsb", "flygbussarna", "kringlan")
_HOME_LOCAL = ("hsl", "helsingin seudun", "vr.fi", "vr-yhtymä")

_CATEGORY_KIND = {
    "Flyg": "flight",
    "Hotell": "hotel",
    "Parkering": "parking",
    "Tåg": "local",
    "Kollektivtrafik": "local",
    "Taxi": "local",
    "Bilhyra": "local",
}


# ---------- Hjälp ----------


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", raw)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", raw)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def _norm_flight_no(value: str | None) -> str | None:
    if not value:
        return None
    m = re.match(r"^\s*([A-Z0-9]{2})\s*0*(\d{1,4})\s*$", value.upper())
    return f"{m.group(1)}{m.group(2)}" if m else None


def _date_tokens(d: date) -> list[str]:
    """Sätt som ett resdatum brukar stå i ett kvitto (gemener)."""
    mon = d.strftime("%b").lower()  # 'nov'
    return [
        d.isoformat(),
        f"{d.day}.{d.month}.{d.year}",
        f"{d:%d.%m.%Y}",
        f"{d:%d}{mon}",          # 18nov (Amadeus)
        f"{d:%d} {mon}",         # 18 nov
        f"{d.day} {mon}",        # 8 nov
    ]


class Receipt:
    """Lättviktsvy av en ProcessedMessage för matchningen."""

    __slots__ = ("message_id", "vendor", "amount", "currency", "day", "text",
                 "kinds", "drive_link", "bill_line_id", "kind", "segments",
                 "parking_entry_at", "parking_exit_at", "duplicate_of")

    def __init__(self, m: ProcessedMessage):
        self.message_id = m.message_id
        self.vendor = m.vendor
        self.amount = m.amount
        self.currency = m.currency
        self.drive_link = m.drive_link
        self.bill_line_id = m.bezala_transaction_id
        # C43 — 'change_fee' (Finnair-ombokning) räknas inte som flygbiljett.
        self.kind = getattr(m, "kind", None)
        # C44 — detaljer ur mailtexten (flygsegment, parkeringstider).
        self.segments = getattr(m, "flight_segments", None) or []
        self.parking_entry_at = getattr(m, "parking_entry_at", None)
        self.parking_exit_at = getattr(m, "parking_exit_at", None)
        self.duplicate_of = getattr(m, "duplicate_of", None)
        self.day = (
            _parse_day(m.receipt_date)
            or _parse_day(m.bezala_payment_date)
            or _parse_day(m.received_at)
        )
        self.text = " ".join(
            str(v) for v in (m.vendor, m.sender, m.subject, m.summary, m.file_name,
                             m.ai_description_en, m.bezala_payment_merchant) if v
        ).lower()
        self.kinds = classify_receipt(m.category, self.text)

    def as_link(self) -> dict:
        return {
            "message_id": self.message_id,
            "vendor": self.vendor,
            "amount": self.amount,
            "currency": self.currency,
            "date": self.day.isoformat() if self.day else None,
            "drive_link": self.drive_link,
            "bill_line_id": self.bill_line_id,
        }


def classify_receipt(category: str | None, text: str) -> set[str]:
    """Vilka slags resekostnad ett kvitto kan vara. Kategorin från AI:n
    går först; leverantörsord är reserv när kategorin saknas/är 'Annat'."""
    kinds: set[str] = set()
    if category in _CATEGORY_KIND:
        kinds.add(_CATEGORY_KIND[category])
        return kinds
    if category not in (None, "", "Annat"):
        return kinds
    if any(w in text for w in _FLIGHT_WORDS):
        kinds.add("flight")
    if any(w in text for w in _HOTEL_WORDS):
        kinds.add("hotel")
    if any(w in text for w in _PARKING_WORDS):
        kinds.add("parking")
    if any(w in text for w in _LOCAL_WORDS):
        kinds.add("local")
    return kinds


def load_receipts(db: Session) -> list[Receipt]:
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.status == "saved")
        .all()
    )
    # C44 — dubbletter (samma köp fångat två gånger) räknas inte.
    return [r for r in (Receipt(m) for m in rows)
            if r.day is not None and not r.duplicate_of]


def _days(db: Session, trip: CalendarTrip) -> list[CalendarTripDay]:
    return (
        db.query(CalendarTripDay)
        .filter(CalendarTripDay.trip_id == trip.id)
        .order_by(CalendarTripDay.day_date)
        .all()
    )


def _hotel_name(day: CalendarTripDay | None) -> str | None:
    if day is None or not day.hotel:
        return None
    if isinstance(day.hotel, dict):
        return day.hotel.get("name")
    return str(day.hotel)


def _name_words(name: str | None) -> list[str]:
    stop = {"hotel", "hotell", "stay", "at", "the", "and", "by", "in", "check"}
    return [w for w in re.findall(r"[a-zåäöæø]{4,}", (name or "").lower()) if w not in stop]


# ---------- Förväntningar ----------


def _home_origin(trip: CalendarTrip) -> bool | None:
    from app.services.trip_detector import home_iatas

    flights = trip.flights or []
    origin = (flights[0].get("origin") if flights else None) or None
    if not origin:
        return None
    origin = str(origin).strip().upper()[-3:]
    return origin in home_iatas()


_DEP_DATE_RE = re.compile(r"\bdep:\s*(\d{1,2})([a-z]{3})(\d{4})\b")
_MON = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def _flight_local_date(f: dict) -> date | None:
    from app.services.receipt_details import _tz

    raw = f.get("start")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(_tz(f.get("origin")))
    return dt.date()


def _receipt_flight_dates(r: Receipt) -> set[date]:
    """Resdatum som kvittot uttryckligen anger: segmentens datum eller
    'DEP: 25AUG2026' i ämnet (Amadeus)."""
    out = {d for d in (_parse_day(s.get("date")) for s in r.segments) if d}
    for m in _DEP_DATE_RE.finditer(r.text):
        mon = _MON.get(m.group(2))
        if mon:
            try:
                out.add(date(int(m.group(3)), mon, int(m.group(1))))
            except ValueError:
                pass
    return out


def flight_match_score(trip: CalendarTrip, r: Receipt) -> int:
    """Hur väl ett flygkvitto hör till resans flyg (C44 — inte bara
    leverantör):

      4  segment i kvittot = resans flightnummer + datum
      3  flightnummer + resdatum i kvittotexten
      2  resdatum (flygdatum) i kvittot
      1  kvittot anger inget resdatum men är daterat på en flygdag, eller
         har flightnumret och är köpt ≤ 60 dagar före
      0  annars — även när kvittot anger andra resdatum (samma AY 962 en
         annan dag är en annan resa: 25 aug-kvittot hör inte till 10 nov)."""
    flights = trip.flights or []
    trip_keys = {(_norm_flight_no(f.get("flight_no")), _flight_local_date(f)) for f in flights}
    trip_keys = {k for k in trip_keys if k[0] and k[1]}
    trip_dates = {d for d in (_flight_local_date(f) for f in flights) if d}
    if not trip_dates:
        trip_dates = {trip.start_date}
    nos = {k[0] for k in trip_keys} | {
        n for n in (_norm_flight_no(f.get("flight_no")) for f in flights) if n}

    if r.segments:
        seg_keys = {(_norm_flight_no(s.get("flight_no")), _parse_day(s.get("date")))
                    for s in r.segments}
        if seg_keys & trip_keys:
            return 4
        seg_dates = {k[1] for k in seg_keys if k[1]}
        return 2 if seg_dates & trip_dates else 0

    explicit = _receipt_flight_dates(r)
    if explicit and not (explicit & trip_dates):
        return 0
    compact = re.sub(r"\s+", "", r.text)
    no_hit = bool(nos) and any(
        re.search(rf"{n[:2].lower()}0*{n[2:]}(?!\d)", compact) for n in nos
    )
    date_hit = bool(explicit & trip_dates) or any(
        t in r.text for d in trip_dates for t in _date_tokens(d)
    )
    if no_hit and date_hit:
        return 3
    if date_hit:
        return 2
    if not explicit and (
        r.day in trip_dates
        or (no_hit and trip.start_date - timedelta(days=60) <= r.day <= trip.start_date)
    ):
        return 1  # köpt på flygdagen / flightnummer strax före resan
    return 0


def _match_flight(trip: CalendarTrip, receipts: list[Receipt]) -> Receipt | None:
    start, end = trip.start_date, trip.end_date
    best: tuple[int, Receipt] | None = None
    for r in receipts:
        if "flight" not in r.kinds or r.kind == "change_fee":
            continue
        if not (start - timedelta(days=FLIGHT_LOOKBACK_DAYS) <= r.day <= end + timedelta(days=30)):
            continue
        score = flight_match_score(trip, r)
        if score and (best is None or score > best[0]):
            best = (score, r)
    return best[1] if best else None


def _in_window(r: Receipt, trip: CalendarTrip, prepaid_tokens: list[str],
               after: int = AFTER_TRIP_DAYS) -> bool:
    if trip.start_date <= r.day <= trip.end_date + timedelta(days=after):
        return True
    # Förbetalt (bokat innan resan): bara om kvittot nämner resdatumet.
    if trip.start_date - timedelta(days=PREPAID_LOOKBACK_DAYS) <= r.day < trip.start_date:
        return any(t in r.text for t in prepaid_tokens)
    return False


LATE_RETURN_HOUR = 6  # hemma före 06:00 = ingen övernattning den natten


def _local_dt(value: str | None) -> datetime | None:
    from zoneinfo import ZoneInfo

    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo("Europe/Helsinki"))
    return dt


def last_night_end(trip: CalendarTrip) -> date:
    """Dagen resan 'egentligen' slutar för hotellnätter. end_date kan vara
    dagen efter när hemkomsten är efter midnatt (landar 23:35, hemma 00:35)
    — det är ingen övernattning. Hemflygets landning går först; annars
    räknas en hemkomst före 06:00 till dagen innan."""
    landing = _local_dt(trip.end_at)
    if landing is not None and trip.start_date <= landing.date() <= trip.end_date:
        return landing.date()
    home = _local_dt(trip.travel_end_at)
    if (home is not None and home.date() == trip.end_date
            and home.hour < LATE_RETURN_HOUR and trip.end_date > trip.start_date):
        return trip.end_date - timedelta(days=1)
    return trip.end_date


def _hotel_slots(trip: CalendarTrip, days: list[CalendarTripDay],
                 receipts: list[Receipt]) -> list[dict]:
    nights = (last_night_end(trip) - trip.start_date).days
    if nights <= 0:
        return [{
            "kind": "hotel", "slot": "", "status": "not_applicable",
            "note": "Day trip — no overnight stay",
        }]
    by_date = {d.day_date: d for d in days}
    night_dates = [trip.start_date + timedelta(days=i) for i in range(nights)]
    names = {n: _hotel_name(by_date.get(n)) for n in night_dates}
    tokens = _date_tokens(trip.start_date)
    hotel_receipts = sorted(
        (r for r in receipts if "hotel" in r.kinds and _in_window(r, trip, tokens)),
        key=lambda r: r.day,
    )
    covered: dict[date, Receipt] = {}

    def candidates(r: Receipt) -> list[date]:
        out = []
        for n in night_dates:
            if n in covered:
                continue
            words = _name_words(names[n])
            if words and not any(w in r.text for w in words):
                continue
            out.append(n)
        return out

    if len(hotel_receipts) >= nights:
        # Ett kvitto per natt, i datumordning.
        for r in hotel_receipts:
            c = candidates(r)
            if c:
                covered[c[0]] = r
    else:
        # Ett kvitto täcker oftast hela vistelsen: nätter före utcheckning
        # (kvittodatum). Förbetalt/kvitto vid incheckning → alla nätter.
        for r in hotel_receipts:
            c = candidates(r)
            before = [n for n in c if n < r.day]
            for n in (before or c):
                covered[n] = r

    out = []
    for n in night_dates:
        r = covered.get(n)
        label = f"Night {n.isoformat()}" + (f" — {names[n]}" if names[n] else "")
        out.append({
            "kind": "hotel", "slot": n.isoformat(),
            "status": "found" if r else "missing",
            "receipt": r, "note": label,
        })
    return out


def _parking_slot(trip: CalendarTrip, receipts: list[Receipt]) -> dict:
    from app.services.trip_parking import ParkingReceipt, parking_for_trip

    home = _home_origin(trip)
    if home is False:
        return {"kind": "parking", "slot": "", "status": "not_applicable",
                "note": "Trip does not start at HEL"}
    # C44 — Moovy/Finavia-kvitto vars infart/utfart täcker resans flygtider.
    timed = [r for r in receipts if r.parking_entry_at and r.parking_exit_at]
    if timed:
        by_id = {r.message_id: r for r in timed}
        proxies = []
        for r in timed:
            p = ParkingReceipt.__new__(ParkingReceipt)
            from app.services.trip_parking import _dt
            p.message_id, p.vendor, p.amount, p.currency = r.message_id, r.vendor, r.amount, r.currency
            p.entry, p.exit = _dt(r.parking_entry_at), _dt(r.parking_exit_at)
            proxies.append(p)
        hit = parking_for_trip(trip, proxies)
        if hit is not None:
            return {"kind": "parking", "slot": "", "status": "found",
                    "receipt": by_id[hit.message_id],
                    "note": "HEL parking (company car) — covers the flights"}
    tokens = _date_tokens(trip.start_date)
    hits = [r for r in receipts if "parking" in r.kinds and _in_window(r, trip, tokens)]
    # Närmast hemkomsten först (Moovy kvittar när bilen hämtas).
    hits.sort(key=lambda r: abs((r.day - trip.end_date).days))
    r = hits[0] if hits else None
    return {"kind": "parking", "slot": "", "status": "found" if r else "missing",
            "receipt": r, "note": "HEL parking (company car)"}


def _local_slots(trip: CalendarTrip, days: list[CalendarTripDay],
                 receipts: list[Receipt]) -> list[dict]:
    from app.services.trip_detector import home_iatas, iata_name

    homes = set(home_iatas())
    seen: dict[str, str] = {}
    for d in days:
        ap = (d.airport or "").upper()
        if not ap or ap in homes or ap in seen:
            continue
        city = d.destination or trip.destination
        if ap in REMOTE_AIRPORTS or (city and iata_name(ap) and city != iata_name(ap)):
            seen[ap] = city or ""
    out = []
    hits = [
        r for r in receipts
        if "local" in r.kinds
        and trip.start_date <= r.day <= trip.end_date + timedelta(days=1)
        and not any(w in r.text for w in _HOME_LOCAL)
    ]
    for ap, city in seen.items():
        r = hits[0] if hits else None
        out.append({
            "kind": "local", "slot": ap,
            "status": "found" if r else "optional",
            "receipt": r,
            "note": f"{ap} → {city}" if city else ap,
        })
    return out


def expected_items(db: Session, trip: CalendarTrip,
                   receipts: list[Receipt]) -> list[dict]:
    days = _days(db, trip)
    if not (trip.flights or []) and "domestic_unconfirmed" in (trip.flags or []):
        # C44 — hotell utan flyg: bara hotellnätterna förväntas.
        items = [
            {"kind": "flight", "slot": "", "status": "not_applicable", "receipt": None,
             "note": "Domestic trip without flight — confirm the trip"},
        ]
        items += _hotel_slots(trip, days, receipts)
        items.append({"kind": "parking", "slot": "", "status": "not_applicable",
                      "receipt": None, "note": "No flight from HEL"})
        return items
    r = _match_flight(trip, receipts)
    flight_nos = ", ".join(f.get("flight_no") for f in (trip.flights or []) if f.get("flight_no"))
    items: list[dict] = [{
        "kind": "flight", "slot": "",
        "status": "found" if r else "missing",
        "receipt": r,
        "note": f"Flights {flight_nos}" if flight_nos else "Flights",
    }]
    items += _hotel_slots(trip, days, receipts)
    items.append(_parking_slot(trip, receipts))
    items += _local_slots(trip, days, receipts)
    return items


# ---------- Lagring / omräkning ----------


def recompute_trip(db: Session, trip: CalendarTrip,
                   receipts: list[Receipt] | None = None) -> list[TripExpectation]:
    """Räkna om resans poster. Användarposter (set_by='user') behålls —
    bara kvittolänken fylls i om ett kvitto dyker upp. Committar inte."""
    if receipts is None:
        receipts = load_receipts(db)
    desired = expected_items(db, trip, receipts)
    existing = {
        (e.kind, e.slot or ""): e
        for e in db.query(TripExpectation).filter(TripExpectation.trip_id == trip.id).all()
    }
    keep: set[tuple[str, str]] = set()
    for item in desired:
        key = (item["kind"], item["slot"])
        keep.add(key)
        rec: Receipt | None = item.get("receipt")
        row = existing.get(key)
        if row is None:
            row = TripExpectation(trip_id=trip.id, kind=item["kind"], slot=item["slot"],
                                  set_by="auto")
            db.add(row)
            existing[key] = row
        if row.set_by == "user":
            if rec is not None and not row.message_id:
                row.message_id = rec.message_id
                row.bill_line_id = rec.bill_line_id
            continue
        new = (item["status"], rec.message_id if rec else None,
               rec.bill_line_id if rec else None, item.get("note"))
        if item["kind"] == "flight" and row.id is not None and (
            (row.status, row.message_id) != new[:2]
        ):
            logger.info("[c44-flight] trip=%s %s flight: %s %s → %s %s",
                        trip.id, trip.start_date, row.status, row.message_id,
                        new[0], new[1])
        if (row.status, row.message_id, row.bill_line_id, row.note) != new:
            row.status, row.message_id, row.bill_line_id, row.note = new
    # Rensa: auto-poster som inte längre förväntas, och användarposter för
    # hotellnätter som hamnat utanför resan (datum ändrade).
    start, end = trip.start_date.isoformat(), trip.end_date.isoformat()
    kept: list[TripExpectation] = []
    for key, row in existing.items():
        if key not in keep and not (
            row.set_by == "user" and not _night_outside(row.slot or "", start, end)
        ):
            db.delete(row)
            continue
        kept.append(row)
    db.flush()
    return sorted(
        kept,
        key=lambda r: (KINDS.index(r.kind) if r.kind in KINDS else 9, r.slot or ""),
    )


def _night_outside(slot: str, start: str, end: str) -> bool:
    """True om slot är ett datum (hotellnatt) som inte längre ligger i resan."""
    return len(slot) == 10 and slot[4] == "-" and not (start <= slot < end)


def _row_dict(row: TripExpectation, by_msg: dict[str, Receipt]) -> dict:
    rec = by_msg.get(row.message_id) if row.message_id else None
    return {
        "kind": row.kind,
        "slot": row.slot or None,
        "status": row.status,
        "set_by": row.set_by,
        "note": row.note,
        "receipt": rec.as_link() if rec else (
            {"message_id": row.message_id} if row.message_id else None
        ),
        "bill_line_id": row.bill_line_id,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def card_lines_for_trip(db: Session, trip: CalendarTrip) -> list[CardLine]:
    return (
        db.query(CardLine)
        .filter(CardLine.status.in_(CARD_LINE_STATUSES))
        .filter(CardLine.txn_date >= trip.start_date, CardLine.txn_date <= trip.end_date)
        .order_by(CardLine.txn_date)
        .all()
    )


def completeness(rows: Iterable[TripExpectation], card_lines: Iterable[CardLine]) -> dict:
    rows = list(rows)
    missing_rows = [r for r in rows if r.status == "missing"]
    missing_items = len(missing_rows)
    missing_cards = sum(1 for c in card_lines if c.status == "missing")
    missing = missing_items + missing_cards
    return {
        "status": "complete" if missing == 0 else "incomplete",
        "missing": missing,
        "missing_items": missing_items,
        "card_lines_without_receipt": missing_cards,
        # t.ex. ['flight', 'hotel 2026-03-02'] — syns direkt i list_trips.
        "missing_kinds": [
            f"{r.kind} {r.slot}" if r.slot else r.kind for r in missing_rows
        ],
    }


def checklist(db: Session, trip: CalendarTrip,
              receipts: list[Receipt] | None = None) -> dict:
    """Räkna om och returnera resans checklista (posterna + kortrader)."""
    if receipts is None:
        receipts = load_receipts(db)
    rows = recompute_trip(db, trip, receipts)
    cards = card_lines_for_trip(db, trip)
    by_msg = {r.message_id: r for r in receipts}
    today = _today()
    return {
        "trip_id": trip.id,
        "start_date": trip.start_date.isoformat(),
        "end_date": trip.end_date.isoformat(),
        "destination": trip.destination,
        "completeness": completeness(rows, cards),
        "items": [_row_dict(r, by_msg) for r in rows],
        "card_lines": [card_line_dict(c, today) for c in cards],
    }


def checklists_for(db: Session, trips: list[CalendarTrip]) -> dict[int, dict]:
    """Checklistor för flera resor med EN kvittoläsning. Committar."""
    if not trips:
        return {}
    receipts = load_receipts(db)
    out = {t.id: checklist(db, t, receipts) for t in trips}
    db.commit()
    return out


def get_trip_checklist(db: Session, trip_id: int) -> dict:
    from app.services.calendar_trip_edit import _get

    trip = _get(db, trip_id)
    out = checklist(db, trip)
    db.commit()
    return out


def set_expectation_status(db: Session, trip_id: int, kind: str, status: str,
                           note: str | None = None, date_: str | None = None) -> dict:
    from app.services.calendar_trip_edit import TripEditError, _get, clean_text, parse_date

    trip = _get(db, trip_id)
    if kind not in KINDS:
        raise TripEditError(f"Unknown kind '{kind}'. Use one of: {', '.join(KINDS)}.")
    if status not in USER_STATUSES:
        raise TripEditError(
            f"Unknown status '{status}'. Use 'not_applicable', 'found' (confirm) "
            "or 'auto' (undo your mark)."
        )
    note = clean_text(note, "note")
    receipts = load_receipts(db)
    rows = recompute_trip(db, trip, receipts)
    targets = [r for r in rows if r.kind == kind]
    if date_:
        d = parse_date(date_, "date").isoformat()
        if kind != "hotel":
            raise TripEditError("'date' only applies to hotel nights.")
        targets = [r for r in targets if r.slot == d]
        if not targets:
            raise TripEditError(
                f"No hotel night {d} on this trip "
                f"({trip.start_date.isoformat()} – {trip.end_date.isoformat()})."
            )
    if not targets:
        if status == "auto":
            raise TripEditError(f"No '{kind}' item on this trip.")
        row = TripExpectation(trip_id=trip.id, kind=kind, slot="", set_by="user")
        db.add(row)
        targets = [row]
    for row in targets:
        if status == "auto":
            row.set_by = "auto"
            row.note = None
        else:
            row.set_by = "user"
            row.status = status
            if note is not None:
                row.note = note
    db.flush()
    out = checklist(db, trip, receipts)
    db.commit()
    return out


# ---------- Överlevnad över kalender-refresh ----------


def snapshot_user_marks(db: Session, trip_ids: list[int]) -> list[dict]:
    """Mikkos markeringar på 'suggested'-resor som refresh ska ersätta."""
    if not trip_ids:
        return []
    # Kolumnfrågor (inte entiteter) — raderna bulk-raderas strax efteråt.
    rows = (
        db.query(CalendarTrip.start_date, CalendarTrip.end_date, TripExpectation.kind,
                 TripExpectation.slot, TripExpectation.status, TripExpectation.note)
        .join(CalendarTrip, CalendarTrip.id == TripExpectation.trip_id)
        .filter(TripExpectation.trip_id.in_(trip_ids), TripExpectation.set_by == "user")
        .all()
    )
    return [
        {"start_date": r[0], "end_date": r[1], "kind": r[2], "slot": r[3] or "",
         "status": r[4], "note": r[5]}
        for r in rows
    ]


def delete_for_trips(db: Session, trip_ids: list[int]) -> None:
    if trip_ids:
        db.query(TripExpectation).filter(
            TripExpectation.trip_id.in_(trip_ids)
        ).delete(synchronize_session=False)


def restore_user_marks(db: Session, marks: list[dict], trips: list[CalendarTrip]) -> int:
    """Flytta markeringar till de nya resorna (samma startdatum, annars
    överlappande). Hotellnätter utanför den nya resan släpps."""
    restored = 0
    for m in marks:
        target = next((t for t in trips if t.start_date == m["start_date"]), None)
        if target is None:
            target = next(
                (t for t in trips
                 if t.start_date <= m["end_date"] and t.end_date >= m["start_date"]),
                None,
            )
        if target is None:
            continue
        slot = m["slot"]
        if _night_outside(slot, target.start_date.isoformat(), target.end_date.isoformat()):
            continue
        db.add(TripExpectation(trip_id=target.id, kind=m["kind"], slot=slot,
                               status=m["status"], note=m["note"], set_by="user"))
        restored += 1
    return restored


# ---------- Del 2: kortrader utan kvitto ----------


def _vendor_from_description(desc: str | None) -> str | None:
    from app.services.match_health import _normalize_merchant_to_vendor

    return _normalize_merchant_to_vendor(desc)


def sync_card_lines(db: Session, raw_rows: list[dict]) -> dict:
    """Spegla Bezalas missing_receipts. Nya rader → 'missing'; rader som
    försvunnit ur listan → 'resolved'; Mikkos 'receipt_not_available'
    behålls. Committar."""
    from app.main import _normalize_missing_receipt

    now = datetime.utcnow()
    seen: set[str] = set()
    added = 0
    existing = {c.bill_line_id: c for c in db.query(CardLine).all()}
    for raw in raw_rows:
        n = _normalize_missing_receipt(raw)
        if n.get("id") is None:
            continue
        bid = str(n["id"])
        seen.add(bid)
        row = existing.get(bid)
        if row is None:
            row = CardLine(bill_line_id=bid, status="missing")
            db.add(row)
            existing[bid] = row
            added += 1
        elif row.status == "resolved":
            row.status = "missing"
            row.resolved_at = None
        row.txn_date = _parse_day(n.get("date"))
        row.description = n.get("description")
        row.vendor = _vendor_from_description(n.get("description"))
        row.amount = n.get("amount")
        row.currency = n.get("currency")
        row.last_seen_at = now
    resolved = 0
    for bid, row in existing.items():
        if bid not in seen and row.status != "resolved":
            row.status = "resolved"
            row.resolved_at = now
            resolved += 1
    db.commit()
    logger.info("C42 card lines: %d i Bezala, %d nya, %d lösta", len(seen), added, resolved)
    _log_card_line_summary(db)
    return {"in_bezala": len(seen), "added": added, "resolved": resolved}


def _log_card_line_summary(db: Session) -> None:
    from collections import Counter

    rows = db.query(CardLine).filter(CardLine.status == "missing").all()
    if not rows:
        logger.info("C42 kortrader utan kvitto: 0")
        return
    dated = [r.txn_date for r in rows if r.txn_date]
    oldest = min(dated) if dated else None
    vendors = Counter((r.vendor or "?") for r in rows).most_common(15)
    logger.info(
        "C42 kortrader utan kvitto: %d, äldsta %s (%s dagar), vendors=%s",
        len(rows), oldest, (_today() - oldest).days if oldest else "?", vendors,
    )


def sync_card_lines_from_bezala(db: Session) -> dict:
    """Läs Bezala (GET /missing_receipts) och spegla. Fel → status 'error'
    (cachade rader används)."""
    from app.services.bezala_client import BezalaClient

    try:
        client = BezalaClient()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"Bezala init: {exc}"}
    try:
        rows = client.list_missing_receipts() or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("C42: Bezala missing_receipts misslyckades: %s", exc)
        return {"status": "error", "detail": str(exc)}
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    return {"status": "ok", **sync_card_lines(db, rows)}


_GENERIC_WORDS = {"hotel", "hotell", "airport", "lentoasema", "the", "and", "oy", "ab",
                  "ltd", "inc", "gmbh", "as", "aps", "com", "www", "store", "shop",
                  "restaurant", "ravintola", "helsinki", "vantaa", "stockholm",
                  "copenhagen", "kobenhavn", "malmo", "finland", "sweden", "mikko",
                  "keinonen", "paypal", "sumup", "izettle", "zettle"}


def _vendor_words(vendor: str | None) -> list[str]:
    import unicodedata

    folded = unicodedata.normalize("NFKD", (vendor or "").lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return [w for w in re.findall(r"[a-z0-9]{4,}", folded) if w not in _GENERIC_WORDS]


def suggest_source(db: Session, vendor: str | None, *, _cache: dict | None = None) -> dict:
    """Var borde kvittot komma ifrån? 'link_fetch' (avsändare vars kvitto
    hämtas via länk), 'gmail_filter' (avsändare i Gmail-filtret) eller
    'unknown'. Tidigare kvitton från samma leverantör ges som ledtråd."""
    from app.services.html_only_senders import list_html_only_senders
    from app.services.settings_service import BUILTIN_INCLUDES, load_settings

    cache = _cache if _cache is not None else {}
    if "senders" not in cache:
        row = load_settings(db)
        cache["link"] = [s.lower() for s in (row.link_fetch_senders or [])]
        gmail = list(BUILTIN_INCLUDES) + list(row.include_senders or [])
        try:
            gmail += [s.sender_pattern for s in list_html_only_senders(db, only_active=True)]
        except Exception:  # noqa: BLE001
            pass
        cache["senders"] = [s.lower() for s in gmail if s]
        cache["history"] = [
            ((m.vendor or "").lower(), (m.sender or "").lower())
            for m in db.query(ProcessedMessage.vendor, ProcessedMessage.sender)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.status == "saved")
            .all()
        ]
    words = _vendor_words(vendor)
    if not words:
        return {"source": "unknown", "sender": None, "hint": None}
    for s in cache["link"]:
        if any(w in s for w in words):
            return {"source": "link_fetch", "sender": s, "hint": None}
    for s in cache["senders"]:
        if any(w in s for w in words):
            return {"source": "gmail_filter", "sender": s, "hint": None}
    for v, s in cache["history"]:
        if any(w in v or w in s for w in words):
            return {"source": "unknown", "sender": None,
                    "hint": f"Earlier receipt from {s or v} — sender not in the Gmail filter"}
    return {"source": "unknown", "sender": None, "hint": None}


def _trip_for_date(trips: list[CalendarTrip], d: date | None) -> CalendarTrip | None:
    if d is None:
        return None
    return next((t for t in trips if t.start_date <= d <= t.end_date), None)


def card_line_dict(c: CardLine, today: date, trip: CalendarTrip | None = None) -> dict:
    out = {
        "bill_line_id": c.bill_line_id,
        "date": c.txn_date.isoformat() if c.txn_date else None,
        "age_days": (today - c.txn_date).days if c.txn_date else None,
        "description": c.description,
        "vendor": c.vendor,
        "amount": c.amount,
        "currency": c.currency,
        "status": c.status,
        "note": c.note,
    }
    if c.status == "private_expense":
        from app.services.bill_line_status import private_expense_description

        out["bezala_comment"] = private_expense_description(c.note)
        out["bezala_transaction_id"] = c.bezala_transaction_id
    if trip is not None:
        out["trip_id"] = trip.id
    return out


def list_card_lines(db: Session, *, include_not_available: bool = False) -> list[dict]:
    statuses = CARD_LINE_STATUSES if include_not_available else ("missing",)
    rows = (
        db.query(CardLine)
        .filter(CardLine.status.in_(statuses))
        .order_by(CardLine.txn_date)
        .all()
    )
    trips = db.query(CalendarTrip).all()
    today = _today()
    cache: dict = {}
    out = []
    for c in rows:
        trip = _trip_for_date(trips, c.txn_date)
        d = card_line_dict(c, today)
        d["trip_id"] = trip.id if trip else None
        d["suggested_source"] = suggest_source(db, c.vendor, _cache=cache)
        # C43 — parkering (HEL/Moovy …) utanför kalenderresa: privat?
        from app.services.bill_line_status import possible_private_flags

        d["flags"] = possible_private_flags(db, c)
        if d["flags"]:
            d["question"] = (
                "Parking outside any calendar trip — private expense? "
                "Use mark_private_expense if Mikko says so."
            )
        out.append(d)
    return out


def set_card_line_status(db: Session, bill_line_id: str, status: str,
                         note: str | None = None, set_by: str = "user") -> dict:
    from app.services.calendar_trip_edit import TripEditError, clean_text

    if status not in CARD_LINE_STATUSES:
        raise TripEditError(
            "Unknown status. Use 'receipt_not_available' (stop alerting), "
            "'private_expense' (private purchase, deducted from salary) or "
            "'missing' (undo)."
        )
    row = db.query(CardLine).filter(CardLine.bill_line_id == str(bill_line_id)).first()
    if row is None:
        raise TripEditError(
            f"Card line {bill_line_id} not found among card payments without receipt."
        )
    if row.status == "resolved":
        raise TripEditError(f"Card line {bill_line_id} already has a receipt in Bezala.")
    row.status = status
    row.set_by = set_by
    note = clean_text(note, "note")
    if note is not None:
        row.note = note
    db.commit()
    return card_line_dict(row, _today())


# ---------- list_missing ----------


def list_missing(db: Session, older_than_days: int = 5, sync: bool = True) -> dict:
    """Allt som saknas: resposter (status 'missing' på resor som slutat för
    minst `older_than_days` dagar sedan) + kortrader utan kvitto som är
    minst så gamla. Läser Bezala (read-only) först om sync=True."""
    sync_result = sync_card_lines_from_bezala(db) if sync else {"status": "skipped"}
    today = _today()
    cutoff = today - timedelta(days=max(0, int(older_than_days)))
    trips = (
        db.query(CalendarTrip)
        .filter(CalendarTrip.end_date <= cutoff)
        .order_by(CalendarTrip.start_date)
        .all()
    )
    lists = checklists_for(db, trips)
    trip_items = []
    for t in trips:
        cl = lists[t.id]
        for item in cl["items"]:
            if item["status"] != "missing":
                continue
            trip_items.append({
                "trip_id": t.id,
                "trip_dates": f"{cl['start_date']} – {cl['end_date']}",
                "destination": t.destination,
                "kind": item["kind"],
                "slot": item["slot"],
                "note": item["note"],
                "age_days": (today - t.end_date).days,
            })
    cards = [
        c for c in list_card_lines(db)
        if c["age_days"] is None or c["age_days"] >= older_than_days
    ]
    # C44 — bästa kandidatkvitto per kortrad (AI MATCH-scoring). Kopplas
    # bara när Mikko ber om det (couple_receipt).
    try:
        from app.services.card_line_coupling import suggest_receipts

        rows = db.query(CardLine).filter(
            CardLine.bill_line_id.in_([c["bill_line_id"] for c in cards])
        ).all()
        sugg = suggest_receipts(db, rows) if rows else {}
    except Exception:  # noqa: BLE001 — förslagen får aldrig fälla listan
        db.rollback()
        logger.exception("C44: kopplingsförslag misslyckades")
        sugg = {}
    for c in cards:
        c["suggested_receipt"] = sugg.get(c["bill_line_id"])
    # C44 — resor som Mikko måste bekräfta (hotell utan flyg).
    unconfirmed = [
        {"trip_id": t.id, "dates": f"{t.start_date} – {t.end_date}",
         "destination": t.destination, "flags": list(t.flags or []),
         "hotels": [h.get("name") for h in (t.hotels or [])],
         "question": "Hotel without flight — was this a business trip?"}
        for t in db.query(CalendarTrip).order_by(CalendarTrip.start_date).all()
        if "domestic_unconfirmed" in (t.flags or [])
    ]
    with_sugg = sum(1 for c in cards if c.get("suggested_receipt"))
    return {
        "older_than_days": older_than_days,
        "bezala_sync": sync_result,
        "trip_items_missing": trip_items,
        "card_lines_without_receipt": cards,
        "unconfirmed_trips": unconfirmed,
        "counts": {"trip_items": len(trip_items), "card_lines": len(cards),
                   "card_lines_with_suggested_receipt": with_sugg,
                   "card_lines_without_candidate": len(cards) - with_sugg,
                   "unconfirmed_trips": len(unconfirmed)},
        # C43 — parkering utanför resa → fråga Mikko om privat.
        "possible_private_count": sum(1 for c in cards if c.get("flags")),
    }
