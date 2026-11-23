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

# car_private = egen bil (inte tjänstebil) -> Admin "Non-company car km".
MODES = ("flight", "train", "taxi", "bus", "car_phev", "car_private")
ENERGY_TYPES = ("jet_fuel", "electricity", "petrol", "none")
ENERGY_UNITS = ("kWh", "l")
SOURCES = ("receipt", "airport_table", "route_table", "assumption", "manual")

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
        return _fill_airports(db, trip, out)
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


def _flight_receipt(db: Session, trip: CalendarTrip) -> ProcessedMessage | None:
    """Flygkvittot för resan - e-ticket, aldrig bokningsbekräftelsen (C53 Del 0b)."""
    row = (db.query(TripExpectation)
           .filter(TripExpectation.trip_id == trip.id, TripExpectation.kind == "flight")
           .first())
    if row is None or not row.message_id:
        return None
    m = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == row.message_id).first()
    if m is None:
        return None
    try:
        from app.services.c53_receipts import original_of

        orig = original_of(db, m)
    except Exception:  # noqa: BLE001
        orig = None
    return orig or m


def _fill_airports(db: Session, trip: CalendarTrip, flights: list[dict]) -> list[dict]:
    """C54 - kalenderflyg utan avgångs-/ankomstflygplats: 1) flygkvittots
    segment med samma flightnummer, 2) resans kedja (föregående flygs ankomst,
    tur och retur till hemmaflygplatsen). Kedjan märks 'inferred' (uppskattning)."""
    if all(f["origin"] and f["dest"] for f in flights):
        return flights
    seg_by_no: dict[str, dict] = {}
    m = _flight_receipt(db, trip)
    for s in (getattr(m, "flight_segments", None) or []) if m is not None else []:
        if isinstance(s, dict) and s.get("flight_no"):
            seg_by_no[str(s["flight_no"]).replace(" ", "").upper()] = s
    for f in flights:
        s = seg_by_no.get((f.get("flight_no") or "").replace(" ", "").upper())
        if s:
            f["origin"] = f["origin"] or (s.get("origin") or "").upper() or None
            f["dest"] = f["dest"] or (s.get("dest") or "").upper() or None
    homes = _home()
    for i, f in enumerate(flights):
        if not f["origin"]:
            if i > 0 and flights[i - 1]["dest"]:
                f["origin"], f["inferred"] = flights[i - 1]["dest"], "origin"
            elif (i == 0 and len(flights) > 1 and flights[-1]["dest"] in homes
                  and f["dest"] and f["dest"] not in homes):
                f["origin"], f["inferred"] = flights[-1]["dest"], "origin"
        if not f["dest"] and i + 1 < len(flights) and flights[i + 1]["origin"]:
            f["dest"], f["inferred"] = flights[i + 1]["origin"], "destination"
    return flights


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


# ---------------------------------------------------------------- C54 Admin-kategorier


def flight_thresholds() -> tuple[float, float]:
    """(short < x, long >= y) km per flygsträcka. Env med default 700/3700."""
    return (_env_float("FLIGHT_SHORT_MAX_KM", 700.0), _env_float("FLIGHT_LONG_MIN_KM", 3700.0))


def flight_category(km: float | None) -> str | None:
    if km is None:
        return None
    short_max, long_min = flight_thresholds()
    if km < short_max:
        return "short"
    if km >= long_min:
        return "long"
    return "medium"


# ---------------------------------------------------------------- C54 tåg/buss med rutttabell


def _context_direction(day: date | None, airport: str, city: str, flights: list[dict],
                       trip: CalendarTrip) -> tuple[str | None, str | None]:
    """Riktning ur resans sammanhang: ankomstdagen = flygplats -> stad,
    hemresedagen = stad -> flygplats. Samma dag (dagsresa) = okänt."""
    out_day = (flights[0].get("arr_date") or flights[0].get("date")) if flights else trip.start_date
    back_day = flights[-1].get("date") if len(flights) > 1 else trip.end_date
    if day is None or out_day == back_day:
        return None, None
    if day == out_day:
        return airport, city
    if day == back_day:
        return city, airport
    return None, None


def _resolve_ground(r, mode: str, trip: CalendarTrip, flights: list[dict]) -> list[dict]:
    """Ett kvitto -> 1 delsträcka (2 vid tur och retur) med från/till och km."""
    from app.data import route_km as rk

    fields = list(getattr(r, "route_fields", None) or []) or [r.text]
    operator = rk.operator_of(" ".join(str(f) for f in fields if f) + " " + (r.vendor or ""))
    frm, to, seen = rk.direction_from(fields)
    direction_known = bool(frm and to)
    estimate_dir = False
    reason = None
    round_trip = any(rk.is_round_trip(f) for f in fields if f)
    if not direction_known:
        ends = list(seen)
        for p in rk.OPERATOR_FIXED.get(operator or "", ()):
            if p not in ends:
                ends.append(p)
        if len(ends) == 2:
            airport = next((p for p in ends if p in rk.AIRPORTS), None)
            city = next((p for p in ends if p != airport), None)
            if airport and city:
                if round_trip:
                    frm, to = airport, city
                else:
                    frm, to = _context_direction(r.day, airport, city, flights, trip)
                    estimate_dir = bool(frm)
                    if not frm:
                        reason = "direction_unknown"
                        frm, to = None, None
            else:
                reason = "direction_unknown"
        else:
            reason = "direction_unknown" if len(ends) == 1 else "no_route_in_table"
    km_receipt = parse_km(r.text)
    vendor = r.vendor or "Receipt"
    if km_receipt is not None:
        km, source, is_est = km_receipt, "receipt", estimate_dir
        note = f"{vendor}; km from receipt"
    else:
        hit = rk.lookup_km(frm, to, operator)
        if hit is not None:
            km, source, is_est = hit["km"], "route_table", True
            note = f"{vendor}; route table {hit['from']} - {hit['to']} {fmt_km(hit['km'])} km"
        else:
            km, source, is_est = None, "receipt", estimate_dir
            reason = reason or "no_route_in_table"
            note = f"{vendor} - km not on receipt"
    if estimate_dir:
        note += "; direction from the trip (flight arrival/departure day)"
    base = {
        "leg_key": f"receipt:{r.message_id}", "leg_date": r.day, "mode": mode,
        "from_place": frm, "to_place": to, "km": km,
        "energy_type": "none", "energy_amount": None, "energy_unit": None,
        "source": source, "is_estimate": is_est, "note": note,
        "receipt_message_id": r.message_id, "km_reason": None if km is not None else reason,
        "_dedupe": (fold_vendor(r.vendor), r.day, r.amount, frm, to)
        if direction_known and not round_trip else None,
        "_summary": "journeys made" in (r.text or "") or "receipts from" in (r.text or ""),
        "_coupled": bool(getattr(r, "bill_line_id", None)),
        "_order": _order_no(fields),
    }
    if not round_trip or not (frm and to):
        return [base]
    out_day = (flights[0].get("arr_date") or flights[0].get("date")) if flights else r.day
    back_day = flights[-1].get("date") if len(flights) > 1 else trip.end_date
    first = dict(base, leg_date=max(r.day, out_day) if r.day and out_day else r.day,
                 note=base["note"] + "; round trip ticket, leg 1 of 2")
    second = dict(base, leg_key=f"receipt:{r.message_id}:return", from_place=to, to_place=frm,
                  leg_date=back_day or r.day, note=base["note"] + "; round trip ticket, leg 2 of 2")
    first["_dedupe"] = ("rt", fold_vendor(r.vendor), r.day, r.amount)
    second["_dedupe"] = ("rt2", fold_vendor(r.vendor), r.day, r.amount)
    return [first, second]


_ORDER_RE = re.compile(r"(?:ordernummer|order number|order no\.?|order|booking)\s*[:#]?\s*(\d{5,})",
                       re.IGNORECASE)


def _order_no(fields) -> str | None:
    """Ordernummer ur ämne/filnamn - olika nummer = olika köp (inte dubblett)."""
    for f in fields:
        m = _ORDER_RE.search(str(f or ""))
        if m:
            return m.group(1)
    return None


def fold_vendor(v: str | None) -> str:
    from app.data.route_km import fold

    return re.sub(r"\s+", " ", fold(v)).strip()


def ground_legs(trip: CalendarTrip, flights: list[dict], receipts: list) -> list[dict]:
    """Tåg/buss/taxi ur kvitton. Två kvitton för samma köp (samma leverantör,
    dag, belopp och riktning) blir EN delsträcka; den andra visas som
    utesluten dubblett. Olika riktningar = olika resor (behålls)."""
    legs: list[dict] = []
    for r in receipts:
        mode = receipt_mode(None, r.text)
        if mode in ("train", "bus"):
            legs += _resolve_ground(r, mode, trip, flights)
            continue
        km = parse_km(r.text)
        legs.append({
            "leg_key": f"receipt:{r.message_id}", "leg_date": r.day, "mode": mode,
            "from_place": None, "to_place": None, "km": km,
            "energy_type": "none", "energy_amount": None, "energy_unit": None,
            "source": "receipt", "is_estimate": False,
            "note": f"{r.vendor or 'Receipt'}" + ("" if km is not None else " - km not on receipt"),
            "receipt_message_id": r.message_id,
            "km_reason": None if km is not None else "no_km_on_receipt",
        })
    groups: dict = {}
    for leg in legs:
        key = leg.pop("_dedupe", None)
        if key is not None:
            groups.setdefault(key, []).append(leg)
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda x: (not x.get("_coupled"), bool(x.get("_summary")),
                                  str(x["receipt_message_id"])))
        keep = group[0]
        for dup in group[1:]:
            if dup.get("_order") and keep.get("_order") and dup["_order"] != keep["_order"]:
                # Två olika köp samma väg och dag: två resor (km är densamma
                # åt båda hållen), men riktningen på kvittot kan vara fel.
                dup["note"] = (f"{dup['note']}; separate ticket (order {dup['_order']}), "
                               "same direction as another ticket - check direction")
                continue
            dup["excluded"] = True
            dup["note"] = (f"{dup['note']}; duplicate of receipt {keep['receipt_message_id']} "
                           "(same vendor, day, amount and route) - not counted")
    for leg in legs:
        leg.pop("_summary", None)
        leg.pop("_coupled", None)
        leg.pop("_order", None)
        leg.setdefault("excluded", False)
    return legs


# ---------------------------------------------------------------- bygg


def build_auto_legs(db: Session, trip: CalendarTrip, all_trips: list[CalendarTrip] | None = None,
                    receipts=None) -> tuple[list[dict], list[str]]:
    """Automatiska delsträckor + flaggor för en resa (sparas inte här)."""
    all_trips = all_trips if all_trips is not None else db.query(CalendarTrip).all()
    homes = _home()
    flags: list[str] = []
    legs: list[dict] = []

    flights = _trip_flights(db, trip)
    flight_m = _flight_receipt(db, trip) if flights else None
    flight_mid = flight_m.message_id if flight_m is not None else None
    for i, f in enumerate(flights):
        km, note = flight_km(f["origin"], f["dest"])
        fno = (f.get("flight_no") or "").replace(" ", "") or None
        inferred = f.get("inferred")
        if inferred:
            note += f"; {inferred} airport inferred from the trip (estimate)"
        legs.append({
            "leg_key": f"flight:{i}:{fno or '-'}:{f['origin'] or '?'}-{f['dest'] or '?'}",
            "leg_date": f["date"], "mode": "flight",
            "from_place": f["origin"], "to_place": f["dest"], "km": km,
            "energy_type": "jet_fuel", "energy_amount": None, "energy_unit": None,
            "source": "assumption" if inferred else "airport_table",
            "is_estimate": bool(inferred),
            "note": f"{f.get('flight_no') or 'Flight'} {f['origin'] or '?'}-{f['dest'] or '?'}; {note}",
            "flight_no": f.get("flight_no"), "flight_category": flight_category(km),
            "receipt_message_id": flight_mid,
            "km_reason": None if km is not None else "airport_unknown",
        })

    starts_home = bool(flights) and flights[0]["origin"] in homes
    ends_home = bool(flights) and flights[-1]["dest"] in homes
    if starts_home and not ends_home and not trip.open_return:
        flags.append("return_flight_missing")
        legs.append({
            "leg_key": "flight:return_missing", "leg_date": trip.end_date, "mode": "flight",
            "from_place": flights[-1]["dest"], "to_place": None, "km": None,
            "energy_type": "jet_fuel", "energy_amount": None, "energy_unit": None,
            "source": "airport_table", "is_estimate": False,
            "note": "Return flight not in the calendar - add km manually",
            "flight_no": None, "flight_category": None, "km_reason": "no_receipt",
        })

    legs += ground_legs(trip, flights, _local_receipts(db, trip, all_trips, receipts))

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
           "flight_category", "receipt_message_id", "km_reason", "excluded")


def _locked(row: TripLeg) -> bool:
    """Mikkos rader rörs aldrig: set_by='user' eller km han skrivit in (manual)."""
    return row.set_by == "user" or (row.source == "manual" and row.km is not None)


def sync_trip_legs(db: Session, trip: CalendarTrip, all_trips: list[CalendarTrip] | None = None,
                   receipts=None) -> tuple[list[TripLeg], list[str]]:
    """Räkna om auto-raderna och spara. Mikkos rader (set_by='user' eller
    source='manual' med km) skrivs aldrig över."""
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
        if _locked(row):
            continue
        for f in _FIELDS:
            v = leg.get(f)
            if f == "excluded":
                v = bool(v)
            setattr(row, f, v)
    for key, row in list(existing.items()):
        if key not in keys and not _locked(row):
            db.delete(row)
            existing.pop(key)
    db.commit()
    rows = sorted(existing.values(), key=lambda r: (r.seq if r.seq is not None else 999, r.id or 0))
    if any(r.km is None and not r.excluded for r in rows):
        flags = ["km_missing"] + flags
    return rows, flags


# ---------------------------------------------------------------- vy


def flight_category_of_km(r) -> str | None:
    return flight_category(r.km) if getattr(r, "mode", None) == "flight" else None


ADMIN_KEYS = ("short_flights_km", "medium_flights_km", "long_flights_km", "train_km",
              "bus_km", "non_company_car_km", "hotel_nights")
ADMIN_LABELS = {
    "short_flights_km": "Short flights km",
    "medium_flights_km": "Medium flights km",
    "long_flights_km": "Long flights km",
    "train_km": "Train km",
    "bus_km": "Bus km",
    "non_company_car_km": "Non-company car km",
    "hotel_nights": "Hotel nights",
}


def admin_category(r) -> str | None:
    """Admins rad för en delsträcka. None = inte i Admins rapport
    (tjänstebilen car_phev) eller flyg utan km (okänd kategori)."""
    mode = r.mode
    if mode == "flight":
        cat = r.flight_category or flight_category(r.km)
        return f"{cat}_flights_km" if cat else None
    if mode == "train":
        return "train_km"
    if mode == "bus":
        return "bus_km"
    if mode in ("taxi", "car_private"):
        return "non_company_car_km"
    return None


def leg_dict(r: TripLeg, receipt_info: dict | None = None) -> dict:
    d = {
        "leg_id": r.id,
        "trip_id": r.trip_id,
        "date": r.leg_date.isoformat() if r.leg_date else None,
        "mode": r.mode,
        "from": r.from_place,
        "to": r.to_place,
        "km": r.km,
        "km_missing": r.km is None,
        "km_reason": getattr(r, "km_reason", None) if r.km is None else None,
        "energy_type": r.energy_type,
        "energy_amount": r.energy_amount,
        "energy_unit": r.energy_unit,
        "source": r.source,
        "is_estimate": bool(r.is_estimate),
        "note": r.note,
        "flight_no": r.flight_no,
        "flight_category": r.flight_category or flight_category_of_km(r),
        "admin_category": admin_category(r),
        "company_car_info_only": r.mode == "car_phev",
        "receipt_message_id": r.receipt_message_id,
        "set_by": r.set_by,
        "excluded": bool(r.excluded),
    }
    if receipt_info is not None:
        d["receipt"] = receipt_info.get(r.receipt_message_id) if r.receipt_message_id else None
    return d


def receipt_infos(db: Session, rows: list[TripLeg]) -> dict:
    """Kvitto + Bezala-utlägg bredvid varje delsträcka (bara läsning)."""
    mids = {r.receipt_message_id for r in rows if r.receipt_message_id}
    if not mids:
        return {}
    out = {}
    for m in db.query(ProcessedMessage).filter(ProcessedMessage.message_id.in_(mids)).all():
        out[m.message_id] = {
            "receipt_id": m.id,
            "message_id": m.message_id,
            "vendor": m.vendor,
            "amount": m.amount,
            "currency": m.currency,
            "date": m.receipt_date,
            "file_name": m.file_name,
            "drive_link": m.drive_link,
            "bezala_transaction_id": m.bezala_transaction_id,
        }
    return out


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


def hotel_nights(db: Session, trip: CalendarTrip, receipts=None) -> dict:
    """Nätter per resa - samma logik som get_trip_checklist (en rad per natt)."""
    from app.services.trip_completeness import _days, _hotel_slots, load_receipts

    receipts = receipts if receipts is not None else load_receipts(db)
    slots = [s for s in _hotel_slots(trip, _days(db, trip), receipts)
             if s.get("status") in ("found", "missing")]
    found = sum(1 for s in slots if s["status"] == "found")
    return {"nights": len(slots), "with_receipt": found,
            "is_estimate": found < len(slots),
            "nights_without_receipt": [s["slot"] for s in slots if s["status"] == "missing"]}


def admin_totals(rows: list[TripLeg], nights: dict | None = None) -> dict:
    """De sju Admin-raderna för en mängd delsträckor (+ hotellnätter).
    EN funktion för Resor-vyn, get_emissions_summary och export_trip_legs."""
    values = {k: 0.0 for k in ADMIN_KEYS}
    estimate = {k: False for k in ADMIN_KEYS}
    missing = {"flights": 0, "train_km": 0, "bus_km": 0, "non_company_car_km": 0}
    car = {"km": 0.0, "kwh": 0.0, "litres": 0.0, "legs": 0}
    for r in rows:
        if r.excluded:
            continue
        if r.mode == "car_phev":
            car["legs"] += 1
            car["km"] += r.km or 0
            if r.energy_unit == "kWh":
                car["kwh"] += r.energy_amount or 0
            elif r.energy_unit == "l":
                car["litres"] += r.energy_amount or 0
            continue
        cat = admin_category(r)
        if r.km is None:
            key = "flights" if r.mode == "flight" else cat
            if key in missing:
                missing[key] += 1
            continue
        if cat is None:
            continue
        values[cat] += r.km
        if r.is_estimate:
            estimate[cat] = True
    if nights is not None:
        values["hotel_nights"] = nights["nights"]
        estimate["hotel_nights"] = bool(nights.get("is_estimate"))
    out = {k: (int(values[k]) if k == "hotel_nights" else round(values[k], 1))
           for k in ADMIN_KEYS}
    return {
        **out,
        "estimate": estimate,
        "missing": missing,
        "legs_missing_km": sum(missing.values()),
        "company_car_info_only": {
            "km": round(car["km"], 1), "kwh": round(car["kwh"], 2),
            "litres": round(car["litres"], 2), "legs": car["legs"],
            "note": "Company car (info only, not in Admin report)",
        },
    }


def sum_admin(parts: list[dict]) -> dict:
    """Årssumma av flera resors admin_totals."""
    total = {k: 0 for k in ADMIN_KEYS}
    estimate = {k: False for k in ADMIN_KEYS}
    missing = {"flights": 0, "train_km": 0, "bus_km": 0, "non_company_car_km": 0}
    car = {"km": 0.0, "kwh": 0.0, "litres": 0.0, "legs": 0}
    for p in parts:
        for k in ADMIN_KEYS:
            total[k] += p[k]
            estimate[k] = estimate[k] or p["estimate"][k]
        for k in missing:
            missing[k] += p["missing"][k]
        for k in car:
            car[k] += p["company_car_info_only"][k]
    out = {k: (int(total[k]) if k == "hotel_nights" else round(total[k], 1)) for k in ADMIN_KEYS}
    return {
        **out, "estimate": estimate, "missing": missing,
        "legs_missing_km": sum(missing.values()),
        "company_car_info_only": {"km": round(car["km"], 1), "kwh": round(car["kwh"], 2),
                                  "litres": round(car["litres"], 2), "legs": car["legs"],
                                  "note": "Company car (info only, not in Admin report)"},
    }


def _missing_list(rows: list[TripLeg]) -> list[dict]:
    return [{"leg_id": r.id, "date": r.leg_date.isoformat() if r.leg_date else None,
             "mode": r.mode, "from": r.from_place, "to": r.to_place,
             "reason": getattr(r, "km_reason", None) or "no_receipt", "note": r.note}
            for r in rows if r.km is None and not r.excluded]


def trip_legs_view(db: Session, trip: CalendarTrip, all_trips=None, receipts=None) -> dict:
    from app.services.trip_completeness import load_receipts

    receipts = receipts if receipts is not None else load_receipts(db)
    rows, flags = sync_trip_legs(db, trip, all_trips, receipts)
    info = receipt_infos(db, rows)
    return {
        "legs": [leg_dict(r, info) for r in rows],
        "legs_flags": flags,
        "legs_totals": _totals(rows),
        "admin": admin_totals(rows, hotel_nights(db, trip, receipts)),
        "legs_missing": _missing_list(rows),
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
        row.km_reason = None
        if source is None:
            row.source = "manual"
            row.is_estimate = False if is_estimate is None else row.is_estimate
    if clear_km:
        row.km = None
        row.km_reason = "no_receipt"
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
        if flight_category not in ("", "short", "medium", "long"):  # "" = efter km
            raise TripEditError("flight_category must be short, medium, long or empty")
        row.flight_category = flight_category or flight_category_of_km(row)
    if row.mode == "flight" and km is not None and flight_category is None:
        row.flight_category = flight_category_of_km(row)
    if row.km is None and not row.km_reason:
        row.km_reason = "no_receipt"
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


def _year_rows(db: Session, year: int):
    """[(resa, delsträckor, flaggor, admin)] - samma underlag för vy, MCP och export."""
    from app.services.trip_completeness import load_receipts

    all_trips = db.query(CalendarTrip).all()
    receipts = load_receipts(db)
    out = []
    for t in trips_for_year(db, year):
        rows, flags = sync_trip_legs(db, t, all_trips, receipts)
        out.append((t, rows, flags, admin_totals(rows, hotel_nights(db, t, receipts))))
    return out


def _rejected_count(db: Session, year: int) -> int:
    start, end = date(year, 1, 1), date(year, 12, 31)
    return (db.query(CalendarTrip)
            .filter(CalendarTrip.status == "rejected")
            .filter(CalendarTrip.start_date <= end, CalendarTrip.end_date >= start)
            .count())


def get_emissions_summary(db: Session, year: int = 2026) -> dict:
    """Admins sju rader per resa och för året (+ tjänstebilen separat som info).
    Resor-vyn, MCP och export_trip_legs läser alla härifrån."""
    year = int(year or 2026)
    data = _year_rows(db, year)
    all_rows = [r for _, rows, _, _ in data for r in rows]
    totals = _totals(all_rows)
    trips = []
    for t, rows, flags, admin in data:
        tt = _totals(rows)
        trips.append({
            "trip_id": t.id,
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "destination": t.destination,
            "status": t.status,
            "admin": admin,
            "km": round(sum(admin[k] for k in ADMIN_KEYS if k != "hotel_nights"), 1),
            "by_mode": {m: v["km"] for m, v in tt["by_mode"].items()},
            "kwh": admin["company_car_info_only"]["kwh"],
            "litres": admin["company_car_info_only"]["litres"],
            "legs": sum(1 for r in rows if not r.excluded),
            "legs_missing_km": admin["legs_missing_km"],
            "missing_legs": _missing_list(rows),
            "flags": flags,
        })
    year_admin = sum_admin([a for *_, a in data])
    active = [r for r in all_rows if not r.excluded]
    short_max, long_min = flight_thresholds()
    return {
        "year": year,
        "trips": len(trips),
        "trips_rejected_not_counted": _rejected_count(db, year),
        "admin": year_admin,
        "admin_labels": ADMIN_LABELS,
        "flight_thresholds_km": {"short_below": short_max, "long_from": long_min},
        "legs": len(active),
        "legs_with_km": sum(1 for r in active if r.km is not None),
        "legs_missing_km": sum(1 for r in active if r.km is None),
        "legs_excluded": sum(1 for r in all_rows if r.excluded),
        "estimated_legs": sum(1 for r in active if r.is_estimate),
        "total_km": round(sum(year_admin[k] for k in ADMIN_KEYS if k != "hotel_nights"), 1),
        "company_car_info_only": year_admin["company_car_info_only"],
        "by_mode": totals["by_mode"],
        "per_trip": trips,
        "car_assumptions": car_settings(),
        "note": ("Admin report rows: short (<700 km), medium (700-3700 km) and long "
                 "(3700+ km) flights per flight leg, train, bus, non-company car (taxi, "
                 "own car) and hotel nights. Company car km/kWh/litres are info only and "
                 "not in the Admin totals. Activity data only - no kg CO2e. "
                 "is_estimate = from route table or assumption."),
    }


EXPORT_COLUMNS = ("trip_id", "date", "mode", "from", "to", "km",
                  "km_missing", "km_reason", "energy_type", "energy_amount", "energy_unit",
                  "is_estimate", "source", "flight_no", "flight_category", "admin_category",
                  "receipt_message_id", "bezala_transaction_id", "note")
ADMIN_EXPORT_COLUMNS = ("trip_id", "year", "start_date", "end_date", "destination",
                        *[ADMIN_LABELS[k] for k in ADMIN_KEYS], "Estimate", "Legs missing km")


def _ascii_dash(v):
    from app.data.route_km import ascii_text

    return ascii_text(v)


def export_rows(db: Session, year: int = 2026) -> list[dict]:
    """Detaljexport: en rad per delsträcka. Tjänstebilen har admin_category tom
    (info only)."""
    out = []
    for t, rows, _, _ in _year_rows(db, year):
        info = receipt_infos(db, rows)
        for r in rows:
            if r.excluded:
                continue
            d = leg_dict(r, info)
            d["bezala_transaction_id"] = (d.get("receipt") or {}).get("bezala_transaction_id")
            if r.mode == "car_phev":
                d["admin_category"] = "company_car_info_only"
            row = {k: _ascii_dash(d.get(k)) for k in EXPORT_COLUMNS}
            row["is_estimate"] = "yes" if d["is_estimate"] else "no"
            row["km_missing"] = "yes" if d["km_missing"] else "no"
            out.append(row)
    return out


def admin_export_rows(db: Session, year: int = 2026) -> list[dict]:
    """Admin-läge: en rad per resa och år med exakt de sju kolumnerna (+ Total).
    Tjänstebilen ingår inte."""
    data = _year_rows(db, year)
    out = []

    def row(base: dict, a: dict) -> dict:
        r = dict(base)
        for k in ADMIN_KEYS:
            r[ADMIN_LABELS[k]] = a[k]
        r["Estimate"] = ", ".join(ADMIN_LABELS[k] for k in ADMIN_KEYS if a["estimate"][k]) or "no"
        r["Legs missing km"] = a["legs_missing_km"]
        return {k: _ascii_dash(v) for k, v in r.items()}

    for t, _, _, a in data:
        out.append(row({"trip_id": t.id, "year": year, "start_date": t.start_date.isoformat(),
                        "end_date": t.end_date.isoformat(), "destination": t.destination}, a))
    out.append(row({"trip_id": "Total", "year": year, "start_date": "", "end_date": "",
                    "destination": f"{len(data)} trips"}, sum_admin([a for *_, a in data])))
    return out


def route_table_rows() -> list[dict]:
    from app.data import route_km

    return [{k: _ascii_dash(r.get(k)) for k in
             ("operator", "mode", "from", "to", "km", "symmetric", "reference")}
            for r in route_km.routes()]


def _csv(rows: list[dict], columns) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(columns))
    w.writeheader()
    for row in rows:
        w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in columns})
    return buf.getvalue()


def export_csv(db: Session, year: int = 2026, mode: str = "detail") -> str:
    if mode == "admin":
        return _csv(admin_export_rows(db, year), ADMIN_EXPORT_COLUMNS)
    if mode == "route_table":
        return _csv(route_table_rows(),
                    ("operator", "mode", "from", "to", "km", "symmetric", "reference"))
    return _csv(export_rows(db, year), EXPORT_COLUMNS)
