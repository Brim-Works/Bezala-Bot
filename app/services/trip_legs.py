"""C53 — km och energi per resa (underlag för utsläppsrapportering).

En rad per delsträcka i `trip_legs`: flyg (stor cirkel mellan flygplatserna),
tåg/buss/taxi (km ur kvittot/beskrivningen, annars km_missing) och hemänden
i tjänstebilen (Audi A6 e-hybrid, plug-in-hybrid) som märkt uppskattning.

Boten levererar aktivitetsdata (km, kWh, liter) — den räknar inte kg CO2e;
Admin tillämpar egna faktorer. Gissa aldrig: saknas km blir det NULL och
flaggan `km_missing`. Uppskattningar har alltid `is_estimate = true`.

Inga skrivningar till Bezala eller Netvisor — bara bottens egen tabell.
Rader som Mikko ändrat (set_by='user') skrivs aldrig över av omräkningen.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import CalendarTrip, ProcessedMessage, TripExpectation, TripLeg
from app.services.calendar_trip_edit import TripEditError

HELSINKI = ZoneInfo("Europe/Helsinki")

MODES = ("flight", "train", "taxi", "bus", "car_phev")
ENERGY_TYPES = ("jet_fuel", "electricity", "petrol", "none")
ENERGY_UNITS = ("kWh", "l")
SOURCES = ("receipt", "airport_table", "assumption", "manual")

# ---------------------------------------------------------------- flyg

# Referensavstånd (Mikko/Admin 2026-11-22), km.
REFERENCE_KM: dict[frozenset, float] = {
    frozenset({"HEL", "ARN"}): 399.0,
    frozenset({"HEL", "OSL"}): 763.0,
    frozenset({"HEL", "GOT"}): 783.0,
    frozenset({"HEL", "CPH"}): 892.0,
}

# IATA → (lat, lon). Stor cirkel för flygplatser utan referensavstånd.
AIRPORT_COORDS: dict[str, tuple[float, float]] = {
    "HEL": (60.3172, 24.9633), "ARN": (59.6519, 17.9186), "BMA": (59.3544, 17.9417),
    "GOT": (57.6628, 12.2798), "MMX": (55.5363, 13.3762), "CPH": (55.6180, 12.6508),
    "OSL": (60.1939, 11.1004), "BGO": (60.2934, 5.2181), "TRD": (63.4578, 10.9240),
    "SVG": (58.8767, 5.6378), "AAL": (57.0928, 9.8492), "BLL": (55.7403, 9.1518),
    "AAR": (56.3000, 10.6190), "AGH": (56.2961, 12.8471), "VXO": (56.9291, 14.7280),
    "RIX": (56.9236, 23.9711), "TLL": (59.4133, 24.8328), "VNO": (54.6341, 25.2858),
    "KEF": (63.9850, -22.6056), "LHR": (51.4700, -0.4543), "LGW": (51.1537, -0.1821),
    "STN": (51.8860, 0.2389), "AMS": (52.3105, 4.7683), "FRA": (50.0379, 8.5622),
    "MUC": (48.3538, 11.7861), "BER": (52.3667, 13.5033), "HAM": (53.6304, 9.9882),
    "CDG": (49.0097, 2.5479), "ORY": (48.7262, 2.3652), "BRU": (50.9014, 4.4844),
    "ZRH": (47.4582, 8.5555), "VIE": (48.1103, 16.5697), "WAW": (52.1657, 20.9671),
    "DUB": (53.4264, -6.2499), "BCN": (41.2974, 2.0833), "MAD": (40.4983, -3.5676),
    "FCO": (41.8003, 12.2389), "MXP": (45.6306, 8.7281), "LIS": (38.7742, -9.1342),
    "PRG": (50.1008, 14.2600), "BUD": (47.4369, 19.2556), "IST": (41.2753, 28.7519),
    "OUL": (64.9301, 25.3546), "TKU": (60.5141, 22.2628), "TMP": (61.4141, 23.6044),
    "RVN": (66.5648, 25.8304),
}

EARTH_RADIUS_KM = 6371.0


def great_circle_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def flight_km(origin: str | None, dest: str | None) -> tuple[float | None, str]:
    """(km, note). km None = okänd flygplats → km_missing."""
    o, d = (origin or "").upper().strip(), (dest or "").upper().strip()
    if not o or not d:
        return None, "Airport missing - km not calculated"
    ref = REFERENCE_KM.get(frozenset({o, d}))
    if ref is not None:
        return ref, "Reference distance"
    if o in AIRPORT_COORDS and d in AIRPORT_COORDS:
        return round(great_circle_km(AIRPORT_COORDS[o], AIRPORT_COORDS[d]), 1), \
            "Great circle from IATA coordinates"
    return None, f"No coordinates for {o if o not in AIRPORT_COORDS else d} - add km manually"


# ---------------------------------------------------------------- tåg/buss/taxi

_KM_RE = re.compile(r"(?<![\d.,])(\d{1,4}(?:[.,]\d{1,3})?)\s*km\b", re.IGNORECASE)

_TAXI_WORDS = ("taxi", "uber", "bolt", "cabonline", "freenow", "free now", "taksi")
_TRAIN_WORDS = ("flytoget", "arlanda express", "skanetrafiken", "skånetrafiken",
                "öresundståg", "oresundstag", "sj.se", "sj ab", "sj ", "vy.no", " vy ",
                "dsb", "atrain", "snälltåget", "snalltaget", "mtr express", "train",
                "tåg", "tog ", "juna")
_BUS_WORDS = ("flygbussarna", "flybussen", "airport coach", "bus ", "buss", "bussi",
              "västtrafik", "vasttrafik", "ruter", "sl.se", "storstockholms lokaltrafik")
_HOME_LOCAL = ("hsl", "helsingin seudun", "vr.fi", "vr-yhtymä")


def parse_km(text: str | None) -> float | None:
    """'Taxi ... 24,3 km' → 24.3. Flera träffar: den första."""
    if not text:
        return None
    m = _KM_RE.search(text)
    if not m:
        return None
    try:
        km = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return km if 0 < km < 5000 else None


def receipt_mode(category: str | None, text: str | None) -> str | None:
    """train | bus | taxi ur kategori + leverantörsord, annars None."""
    t = f" {(text or '').lower()} "
    if any(w in t for w in _HOME_LOCAL):
        return None
    if category == "Taxi" or any(w in t for w in _TAXI_WORDS):
        return "taxi"
    if category == "Tåg" or any(w in t for w in _TRAIN_WORDS):
        return "train"
    if any(w in t for w in _BUS_WORDS):
        return "bus"
    if category == "Kollektivtrafik":
        return "bus"
    return None


def append_km(description: str | None, km: float | None) -> str | None:
    """Lägg 'xx km' sist i beskrivningen när km är känt (inte dubbelt)."""
    if not description or km is None:
        return description
    if re.search(r"\bkm\s*$", description.strip(), re.IGNORECASE):
        return description
    return f"{description.rstrip(' ,.')}, {fmt_km(km)} km"


def fmt_km(km: float) -> str:
    return f"{km:.1f}".rstrip("0").rstrip(".") if km != int(km) else str(int(km))


# ---------------------------------------------------------------- bil (hemänden)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw.strip().replace(",", "."))
    except ValueError:
        return default


def car_settings() -> dict:
    return {
        "home_km_one_way": _env_float("TRIP_CAR_HOME_KM_ONE_WAY", 48.0),
        "out_electric_share": _env_float("TRIP_CAR_OUT_ELECTRIC_SHARE", 1.0),
        "back_electric_share": _env_float("TRIP_CAR_BACK_ELECTRIC_SHARE", 0.0),
        "electric_kwh_per_100km": _env_float("TRIP_CAR_ELECTRIC_KWH_PER_100KM", 16.5),
        "petrol_l_per_100km_discharged": _env_float(
            "TRIP_CAR_PETROL_L_PER_100KM_DISCHARGED", 7.0),
        "home_place": os.environ.get("TRIP_CAR_HOME_PLACE", "").strip() or "Porvoo",
        "car": "Audi A6 e-hybrid quattro (plug-in hybrid, company car)",
    }


def car_energy(km: float | None, energy_type: str, cfg: dict | None = None) -> tuple[float | None, str | None]:
    cfg = cfg or car_settings()
    if km is None:
        return None, None
    if energy_type == "electricity":
        return round(km * cfg["electric_kwh_per_100km"] / 100, 2), "kWh"
    if energy_type == "petrol":
        return round(km * cfg["petrol_l_per_100km_discharged"] / 100, 2), "l"
    return None, None


def _car_legs(direction: str, d: date, share: float, cfg: dict) -> list[dict]:
    """En bilresa (ut/hem) → 1 rad (100 % el eller 100 % bensin) eller 2
    rader om andelen ligger mellan 0 och 1."""
    km = cfg["home_km_one_way"]
    share = min(max(share, 0.0), 1.0)
    home, hel = cfg["home_place"], "HEL"
    frm, to = (home, hel) if direction == "out" else (hel, home)
    parts = []
    if share > 0:
        parts.append(("electricity", round(km * share, 1), "" if share >= 1 else ":el"))
    if share < 1:
        parts.append(("petrol", round(km * (1 - share), 1), "" if share <= 0 else ":petrol"))
    out = []
    for energy_type, part_km, suffix in parts:
        amount, unit = car_energy(part_km, energy_type, cfg)
        label = "battery (charged at home)" if energy_type == "electricity" \
            else "petrol (battery empty)"
        out.append({
            "leg_key": f"car:{direction}{suffix}", "leg_date": d, "mode": "car_phev",
            "from_place": frm, "to_place": to, "km": part_km,
            "energy_type": energy_type, "energy_amount": amount, "energy_unit": unit,
            "source": "assumption", "is_estimate": True,
            "note": (f"Company car {'to' if direction == 'out' else 'from'} HEL, {label}; "
                     f"estimate {cfg['home_km_one_way']:g} km one way"),
        })
    return out


# ---------------------------------------------------------------- hjälp


def _hel_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(HELSINKI).date()


def _home() -> set[str]:
    try:
        from app.services.trip_detector import home_iatas

        return set(home_iatas()) or {"HEL"}
    except Exception:  # noqa: BLE001
        return {"HEL"}


def _trip_flights(db: Session, trip: CalendarTrip) -> list[dict]:
    """Flygsträckor: kalenderns flyg, annars flight_segments på flygkvittot."""
    out = []
    for f in trip.flights or []:
        out.append({
            "flight_no": f.get("flight_no"),
            "origin": (f.get("origin") or "").upper() or None,
            "dest": (f.get("destination_iata") or "").upper() or None,
            "date": _hel_date(f.get("start")) or trip.start_date,
            "arr_date": _hel_date(f.get("end")),
            "source_note": "calendar",
        })
    if out:
        return out
    row = (db.query(TripExpectation)
           .filter(TripExpectation.trip_id == trip.id, TripExpectation.kind == "flight")
           .first())
    if row is None or not row.message_id:
        return []
    m = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == row.message_id).first()
    for s in (m.flight_segments if m is not None else None) or []:
        if not isinstance(s, dict):
            continue
        d = None
        try:
            d = date.fromisoformat(str(s.get("date"))[:10]) if s.get("date") else None
        except ValueError:
            d = None
        if d is not None and not (trip.start_date - timedelta(days=1) <= d
                                  <= trip.end_date + timedelta(days=1)):
            continue
        out.append({
            "flight_no": s.get("flight_no"), "origin": (s.get("origin") or "").upper() or None,
            "dest": (s.get("dest") or "").upper() or None, "date": d or trip.start_date,
            "arr_date": d, "source_note": "flight receipt",
        })
    return out


def _has_moovy_parking(db: Session, trip: CalendarTrip) -> bool:
    if (trip.time_source or "") == "parking":
        return True
    row = (db.query(TripExpectation)
           .filter(TripExpectation.trip_id == trip.id, TripExpectation.kind == "parking",
                   TripExpectation.status == "found")
           .first())
    return row is not None


def _local_receipts(db: Session, trip: CalendarTrip, all_trips: list[CalendarTrip],
                    receipts=None) -> list:
    from app.services.trip_completeness import load_receipts

    receipts = receipts if receipts is not None else load_receipts(db)
    others = [t for t in all_trips if t.id != trip.id and t.status != "rejected"]
    out = []
    for r in receipts:
        if r.day is None or "local" not in r.kinds or r.kinds & {"parking", "hotel", "flight"}:
            continue
        if not (trip.start_date <= r.day <= trip.end_date + timedelta(days=1)):
            continue
        if r.day > trip.end_date and any(o.start_date <= r.day <= o.end_date for o in others):
            continue
        if r.amount is not None and r.amount < 0:
            continue
        if receipt_mode(None, r.text) is None:
            continue  # t.ex. bilhyra/hemmatrafik - inte en delsträcka här
        out.append(r)
    return out


# ---------------------------------------------------------------- bygg


def build_auto_legs(db: Session, trip: CalendarTrip, all_trips: list[CalendarTrip] | None = None,
                    receipts=None) -> tuple[list[dict], list[str]]:
    """Automatiska delsträckor + flaggor för en resa (sparas inte här)."""
    all_trips = all_trips if all_trips is not None else db.query(CalendarTrip).all()
    homes = _home()
    flags: list[str] = []
    legs: list[dict] = []

    flights = _trip_flights(db, trip)
    for i, f in enumerate(flights):
        km, note = flight_km(f["origin"], f["dest"])
        fno = (f.get("flight_no") or "").replace(" ", "") or None
        legs.append({
            "leg_key": f"flight:{i}:{fno or '-'}:{f['origin'] or '?'}-{f['dest'] or '?'}",
            "leg_date": f["date"], "mode": "flight",
            "from_place": f["origin"], "to_place": f["dest"], "km": km,
            "energy_type": "jet_fuel", "energy_amount": None, "energy_unit": None,
            "source": "airport_table", "is_estimate": False,
            "note": f"{f.get('flight_no') or 'Flight'} {f['origin'] or '?'}-{f['dest'] or '?'}; {note}",
            "flight_no": f.get("flight_no"), "flight_category": None,
        })

    starts_home = bool(flights) and flights[0]["origin"] in homes
    ends_home = bool(flights) and flights[-1]["dest"] in homes
    if starts_home and not ends_home and not trip.open_return:
        flags.append("return_flight_missing")
        legs.append({
            "leg_key": "flight:return_missing", "leg_date": trip.end_date, "mode": "flight",
            "from_place": flights[-1]["dest"], "to_place": None, "km": None,
            "energy_type": "jet_fuel", "energy_amount": None, "energy_unit": None,
            "source": "manual", "is_estimate": False,
            "note": "Return flight not in the calendar - add km manually",
            "flight_no": None, "flight_category": None,
        })

    for r in _local_receipts(db, trip, all_trips, receipts):
        mode = receipt_mode(None, r.text)
        km = parse_km(r.text)
        legs.append({
            "leg_key": f"receipt:{r.message_id}", "leg_date": r.day, "mode": mode,
            "from_place": None, "to_place": None, "km": km,
            "energy_type": "none", "energy_amount": None, "energy_unit": None,
            "source": "receipt", "is_estimate": False,
            "note": f"{r.vendor or 'Receipt'}" + ("" if km is not None else " - km not on receipt"),
            "receipt_message_id": r.message_id,
        })

    if starts_home and ends_home and _has_moovy_parking(db, trip):
        cfg = car_settings()
        back_date = flights[-1].get("arr_date") or trip.end_date
        legs = (_car_legs("out", trip.start_date, cfg["out_electric_share"], cfg)
                + legs + _car_legs("back", back_date, cfg["back_electric_share"], cfg))
    else:
        flags.append("home_transport_unknown")

    for i, leg in enumerate(legs):
        leg["seq"] = i
    return legs, flags


_FIELDS = ("seq", "leg_date", "mode", "from_place", "to_place", "km", "energy_type",
           "energy_amount", "energy_unit", "source", "is_estimate", "note", "flight_no",
           "flight_category", "receipt_message_id")


def sync_trip_legs(db: Session, trip: CalendarTrip, all_trips: list[CalendarTrip] | None = None,
                   receipts=None) -> tuple[list[TripLeg], list[str]]:
    """Räkna om auto-raderna och spara. Mikkos rader (set_by='user') rörs inte."""
    auto, flags = build_auto_legs(db, trip, all_trips, receipts)
    existing = {r.leg_key: r for r in db.query(TripLeg).filter(TripLeg.trip_id == trip.id).all()}
    keys = set()
    for leg in auto:
        keys.add(leg["leg_key"])
        row = existing.get(leg["leg_key"])
        if row is None:
            row = TripLeg(trip_id=trip.id, leg_key=leg["leg_key"], set_by="auto", excluded=False)
            db.add(row)
            existing[leg["leg_key"]] = row
        if row.set_by == "user":
            continue
        for f in _FIELDS:
            setattr(row, f, leg.get(f))
    for key, row in list(existing.items()):
        if key not in keys and row.set_by != "user":
            db.delete(row)
            existing.pop(key)
    db.commit()
    rows = sorted(existing.values(), key=lambda r: (r.seq if r.seq is not None else 999, r.id or 0))
    if any(r.km is None and not r.excluded for r in rows):
        flags = ["km_missing"] + flags
    return rows, flags


# ---------------------------------------------------------------- vy


def leg_dict(r: TripLeg) -> dict:
    return {
        "leg_id": r.id,
        "trip_id": r.trip_id,
        "date": r.leg_date.isoformat() if r.leg_date else None,
        "mode": r.mode,
        "from": r.from_place,
        "to": r.to_place,
        "km": r.km,
        "km_missing": r.km is None,
        "energy_type": r.energy_type,
        "energy_amount": r.energy_amount,
        "energy_unit": r.energy_unit,
        "source": r.source,
        "is_estimate": bool(r.is_estimate),
        "note": r.note,
        "flight_no": r.flight_no,
        "flight_category": r.flight_category,
        "receipt_message_id": r.receipt_message_id,
        "set_by": r.set_by,
        "excluded": bool(r.excluded),
    }


def _totals(rows: list[TripLeg]) -> dict:
    by_mode: dict[str, dict] = {}
    total_km = 0.0
    for r in rows:
        if r.excluded:
            continue
        m = by_mode.setdefault(r.mode, {"km": 0.0, "kwh": 0.0, "litres": 0.0,
                                        "legs": 0, "legs_missing_km": 0})
        m["legs"] += 1
        if r.km is None:
            m["legs_missing_km"] += 1
        else:
            m["km"] += r.km
            total_km += r.km
        if r.energy_amount is not None:
            if r.energy_unit == "kWh":
                m["kwh"] += r.energy_amount
            elif r.energy_unit == "l":
                m["litres"] += r.energy_amount
    for m in by_mode.values():
        m["km"] = round(m["km"], 1)
        m["kwh"] = round(m["kwh"], 2)
        m["litres"] = round(m["litres"], 2)
    return {"km": round(total_km, 1), "by_mode": by_mode}


def trip_legs_view(db: Session, trip: CalendarTrip, all_trips=None, receipts=None) -> dict:
    rows, flags = sync_trip_legs(db, trip, all_trips, receipts)
    return {
        "legs": [leg_dict(r) for r in rows],
        "legs_flags": flags,
        "legs_totals": _totals(rows),
    }


def get_trip_legs(db: Session, trip_id: int) -> dict:
    trip = db.query(CalendarTrip).filter(CalendarTrip.id == trip_id).first()
    if trip is None:
        raise TripEditError(f"Trip {trip_id} not found")
    return {"trip_id": trip.id, **trip_legs_view(db, trip)}


# ---------------------------------------------------------------- redigering


def update_trip_leg(db: Session, leg_id: int, *, km: float | None = None,
                    mode: str | None = None, date_: str | None = None,
                    from_place: str | None = None, to_place: str | None = None,
                    energy_type: str | None = None, energy_amount: float | None = None,
                    energy_unit: str | None = None, source: str | None = None,
                    is_estimate: bool | None = None, note: str | None = None,
                    flight_category: str | None = None, excluded: bool | None = None,
                    clear_km: bool = False) -> dict:
    """Mikko ändrar en delsträcka. Raden låses (set_by='user')."""
    row = db.query(TripLeg).filter(TripLeg.id == leg_id).first()
    if row is None:
        raise TripEditError(f"Trip leg {leg_id} not found")
    if mode is not None:
        if mode not in MODES:
            raise TripEditError(f"mode must be one of {list(MODES)}")
        row.mode = mode
    if km is not None:
        if km < 0 or km > 20000:
            raise TripEditError("km must be between 0 and 20000")
        row.km = round(float(km), 1)
        if source is None:
            row.source = "manual"
    if clear_km:
        row.km = None
    if date_ is not None:
        try:
            row.leg_date = date.fromisoformat(date_)
        except ValueError as exc:
            raise TripEditError("date must be YYYY-MM-DD") from exc
    if from_place is not None:
        row.from_place = from_place.strip()[:80] or None
    if to_place is not None:
        row.to_place = to_place.strip()[:80] or None
    if energy_type is not None:
        if energy_type not in ENERGY_TYPES:
            raise TripEditError(f"energy_type must be one of {list(ENERGY_TYPES)}")
        row.energy_type = energy_type
    if energy_unit is not None:
        if energy_unit not in ENERGY_UNITS:
            raise TripEditError(f"energy_unit must be one of {list(ENERGY_UNITS)}")
        row.energy_unit = energy_unit
    if energy_amount is not None:
        if energy_amount < 0:
            raise TripEditError("energy_amount must be >= 0")
        row.energy_amount = round(float(energy_amount), 2)
    elif row.mode == "car_phev" and (km is not None or energy_type is not None):
        row.energy_amount, row.energy_unit = car_energy(row.km, row.energy_type)
    if row.energy_type == "none":
        row.energy_amount, row.energy_unit = None, None
    if source is not None:
        if source not in SOURCES:
            raise TripEditError(f"source must be one of {list(SOURCES)}")
        row.source = source
    if is_estimate is not None:
        row.is_estimate = bool(is_estimate)
    if note is not None:
        row.note = note.strip() or None
    if flight_category is not None:
        if flight_category not in ("", "short", "medium", "long"):
            raise TripEditError("flight_category must be short, medium, long or empty")
        row.flight_category = flight_category or None
    if excluded is not None:
        row.excluded = bool(excluded)
    row.set_by = "user"
    db.commit()
    return leg_dict(row)


def add_trip_leg(db: Session, trip_id: int, *, mode: str, date_: str,
                 km: float | None = None, from_place: str | None = None,
                 to_place: str | None = None, energy_type: str = "none",
                 energy_amount: float | None = None, energy_unit: str | None = None,
                 is_estimate: bool = False, note: str | None = None) -> dict:
    trip = db.query(CalendarTrip).filter(CalendarTrip.id == trip_id).first()
    if trip is None:
        raise TripEditError(f"Trip {trip_id} not found")
    if mode not in MODES:
        raise TripEditError(f"mode must be one of {list(MODES)}")
    n = db.query(TripLeg).filter(TripLeg.trip_id == trip_id,
                                 TripLeg.leg_key.like("manual:%")).count()
    row = TripLeg(trip_id=trip_id, leg_key=f"manual:{n + 1}:{datetime.utcnow().timestamp():.0f}",
                  seq=900 + n, mode=mode, energy_type="none", source="manual",
                  set_by="user", excluded=False)
    db.add(row)
    db.flush()
    return update_trip_leg(db, row.id, km=km, date_=date_, from_place=from_place or "",
                           to_place=to_place or "", energy_type=energy_type,
                           energy_amount=energy_amount, energy_unit=energy_unit,
                           source="manual", is_estimate=is_estimate, note=note or "")


# ---------------------------------------------------------------- sammanställning


def trips_for_year(db: Session, year: int) -> list[CalendarTrip]:
    start, end = date(year, 1, 1), date(year, 12, 31)
    return (db.query(CalendarTrip)
            .filter(CalendarTrip.status != "rejected")
            .filter(CalendarTrip.start_date <= end, CalendarTrip.end_date >= start)
            .order_by(CalendarTrip.start_date, CalendarTrip.id)
            .all())


def _year_rows(db: Session, year: int) -> list[tuple[CalendarTrip, list[TripLeg], list[str]]]:
    from app.services.trip_completeness import load_receipts

    all_trips = db.query(CalendarTrip).all()
    receipts = load_receipts(db)
    return [(t, *sync_trip_legs(db, t, all_trips, receipts)) for t in trips_for_year(db, year)]


def get_emissions_summary(db: Session, year: int = 2026) -> dict:
    year = int(year or 2026)
    data = _year_rows(db, year)
    all_rows = [r for _, rows, _ in data for r in rows]
    totals = _totals(all_rows)
    trips = []
    for t, rows, flags in data:
        tt = _totals(rows)
        trips.append({
            "trip_id": t.id,
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "destination": t.destination,
            "km": tt["km"],
            "by_mode": {m: v["km"] for m, v in tt["by_mode"].items()},
            "kwh": round(sum(v["kwh"] for v in tt["by_mode"].values()), 2),
            "litres": round(sum(v["litres"] for v in tt["by_mode"].values()), 2),
            "legs": sum(1 for r in rows if not r.excluded),
            "legs_missing_km": sum(1 for r in rows if r.km is None and not r.excluded),
            "flags": flags,
        })
    active = [r for r in all_rows if not r.excluded]
    return {
        "year": year,
        "trips": len(trips),
        "legs": len(active),
        "legs_missing_km": sum(1 for r in active if r.km is None),
        "estimated_legs": sum(1 for r in active if r.is_estimate),
        "total_km": totals["km"],
        "by_mode": totals["by_mode"],
        "per_trip": trips,
        "car_assumptions": car_settings(),
        "note": ("Activity data only (km, kWh, litres) - no kg CO2e. Admin applies own "
                 "emission factors. Car legs are estimates (source=assumption). "
                 "Flights: great circle between airports, round trip = two legs."),
    }


EXPORT_COLUMNS = ("trip_id", "date", "mode", "from", "to", "km", "km_missing",
                  "energy_type", "energy_amount", "energy_unit", "is_estimate", "source",
                  "flight_no", "flight_category", "note")


def _ascii_dash(v):
    if isinstance(v, str):
        return v.replace("–", "-").replace("—", "-").replace("→", "-")
    return v


def export_rows(db: Session, year: int = 2026) -> list[dict]:
    out = []
    for t, rows, _ in _year_rows(db, year):
        for r in rows:
            if r.excluded:
                continue
            d = leg_dict(r)
            row = {k: _ascii_dash(d.get(k)) for k in EXPORT_COLUMNS}
            row["is_estimate"] = "yes" if d["is_estimate"] else "no"
            row["km_missing"] = "yes" if d["km_missing"] else "no"
            out.append(row)
    return out


def export_csv(db: Session, year: int = 2026) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(EXPORT_COLUMNS))
    w.writeheader()
    for row in export_rows(db, year):
        w.writerow({k: ("" if v is None else v) for k, v in row.items()})
    return buf.getvalue()
