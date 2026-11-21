"""Resor — upptäck resor ur kalender-event (ren logik, ingen DB/IO).

Indata: normaliserade event från `calendar_client.normalize_event`
(dict med id, summary, location, start, end, all_day, event_type,
visibility, transparency, self_response, attendees_other, ...).
`start`/`end` är `datetime` (med tidszon) för tidsatta event och `date`
för heldagsevent (end är då exklusivt, som i Google Calendar).

Flyg (C39 — strikt, så att andras flyg inte räknas som Mikkos):
  - Gmail-auto-event (eventType fromGmail) med titeln
    "Flight to <stad/IATA> (XX nnn)". Avreseort läses ur location
    ("Helsinki HEL" → HEL).
  - Manuellt event med exakt mönstret
    "Finnair <stad> (IATA) - <stad> (IATA)".
  Allt annat ("Filippas flyg landar LH2466", "Italienarna landar AY1772")
  är INTE flyg.
  Manuellt Finnair-event med samma start/slut som ett Gmail-flyg är en
  dubblett och slås ihop med Gmail-flyget.

Gruppering:
  - En resa startar vid första avresan och slutar vid hemkomsten
    (flyg till hemorten).
  - Utresa FRÅN hemorten medan en resa är öppen betyder att Mikko var
    hemma emellan: föregående resa stängs och flaggas `missing_return`.
  - Två utresor från hemorten samma dag till samma ort innan hemresa
    (t.ex. AY 951 06:20 + AY 953 07:25) slås inte ihop tyst: resan
    flaggas `needs_review` och båda alternativen sparas i
    `flight_options`.
  - Utresa från annan ort än hemorten (CPH → OSL) är en anslutning
    inom samma resa.
  - Saknas hemresa (eller går det > MAX_TRIP_DAYS till nästa flyg)
    flaggas resan `missing_return`.

Destination normaliseras via IATA-kod till svenskt namn
(CPH Köpenhamn, ARN Stockholm, GOT Göteborg, OSL Oslo, HEL Helsingfors).

Möten per resdag filtreras (se `is_relevant_meeting`).
"""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

DEFAULT_HOME_IATA = ("HEL",)
MAX_TRIP_DAYS = 21

FLAG_MISSING_RETURN = "missing_return"
FLAG_NEEDS_REVIEW = "needs_review"

# Event-typer från Google som aldrig är möten (normaliserade: gemener,
# utan "_", så både API:ts "workingLocation" och "WORKING_LOCATION" matchar).
_IGNORED_EVENT_TYPES = {"birthday", "workinglocation", "focustime", "outofoffice"}

_GMAIL_FLIGHT_RE = re.compile(
    r"^\s*flight to\s+(?P<dest>.+?)\s*\((?P<no>[A-Z0-9]{2})\s?(?P<num>\d{1,4})\)\s*$",
    re.IGNORECASE,
)
_FINNAIR_RE = re.compile(
    r"^\s*Finnair\s+(?P<o_city>[^()]+?)\s*\((?P<o>[A-Z]{3})\)\s*-\s*"
    r"(?P<d_city>[^()]+?)\s*\((?P<d>[A-Z]{3})\)\s*$"
)
_IATA_TAIL_RE = re.compile(r"\b([A-Z]{3})\s*$")

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

# IATA → (svenskt namn, landskod)
IATA_INFO: dict[str, tuple[str, str]] = {
    "HEL": ("Helsingfors", "FI"), "CPH": ("Köpenhamn", "DK"),
    "ARN": ("Stockholm", "SE"), "BMA": ("Stockholm", "SE"),
    "GOT": ("Göteborg", "SE"), "MMX": ("Malmö", "SE"),
    "OSL": ("Oslo", "NO"), "BGO": ("Bergen", "NO"), "TRD": ("Trondheim", "NO"),
    "SVG": ("Stavanger", "NO"), "AAL": ("Aalborg", "DK"), "BLL": ("Billund", "DK"),
    "AAR": ("Århus", "DK"), "RIX": ("Riga", "LV"), "TLL": ("Tallinn", "EE"),
    "VNO": ("Vilnius", "LT"), "KEF": ("Reykjavik", "IS"),
    "LHR": ("London", "GB"), "LGW": ("London", "GB"), "STN": ("London", "GB"),
    "AMS": ("Amsterdam", "NL"), "FRA": ("Frankfurt", "DE"), "MUC": ("München", "DE"),
    "BER": ("Berlin", "DE"), "HAM": ("Hamburg", "DE"), "CDG": ("Paris", "FR"),
    "ORY": ("Paris", "FR"), "BRU": ("Bryssel", "BE"), "ZRH": ("Zürich", "CH"),
    "VIE": ("Wien", "AT"), "WAW": ("Warszawa", "PL"), "DUB": ("Dublin", "IE"),
    "BCN": ("Barcelona", "ES"), "MAD": ("Madrid", "ES"), "FCO": ("Rom", "IT"),
    "MXP": ("Milano", "IT"), "LIS": ("Lissabon", "PT"), "PRG": ("Prag", "CZ"),
    "BUD": ("Budapest", "HU"), "IST": ("Istanbul", "TR"),
    "OUL": ("Uleåborg", "FI"), "TKU": ("Åbo", "FI"), "TMP": ("Tammerfors", "FI"),
    "RVN": ("Rovaniemi", "FI"),
}

# Stadsnamn (alla språk/varianter, utan diakritik, gemener) → IATA.
_CITY_TOKEN_IATA: dict[str, str] = {
    "helsinki": "HEL", "helsingfors": "HEL", "vantaa": "HEL",
    "copenhagen": "CPH", "kobenhavn": "CPH", "kopenhamn": "CPH",
    "koopenhamina": "CPH", "kastrup": "CPH",
    "stockholm": "ARN", "tukholma": "ARN", "arlanda": "ARN", "bromma": "BMA",
    "goteborg": "GOT", "gothenburg": "GOT", "landvetter": "GOT", "gooteborg": "GOT",
    "malmo": "MMX", "oslo": "OSL", "gardermoen": "OSL", "bergen": "BGO",
    "trondheim": "TRD", "stavanger": "SVG", "aalborg": "AAL", "billund": "BLL",
    "riga": "RIX", "tallinn": "TLL", "vilnius": "VNO", "reykjavik": "KEF",
    "london": "LHR", "amsterdam": "AMS", "frankfurt": "FRA", "munchen": "MUC",
    "munich": "MUC", "berlin": "BER", "hamburg": "HAM", "paris": "CDG",
    "brussels": "BRU", "bryssel": "BRU", "zurich": "ZRH", "vienna": "VIE",
    "wien": "VIE", "warsaw": "WAW", "warszawa": "WAW", "dublin": "DUB",
    "barcelona": "BCN", "madrid": "MAD", "rome": "FCO", "rom": "FCO",
    "milan": "MXP", "milano": "MXP", "lisbon": "LIS", "lissabon": "LIS",
    "prague": "PRG", "prag": "PRG", "budapest": "BUD", "istanbul": "IST",
    "oulu": "OUL", "turku": "TKU", "tampere": "TMP", "rovaniemi": "RVN",
}

# Orter som räknas som "ort i titeln" för mötesfiltret (utöver resans
# destinationer). Hemorten ingår inte.
_TITLE_CITIES = {
    "kopenhamn", "copenhagen", "kobenhavn", "malmo", "lund", "helsingborg",
    "stockholm", "goteborg", "gothenburg", "uppsala", "orebro", "linkoping",
    "vasteras", "norrkoping", "jonkoping", "umea", "sundsvall", "oslo",
    "bergen", "trondheim", "stavanger", "drammen", "aarhus", "arhus", "odense",
    "aalborg", "riga", "tallinn", "vilnius", "london", "amsterdam", "berlin",
    "paris", "madrid", "barcelona",
}

_WEBINAR_RE = re.compile(
    r"live\s?stream|webinar|webinaari|webbinarium|visma talks|\[live",
    re.IGNORECASE,
)
# Mötesrum med platskod, t.ex. "GOT-LIN-19-193", "OSL-SKO-3-Stockholm",
# "HEL-RAU-6-Pixel".
_ROOM_CODE_RE = re.compile(r"\b[A-Z]{3}-[A-Z]{3}-")


# ---------- Hjälpare ----------


def _fold(text: str) -> str:
    """Gemener utan diakritik (ö → o, ø → o, å → a)."""
    text = (text or "").replace("ø", "o").replace("Ø", "O").replace("æ", "ae")
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z]+", _fold(text))


def _etype(ev: dict) -> str:
    return (ev.get("event_type") or "default").replace("_", "").lower()


def city_to_iata(text: str | None) -> str | None:
    """'Copenhagen Kastrup' → CPH, 'HELSINKI HELSINKI VANTAA' → HEL,
    'CPH' → CPH. None om okänd."""
    if not text:
        return None
    raw = text.strip(" .")
    if re.fullmatch(r"[A-Za-z]{3}", raw) and raw.upper() in IATA_INFO:
        return raw.upper()
    for tok in _tokens(raw):
        if tok in _CITY_TOKEN_IATA:
            return _CITY_TOKEN_IATA[tok]
    return None


def iata_name(iata: str | None) -> str | None:
    if not iata:
        return None
    return IATA_INFO.get(iata, (iata, None))[0]


def iata_country(iata: str | None) -> str | None:
    if not iata:
        return None
    return IATA_INFO.get(iata, (None, None))[1]


def home_iatas() -> tuple[str, ...]:
    """Hemflygplatser. Kan överstyras med env TRIP_HOME_IATA (kommasep.)."""
    raw = os.environ.get("TRIP_HOME_IATA", "").strip()
    if raw:
        return tuple(p.strip().upper() for p in raw.split(",") if p.strip())
    return DEFAULT_HOME_IATA


def _homes_to_iata(homes: Iterable[str] | None) -> tuple[str, ...]:
    if homes is None:
        return home_iatas()
    out: list[str] = []
    for h in homes:
        code = city_to_iata(h)
        if code and code not in out:
            out.append(code)
    return tuple(out) or home_iatas()


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


def _ts(value) -> float:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp()


def _sort_key(ev: dict) -> float:
    return _ts(ev["start"])


def _hhmm(value) -> str | None:
    return value.strftime("%H:%M") if isinstance(value, datetime) else None


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


# ---------- Klassificering ----------


def parse_flight(ev: dict) -> dict | None:
    """Returnerar {'origin', 'dest', 'flight_no', 'manual'} eller None.

    Endast Gmail-flyg ("Flight to … (XX nnn)") och manuella
    "Finnair <stad> (IATA) - <stad> (IATA)" räknas som flyg.
    """
    summary = ev.get("summary") or ""
    if _etype(ev) == "fromgmail":
        m = _GMAIL_FLIGHT_RE.match(summary)
        if not m:
            return None
        loc = (ev.get("location") or "").strip()
        tail = _IATA_TAIL_RE.search(loc)
        origin = tail.group(1) if tail else city_to_iata(loc)
        return {
            "origin": origin,
            "dest": city_to_iata(m.group("dest")),
            "dest_text": m.group("dest").strip(),
            "flight_no": f"{m.group('no').upper()} {m.group('num')}",
            "manual": False,
        }
    m = _FINNAIR_RE.match(summary)
    if m:
        return {
            "origin": m.group("o"),
            "dest": m.group("d"),
            "dest_text": m.group("d_city").strip(),
            "flight_no": None,
            "manual": True,
        }
    return None


def is_flight(ev: dict) -> bool:
    return parse_flight(ev) is not None


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
    if _etype(ev) in _IGNORED_EVENT_TYPES:
        return "ignored"
    return "meeting"


def flight_destination(ev: dict) -> str | None:
    """Svenskt stadsnamn för flygets destination (eller None)."""
    info = parse_flight(ev)
    if not info:
        return None
    if info["dest"]:
        return iata_name(info["dest"])
    return info["dest_text"] or None


def hotel_name(ev: dict) -> str:
    summary = (ev.get("summary") or "").strip()
    m = _STAY_NAME_RE.match(summary)
    return m.group(1).strip() if m else summary


# ---------- Mötesfilter ----------


def _title_has_city(summary: str, extra: Iterable[str] = ()) -> bool:
    toks = set(_tokens(summary))
    cities = _TITLE_CITIES | {_fold(c) for c in extra if c}
    return bool(toks & cities)


def is_relevant_meeting(ev: dict, trip_cities: Iterable[str] = ()) -> bool:
    """Ska eventet listas som möte på en resdag?

    Tas bort: avböjda, webinarier/live streams, transparent/free,
    privata utan andra deltagare som tackat ja, obesvarade (needsAction)
    och egna event utan andra deltagare.
    Tas med: event med ort i titeln, mötesrum med platskod
    (GOT-LIN, OSL-SKO, HEL-RAU …) och accepterade event med andra
    deltagare.
    """
    summary = ev.get("summary") or ""
    location = ev.get("location") or ""
    response = ev.get("self_response")
    others = int(ev.get("attendees_other") or 0)
    others_yes = int(ev.get("attendees_other_accepted") or 0)

    if response == "declined":
        return False
    if _WEBINAR_RE.search(summary) or ev.get("is_stream"):
        return False
    if (ev.get("transparency") or "opaque") == "transparent":
        return False
    if (ev.get("visibility") or "") in ("private", "confidential") and others_yes == 0:
        return False
    if _title_has_city(summary, trip_cities):
        return True
    if response == "needsAction":
        return False
    if _ROOM_CODE_RE.search(location) or ev.get("has_room"):
        return True
    return others > 0


# ---------- Gruppering ----------


def _dedupe_flights(flights: list[tuple[dict, dict]]) -> list[tuple[dict, dict]]:
    """Slå ihop manuella Finnair-event med Gmail-flyg som har samma
    start/slut. Gmail-flyget behålls; det manuella id:t sparas i
    `duplicate_ids`."""
    gmail = [(ev, info) for ev, info in flights if not info["manual"]]
    out: list[tuple[dict, dict]] = []
    for ev, info in flights:
        if info["manual"]:
            twin = next(
                (g for g, _ in gmail
                 if _ts(g["start"]) == _ts(ev["start"])
                 and _ts(g["end"]) == _ts(ev["end"])),
                None,
            )
            if twin is not None:
                twin.setdefault("_duplicate_ids", []).append(ev["id"])
                continue
        else:
            dup = next(
                (o for o, oi in out
                 if not oi["manual"] and _ts(o["start"]) == _ts(ev["start"])
                 and _ts(o["end"]) == _ts(ev["end"])
                 and oi["flight_no"] == info["flight_no"]),
                None,
            )
            if dup is not None:
                dup.setdefault("_duplicate_ids", []).append(ev["id"])
                continue
        out.append((ev, info))
    return out


def _new_trip(f: dict, info: dict) -> dict:
    return {
        "flights": [f],
        "outbound": [(f, info)],
        "alternatives": [],
        "return_flight": None,
        "flags": [],
    }


def _close(trip: dict, trips: list[dict], flag: str | None = None) -> None:
    if flag and flag not in trip["flags"]:
        trip["flags"].append(flag)
    trips.append(trip)


def _group_flights(
    flights: list[tuple[dict, dict]], homes: tuple[str, ...],
) -> tuple[list[dict], list[dict]]:
    """Gruppera sorterade flyg till rå-resor. Returnerar (resor, ungrouped)."""
    trips: list[dict] = []
    ungrouped: list[dict] = []
    current: dict | None = None

    for f, info in flights:
        dest_home = info["dest"] in homes if info["dest"] else None
        origin_home = info["origin"] in homes if info["origin"] else None

        if current is not None:
            last = current["flights"][-1]
            if (_start_date(f) - _end_date(last)).days > MAX_TRIP_DAYS:
                _close(current, trips, FLAG_MISSING_RETURN)
                current = None

        if current is None:
            if dest_home is True:
                ungrouped.append({"event": f, "reason": "return_without_departure"})
                continue
            current = _new_trip(f, info)
            continue

        if dest_home is True:
            current["flights"].append(f)
            current["return_flight"] = f
            _close(current, trips)
            current = None
            continue

        if origin_home is True:
            last_out, last_info = current["outbound"][-1]
            if (
                _start_date(f) == _start_date(last_out)
                and info["dest"] == last_info["dest"]
            ):
                # Två utresor samma dag till samma ort: alternativ, inte
                # två resor. Slås inte ihop tyst.
                current["flights"].append(f)
                current["alternatives"].append((f, info))
                if FLAG_NEEDS_REVIEW not in current["flags"]:
                    current["flags"].append(FLAG_NEEDS_REVIEW)
                continue
            # Ny utresa hemifrån: Mikko var hemma emellan.
            _close(current, trips, FLAG_MISSING_RETURN)
            current = _new_trip(f, info)
            continue

        # Anslutning (CPH → OSL) eller okänd avreseort.
        current["flights"].append(f)
        current["outbound"].append((f, info))
        if info["dest"] is None and FLAG_NEEDS_REVIEW not in current["flags"]:
            current["flags"].append(FLAG_NEEDS_REVIEW)

    if current is not None:
        _close(current, trips, FLAG_MISSING_RETURN)
    trips.sort(key=lambda t: _sort_key(t["flights"][0]))
    return trips, ungrouped


def _covers(ev: dict, d: date) -> bool:
    """Gäller hotell-eventet natten som börjar dag d?"""
    s = _start_date(ev)
    last_night = max(s, _end_date(ev) - timedelta(days=1))
    return s <= d <= last_night


def _meeting_on(ev: dict, d: date) -> bool:
    return _start_date(ev) <= d <= _end_date(ev)


def _meeting_dict(ev: dict) -> dict:
    return {
        "id": ev["id"],
        "summary": (ev.get("summary") or "").strip(),
        "start_time": _hhmm(ev["start"]),
        "end_time": _hhmm(ev["end"]),
        "location": ev.get("location") or "",
    }


def _flight_dict(f: dict) -> dict:
    info = parse_flight(f) or {}
    return {
        "id": f["id"],
        "summary": f.get("summary") or "",
        "start": _iso(f["start"]),
        "end": _iso(f["end"]),
        "origin": info.get("origin"),
        "destination_iata": info.get("dest"),
        "destination": flight_destination(f),
        "flight_no": info.get("flight_no"),
        "duplicate_ids": list(f.get("_duplicate_ids") or []),
    }


def _build_trip(
    raw: dict,
    hotels: list[dict],
    meetings: list[dict],
    next_start: date | None,
) -> dict:
    flights = raw["flights"]
    first = flights[0]
    ret = raw["return_flight"]
    start_date = _start_date(first)

    span_end = _end_date(ret) if ret else _end_date(flights[-1])
    trip_hotels = [
        h for h in hotels
        if _start_date(h) <= span_end and _end_date(h) >= start_date
    ]
    if ret:
        end_date = _end_date(ret)
    else:
        # Saknad hemresa: förläng genom sista hotellnatten, men aldrig
        # in i nästa resa.
        end_date = max([span_end] + [_end_date(h) for h in trip_hotels])
        if next_start is not None and end_date >= next_start:
            end_date = max(start_date, next_start - timedelta(days=1))

    outbound = [
        (f, iata_name(i["dest"]) if i["dest"] else (i["dest_text"] or None), i["dest"])
        for f, i in raw["outbound"]
    ]
    dests: list[str] = []
    for _, name, _code in outbound:
        if name and name not in dests:
            dests.append(name)
    countries: list[str] = []
    for _, _name, code in outbound:
        c = iata_country(code)
        if c and c not in countries:
            countries.append(c)

    trip_cities = list(dests)
    day_meetings_all = [m for m in meetings if is_relevant_meeting(m, trip_cities)]

    days = []
    d = start_date
    while d <= end_date:
        day_dest = None
        for f, name, _code in outbound:
            if _start_date(f) <= d and name:
                day_dest = name
        day_flights = [f for f in flights if _start_date(f) == d]
        day_hotel = next((h for h in trip_hotels if _covers(h, d)), None)
        day_meetings = [m for m in day_meetings_all if _meeting_on(m, d)]

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

    all_ids: list[str] = []
    for f in flights:
        all_ids.append(f["id"])
        all_ids.extend(f.get("_duplicate_ids") or [])
    all_ids.extend(h["id"] for h in trip_hotels)
    for day in days:
        for sid in day["source_event_ids"]:
            if sid not in all_ids:
                all_ids.append(sid)

    flight_options = []
    if raw["alternatives"]:
        first_out = raw["outbound"][0][0]
        flight_options = [_flight_dict(first_out)] + [
            _flight_dict(f) for f, _ in raw["alternatives"]
        ]

    flags = list(raw["flags"])
    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_at": _iso(first["start"]),
        "end_at": _iso(ret["end"]) if ret else None,
        "destination": " → ".join(dests) if dests else None,
        "country": countries[0] if len(countries) == 1 else (
            ",".join(countries) if countries else None
        ),
        "open_return": FLAG_MISSING_RETURN in flags,
        "flags": flags,
        "flights": [_flight_dict(f) for f in flights],
        "flight_options": flight_options,
        "hotels": [{"id": h["id"], "name": hotel_name(h)} for h in trip_hotels],
        "days": days,
        "source_event_ids": all_ids,
    }


def detect_trips(events: Iterable[dict], homes: Iterable[str] | None = None) -> dict:
    """Huvudfunktion. Returnerar {'trips', 'ungrouped', 'stats'}."""
    home_codes = _homes_to_iata(homes)
    evs = sorted((e for e in events if e and e.get("start") is not None), key=_sort_key)

    flights: list[tuple[dict, dict]] = []
    hotels, meetings = [], []
    for ev in evs:
        info = parse_flight(ev)
        if info is not None:
            flights.append((dict(ev), info))
            continue
        kind = classify(ev)
        if kind == "hotel":
            hotels.append(ev)
        elif kind == "meeting":
            meetings.append(ev)

    flights = _dedupe_flights(flights)
    raw_trips, ungrouped_raw = _group_flights(flights, home_codes)
    trips = []
    for i, r in enumerate(raw_trips):
        nxt = _start_date(raw_trips[i + 1]["flights"][0]) if i + 1 < len(raw_trips) else None
        trips.append(_build_trip(r, hotels, meetings, nxt))

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
            "missing_return": sum(1 for t in trips if FLAG_MISSING_RETURN in t["flags"]),
            "needs_review": sum(1 for t in trips if FLAG_NEEDS_REVIEW in t["flags"]),
            "ungrouped": len(ungrouped),
        },
    }
