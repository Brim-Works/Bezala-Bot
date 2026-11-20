"""Resor R1 — upptäck resor ur kalender-event (ren logik, ingen DB/IO).

Indata: normaliserade event från `calendar_client.normalize_event`
(dict med id, summary, description, location, start, end, all_day,
event_type, ...). `start`/`end` är `datetime` (med tidszon) för
tidsatta event och `date` för heldagsevent (end är då exklusivt, som i
Google Calendar).

Klassificering:
  - flyg:   Gmail-auto-event ("Flight to Stockholm (AY 801)"), Finnair,
            eller summary med flygnummer (AY 123, SK 1234, ...).
  - hotell: "Stay at …"/"Hotel …"/kända kedjor (Scandic, Sokos, …).
  - möte:   allt annat (utom födelsedagar, arbetsplats, fokustid m.m.).

Gruppering:
  - En resa startar vid första avresan (flyg vars destination inte är
    hemorten) och slutar vid hemkomsten (flyg till hemorten), oavsett
    om det sker samma dag eller senare.
  - Flera utresor i följd (anslutningar, Stockholm → London) hör till
    samma resa så länge ingen hemresa har skett.
  - Saknas hemresa (eller går det > MAX_TRIP_DAYS till nästa flyg)
    stängs resan som `open_return=True`.
  - Hemresa utan föregående avresa och hotell utanför resor rapporteras
    som `ungrouped`.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

DEFAULT_HOME_CITIES = ("helsinki", "helsingfors", "vantaa", "hel")
MAX_TRIP_DAYS = 21

# Event-typer från Google som aldrig är möten.
_IGNORED_EVENT_TYPES = {"birthday", "workingLocation", "focusTime", "outOfOffice"}

_FLIGHT_NO_RE = re.compile(
    r"\b(AY|SK|DY|D8|BT|LH|KL|AF|BA|TK|FR|U2|W6|LO|OS|LX|SN|EW|EK|QR|AZ|IB|"
    r"VY|UA|DL|AA|AC|N7|HV|DS|EI|TP|A3|OU|JU|LG|WF|RC)\s?\d{2,4}\b"
)
_FLIGHT_WORD_RE = re.compile(
    r"^\s*(flight|flyg|lento|flug)\s+(to|till|nach|kohteeseen)\b",
    re.IGNORECASE,
)
# Flygbolagsnamn räknas bara som flyg om det även finns en rutt
# ("Finnair HEL → ARN"), annars blir "Finnair kundmöte" ett flyg.
_AIRLINE_RE = re.compile(r"\b(finnair|norwegian|airbaltic|sas)\b", re.IGNORECASE)
_FLIGHT_DEST_RE = re.compile(
    r"(?:flight|flyg|lento|flug)\s+(?:to|till|nach|kohteeseen)\s+(.+?)\s*(?:\(|,|$)",
    re.IGNORECASE,
)
_ARROW_RE = re.compile(r"(?:→|->|–|—|\s-\s)\s*([A-Za-zÅÄÖåäöÜüÉé .]+?)\s*(?:\(|,|$)")
_HOTEL_RE = re.compile(
    r"^\s*(stay at|vistelse på|vistelse vid|majoitus|hotel|hotell)\b|"
    r"\b(hotel|hotell|hostel|scandic|radisson|clarion|hilton|sokos|marriott|"
    r"sheraton|holiday inn|best western|comfort hotel|quality hotel|"
    r"elite hotel|strawberry|airbnb|cumulus|novotel|ibis)\b",
    re.IGNORECASE,
)
_STAY_NAME_RE = re.compile(
    r"^\s*(?:stay at|vistelse på|vistelse vid|majoitus:?)\s+(.+)$", re.IGNORECASE,
)


# ---------- Hjälpare ----------


def home_cities() -> tuple[str, ...]:
    """Hemort-tokens. Kan överstyras med env TRIP_HOME_CITIES (kommasep.)."""
    raw = os.environ.get("TRIP_HOME_CITIES", "").strip()
    if raw:
        return tuple(p.strip().lower() for p in raw.split(",") if p.strip())
    return DEFAULT_HOME_CITIES


def _is_home(place: str | None, homes: Iterable[str]) -> bool | None:
    """True/False om platsen är hemorten, None om okänd."""
    if not place:
        return None
    words = set(re.findall(r"[a-zåäöü]+", place.lower()))
    return any(h in words for h in homes)


def _start_date(ev: dict) -> date:
    s = ev["start"]
    return s.date() if isinstance(s, datetime) else s


def _end_date(ev: dict) -> date:
    """Sista dag eventet berör (inklusive)."""
    e = ev["end"]
    if isinstance(e, datetime):
        return e.date()
    # Heldagsevent: end är exklusivt.
    return max(_start_date(ev), e - timedelta(days=1))


def _sort_key(ev: dict) -> float:
    s = ev["start"]
    if isinstance(s, datetime):
        if s.tzinfo is None:
            s = s.replace(tzinfo=timezone.utc)
        return s.timestamp()
    return datetime(s.year, s.month, s.day, tzinfo=timezone.utc).timestamp()


def _hhmm(value) -> str | None:
    return value.strftime("%H:%M") if isinstance(value, datetime) else None


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


# ---------- Klassificering ----------


def is_flight(ev: dict) -> bool:
    summary = ev.get("summary") or ""
    if _FLIGHT_WORD_RE.search(summary):
        return True
    if _FLIGHT_NO_RE.search(summary):
        return True
    return bool(_AIRLINE_RE.search(summary) and _ARROW_RE.search(summary))


def is_hotel(ev: dict) -> bool:
    if is_flight(ev):
        return False
    return bool(_HOTEL_RE.search(ev.get("summary") or ""))


def classify(ev: dict) -> str:
    """'flight' | 'hotel' | 'meeting' | 'ignored'."""
    if is_flight(ev):
        return "flight"
    if is_hotel(ev):
        return "hotel"
    if ev.get("event_type") in _IGNORED_EVENT_TYPES:
        return "ignored"
    return "meeting"


# Gmail skriver ibland bara flygplatskoden ("Flight to CPH (AY 959)").
_IATA_CITY = {
    "HEL": "Helsinki", "CPH": "Copenhagen", "ARN": "Stockholm",
    "BMA": "Stockholm", "OSL": "Oslo", "GOT": "Göteborg", "MMX": "Malmö",
    "BGO": "Bergen", "TRD": "Trondheim", "SVG": "Stavanger", "AAL": "Aalborg",
    "BLL": "Billund", "RIX": "Riga", "TLL": "Tallinn", "VNO": "Vilnius",
    "KEF": "Reykjavik", "LHR": "London", "LGW": "London", "STN": "London",
    "AMS": "Amsterdam", "FRA": "Frankfurt", "MUC": "München", "BER": "Berlin",
    "HAM": "Hamburg", "CDG": "Paris", "ORY": "Paris", "BRU": "Bryssel",
    "ZRH": "Zürich", "VIE": "Wien", "WAW": "Warszawa", "DUB": "Dublin",
    "BCN": "Barcelona", "MAD": "Madrid", "FCO": "Rom", "MXP": "Milano",
    "LIS": "Lissabon", "PRG": "Prag", "BUD": "Budapest", "IST": "Istanbul",
    "OUL": "Oulu", "TKU": "Turku", "TMP": "Tampere", "RVN": "Rovaniemi",
}


def _clean_destination(raw: str) -> str | None:
    dest = raw.strip(" .")
    if not dest:
        return None
    return _IATA_CITY.get(dest.upper(), dest)


def flight_destination(ev: dict) -> str | None:
    """Fritext-destination ur flygets summary (eller None)."""
    summary = ev.get("summary") or ""
    m = _FLIGHT_DEST_RE.search(summary) or _ARROW_RE.search(summary)
    if m:
        return _clean_destination(m.group(1))
    return None


def hotel_name(ev: dict) -> str:
    summary = (ev.get("summary") or "").strip()
    m = _STAY_NAME_RE.match(summary)
    return m.group(1).strip() if m else summary


# ---------- Gruppering ----------


def _new_trip(flight: dict, dest: str | None) -> dict:
    return {"flights": [flight], "dests": [(flight, dest)], "return_flight": None}


def _group_flights(
    flights: list[dict], homes: tuple[str, ...],
) -> tuple[list[dict], list[dict]]:
    """Gruppera sorterade flyg till rå-resor. Returnerar (resor, ungrouped)."""
    trips: list[dict] = []
    ungrouped: list[dict] = []
    current: dict | None = None

    for f in flights:
        dest = flight_destination(f)
        home = _is_home(dest, homes)

        if current is not None:
            last = current["flights"][-1]
            if (_start_date(f) - _end_date(last)).days > MAX_TRIP_DAYS:
                trips.append(current)  # stängs utan hemresa
                current = None

        if current is None:
            if home is True:
                ungrouped.append({
                    "event": f,
                    "reason": "return_without_departure",
                })
                continue
            current = _new_trip(f, dest)
            continue

        current["flights"].append(f)
        # Okänd destination när en resa är öppen tolkas som hemresa.
        if home is True or home is None:
            current["return_flight"] = f
            trips.append(current)
            current = None
        else:
            current["dests"].append((f, dest))

    if current is not None:
        trips.append(current)
    return trips, ungrouped


def _covers(ev: dict, d: date) -> bool:
    """Gäller hotell-eventet natten som börjar dag d?

    Utcheckningsdagen = _end_date(ev) (för Gmail-heldagsevent "Stay at …"
    är end exklusivt, dvs. dagen efter utcheckning). Sista natten är
    dagen före utcheckning.
    """
    s = _start_date(ev)
    last_night = max(s, _end_date(ev) - timedelta(days=1))
    return s <= d <= last_night


def _meeting_on(ev: dict, d: date) -> bool:
    return _start_date(ev) <= d <= _end_date(ev)


def _meeting_dict(ev: dict) -> dict:
    return {
        "id": ev["id"],
        "summary": ev.get("summary") or "",
        "start_time": _hhmm(ev["start"]),
        "end_time": _hhmm(ev["end"]),
        "location": ev.get("location") or "",
    }


def _build_trip(raw: dict, hotels: list[dict], meetings: list[dict]) -> dict:
    flights = raw["flights"]
    first = flights[0]
    ret = raw["return_flight"]
    start_date = _start_date(first)

    # Hotell som överlappar resan.
    span_end = _end_date(ret) if ret else _end_date(flights[-1])
    trip_hotels = [
        h for h in hotels
        if _start_date(h) <= span_end and _end_date(h) >= start_date
    ]
    if ret:
        end_date = _end_date(ret)
    else:
        # Öppen resa: förläng genom sista hotellnatten.
        end_date = max(
            [span_end] + [_end_date(h) for h in trip_hotels]
        )

    outbound = [(f, d) for f, d in raw["dests"]]
    dests = []
    for _, d in outbound:
        if d and d not in dests:
            dests.append(d)

    days = []
    d = start_date
    while d <= end_date:
        # Destination = senaste utresa som avgått senast denna dag.
        day_dest = None
        for f, dest in outbound:
            if _start_date(f) <= d and dest:
                day_dest = dest
        day_flights = [f for f in flights if _start_date(f) == d]
        day_hotel = next((h for h in trip_hotels if _covers(h, d)), None)
        day_meetings = [m for m in meetings if _meeting_on(m, d)]

        source_ids = [f["id"] for f in day_flights]
        if day_hotel:
            source_ids.append(day_hotel["id"])
        source_ids.extend(m["id"] for m in day_meetings)

        days.append({
            "date": d.isoformat(),
            "start_time": _hhmm(first["start"]) if d == start_date else None,
            "end_time": _hhmm(ret["end"]) if ret and d == end_date else None,
            "destination": day_dest,
            "meetings": [_meeting_dict(m) for m in day_meetings],
            "hotel": (
                {"id": day_hotel["id"], "name": hotel_name(day_hotel)}
                if day_hotel else None
            ),
            "source_event_ids": source_ids,
        })
        d += timedelta(days=1)

    all_ids: list[str] = [f["id"] for f in flights] + [h["id"] for h in trip_hotels]
    for day in days:
        for sid in day["source_event_ids"]:
            if sid not in all_ids:
                all_ids.append(sid)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_at": _iso(first["start"]),
        "end_at": _iso(ret["end"]) if ret else None,
        "destination": " → ".join(dests) if dests else None,
        "open_return": ret is None,
        "flights": [
            {
                "id": f["id"],
                "summary": f.get("summary") or "",
                "start": _iso(f["start"]),
                "end": _iso(f["end"]),
                "destination": flight_destination(f),
            }
            for f in flights
        ],
        "hotels": [{"id": h["id"], "name": hotel_name(h)} for h in trip_hotels],
        "days": days,
        "source_event_ids": all_ids,
    }


def detect_trips(events: Iterable[dict], homes: Iterable[str] | None = None) -> dict:
    """Huvudfunktion. Returnerar {'trips', 'ungrouped', 'stats'}."""
    homes_t = tuple(h.lower() for h in (homes or home_cities()))
    evs = sorted((e for e in events if e and e.get("start") is not None), key=_sort_key)

    flights, hotels, meetings = [], [], []
    for ev in evs:
        kind = classify(ev)
        if kind == "flight":
            flights.append(ev)
        elif kind == "hotel":
            hotels.append(ev)
        elif kind == "meeting":
            meetings.append(ev)

    raw_trips, ungrouped_raw = _group_flights(flights, homes_t)
    trips = [_build_trip(r, hotels, meetings) for r in raw_trips]

    used_hotels = {h["id"] for t in trips for h in t["hotels"]}
    for h in hotels:
        if h["id"] not in used_hotels:
            ungrouped_raw.append({"event": h, "reason": "hotel_without_flight"})

    ungrouped = [
        {
            "id": u["event"]["id"],
            "summary": u["event"].get("summary") or "",
            "date": _start_date(u["event"]).isoformat(),
            "reason": u["reason"],
        }
        for u in ungrouped_raw
    ]
    ungrouped.sort(key=lambda u: u["date"])

    return {
        "trips": trips,
        "ungrouped": ungrouped,
        "stats": {
            "events": len(evs),
            "flights": len(flights),
            "hotels": len(hotels),
            "meetings": len(meetings),
            "trips": len(trips),
            "open_return": sum(1 for t in trips if t["open_return"]),
            "ungrouped": len(ungrouped),
        },
    }
