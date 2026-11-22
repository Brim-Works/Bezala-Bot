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

Destination normaliseras via IATA-kod till kanoniskt engelskt namn
(CPH Copenhagen, ARN Stockholm, GOT Gothenburg, OSL Oslo, HEL Helsinki).

C41 — restid och arbetsort:
  * Hemänden får marginal (env, minuter): resan börjar
    TRIP_HOME_AIRPORT_BEFORE_DEPARTURE_MIN (60) + TRIP_HOME_DRIVE_MIN (40)
    före avgång från HEL och slutar TRIP_HOME_AIRPORT_AFTER_LANDING_MIN
    (20) + TRIP_HOME_DRIVE_MIN (40) efter landning på HEL. Ingen marginal
    på utlandsänden. Flygtiderna sparas oförändrade bredvid.
  * Flygplats ≠ arbetsort: för flygplatser i TRIP_AIRPORT_DEFAULT_COUNTRY
    (JSON, default {"CPH": "SE"}) härleds orten per dag i ordning:
      0. Mikkos egna heldagsevent med bara ett ortnamn ("Malmö")
      1. ort i mötestitel eller plats
      2. mötesrum (prefix + kontor, t.ex. MAL-OAS, HEL-FAB)
      3. hotell-event (namn eller adress)
      4. kvitton samma dag (skickas in av store)
    Kandidater: TRIP_CPH_KNOWN_CITIES (Malmö, Helsingborg, Växjö, Ljungby)
    → Sweden, eller orter i flygplatsens eget land (Denmark). Dag utan
    ledtråd ärver grannens ort; finns ingen alls blir orten tom, landet
    default och resan flaggas `destination_unconfirmed`.

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
FLAG_DESTINATION_UNCONFIRMED = "destination_unconfirmed"
# C44 — hotell-event utan flyg (inrikesresa?) — Mikko bekräftar.
FLAG_DOMESTIC_UNCONFIRMED = "domestic_unconfirmed"

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

# IATA → (kanoniskt engelskt namn, landskod). C41: engelska exonymer
# (Copenhagen, Gothenburg, Helsinki) — svenska visas bara i UI.
IATA_INFO: dict[str, tuple[str, str]] = {
    "HEL": ("Helsinki", "FI"), "CPH": ("Copenhagen", "DK"),
    "ARN": ("Stockholm", "SE"), "BMA": ("Stockholm", "SE"),
    "GOT": ("Gothenburg", "SE"), "MMX": ("Malmö", "SE"),
    "OSL": ("Oslo", "NO"), "BGO": ("Bergen", "NO"), "TRD": ("Trondheim", "NO"),
    "SVG": ("Stavanger", "NO"), "AAL": ("Aalborg", "DK"), "BLL": ("Billund", "DK"),
    "AAR": ("Aarhus", "DK"), "RIX": ("Riga", "LV"), "TLL": ("Tallinn", "EE"),
    "VNO": ("Vilnius", "LT"), "KEF": ("Reykjavik", "IS"),
    "LHR": ("London", "GB"), "LGW": ("London", "GB"), "STN": ("London", "GB"),
    "AMS": ("Amsterdam", "NL"), "FRA": ("Frankfurt", "DE"), "MUC": ("Munich", "DE"),
    "BER": ("Berlin", "DE"), "HAM": ("Hamburg", "DE"), "CDG": ("Paris", "FR"),
    "ORY": ("Paris", "FR"), "BRU": ("Brussels", "BE"), "ZRH": ("Zurich", "CH"),
    "VIE": ("Vienna", "AT"), "WAW": ("Warsaw", "PL"), "DUB": ("Dublin", "IE"),
    "BCN": ("Barcelona", "ES"), "MAD": ("Madrid", "ES"), "FCO": ("Rome", "IT"),
    "MXP": ("Milan", "IT"), "LIS": ("Lisbon", "PT"), "PRG": ("Prague", "CZ"),
    "BUD": ("Budapest", "HU"), "IST": ("Istanbul", "TR"),
    "OUL": ("Oulu", "FI"), "TKU": ("Turku", "FI"), "TMP": ("Tampere", "FI"),
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

# C41 — mötesrum: prefix + kontor (två första delarna) → (ort, land).
# "HEL" ensamt räcker inte: HEL-RAU är Helsingfors (Rautatalo) men
# HEL-FAB är Helsingborg.
ROOM_OFFICES: dict[str, tuple[str, str]] = {
    "MAL-OAS": ("Malmö", "SE"),
    "HEL-FAB": ("Helsingborg", "SE"),
    "VXO-SAM": ("Växjö", "SE"),
    "LBY-LHQ": ("Ljungby", "SE"),
    "GOT-LIN": ("Gothenburg", "SE"),
    "STO-BLH": ("Stockholm", "SE"),
    "OSL-SKO": ("Oslo", "NO"),
    "HEL-RAU": ("Helsinki", "FI"),
}
_ROOM_OFFICE_RE = re.compile(r"\b([A-Z]{3}-[A-Z]{3})-")

# C41 — orter som kan vara arbetsort (vikt token → (engelskt namn, land)).
_WORK_CITIES: dict[str, tuple[str, str]] = {
    "malmo": ("Malmö", "SE"), "helsingborg": ("Helsingborg", "SE"),
    "vaxjo": ("Växjö", "SE"), "ljungby": ("Ljungby", "SE"), "lund": ("Lund", "SE"),
    "stockholm": ("Stockholm", "SE"), "goteborg": ("Gothenburg", "SE"),
    "gothenburg": ("Gothenburg", "SE"),
    "copenhagen": ("Copenhagen", "DK"), "kobenhavn": ("Copenhagen", "DK"),
    "kopenhamn": ("Copenhagen", "DK"), "aarhus": ("Aarhus", "DK"),
    "arhus": ("Aarhus", "DK"), "odense": ("Odense", "DK"),
    "aalborg": ("Aalborg", "DK"), "roskilde": ("Roskilde", "DK"),
    "helsingor": ("Helsingør", "DK"), "billund": ("Billund", "DK"),
    "oslo": ("Oslo", "NO"), "helsinki": ("Helsinki", "FI"),
    "helsingfors": ("Helsinki", "FI"),
}

DEFAULT_AIRPORT_COUNTRY = {"CPH": "SE"}
_TRAVEL_RECEIPT_RE = re.compile(
    r"finnair|\bsas\b|norwegian|lufthansa|klm|finavia|moovy|airport|"
    r"lufthavn|kastrup|flygplats|lentoasema|aimo park|europark",
    re.IGNORECASE,
)
DEFAULT_CPH_KNOWN_CITIES = ("Malmö", "Helsingborg", "Växjö", "Ljungby")
# C44 — hemresa som går före 18:00 → ingen middag på plats den dagen.
EVENING_HOUR = 18


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def travel_margins() -> tuple[int, int, int]:
    """(före avgång på flygplatsen, bilresa, efter landning) i minuter."""
    return (
        _env_int("TRIP_HOME_AIRPORT_BEFORE_DEPARTURE_MIN", 60),
        _env_int("TRIP_HOME_DRIVE_MIN", 40),
        _env_int("TRIP_HOME_AIRPORT_AFTER_LANDING_MIN", 20),
    )


def airport_default_country() -> dict[str, str]:
    """TRIP_AIRPORT_DEFAULT_COUNTRY='{"CPH":"SE"}' (JSON, går att utöka)."""
    import json

    raw = os.environ.get("TRIP_AIRPORT_DEFAULT_COUNTRY", "").strip()
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return {str(k).upper(): str(v).upper() for k, v in data.items()}
        except ValueError:
            pass
    return dict(DEFAULT_AIRPORT_COUNTRY)


def known_cities(airport: str) -> list[str]:
    """Kända arbetsorter för en flygplats (TRIP_<IATA>_KNOWN_CITIES)."""
    raw = os.environ.get(f"TRIP_{airport}_KNOWN_CITIES", "").strip()
    if raw:
        return [c.strip() for c in raw.split(",") if c.strip()]
    return list(DEFAULT_CPH_KNOWN_CITIES) if airport == "CPH" else []


def _candidates(airport: str) -> dict[str, tuple[str, str]]:
    """Vikt token → (ort, land) som får bli arbetsort för flygplatsen:
    kända orter (→ flygplatsens default-land) + orter i flygplatsens eget
    land (t.ex. en annan dansk ort → Denmark)."""
    default_cc = airport_default_country().get(airport) or iata_country(airport)
    own_cc = iata_country(airport)
    known = {_fold(c) for c in known_cities(airport)}
    out: dict[str, tuple[str, str]] = {}
    for tok, (city, cc) in _WORK_CITIES.items():
        if _fold(city) in known or tok in known:
            out[tok] = (city, default_cc or cc)
        elif cc == own_cc:
            out[tok] = (city, cc)
    for name in known_cities(airport):
        tok = _fold(name)
        if tok not in out:
            out[tok] = (name, default_cc or "")
    return out


def _cities_in(text: str, cands: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    out = []
    for tok in _tokens(text):
        if tok in cands and cands[tok] not in out:
            out.append(cands[tok])
    return out


def _room_cities(location: str, cands: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    out = []
    names = {c for c, _ in cands.values()}
    for code in _ROOM_OFFICE_RE.findall(location or ""):
        office = ROOM_OFFICES.get(code)
        if office and office[0] in names:
            hit = next(v for v in cands.values() if v[0] == office[0])
            if hit not in out:
                out.append(hit)
    return out


def _pick(hits: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Vanligaste orten (vid lika: den som kom först)."""
    if not hits:
        return None
    counts: dict[tuple[str, str], int] = {}
    for h in hits:
        counts[h] = counts.get(h, 0) + 1
    return max(counts, key=lambda h: (counts[h], -hits.index(h)))


def is_city_marker(ev: dict, cands: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    """Mikkos eget heldagsevent med bara ett ortnamn som titel ("Malmö")."""
    if not ev.get("all_day"):
        return None
    if not (ev.get("organizer_self") or int(ev.get("attendees_other") or 0) == 0):
        return None
    toks = _tokens(ev.get("summary") or "")
    if len(toks) != 1 or toks[0] not in cands:
        return None
    return cands[toks[0]]


def resolve_work_city(
    day: date,
    airport: str,
    day_meetings: list[dict],
    all_events: list[dict],
    hotels: list[dict],
    receipt_texts: list[str] = (),
) -> tuple[tuple[str, str] | None, str, bool]:
    """Härled arbetsort för en dag. Returnerar ((ort, land) | None,
    källa, på_kontoret)."""
    cands = _candidates(airport)
    markers = [
        m for m in (is_city_marker(e, cands) for e in all_events
                    if _start_date(e) <= day <= _end_date(e))
        if m
    ]
    room_hits = []
    for m in day_meetings:
        room_hits += _room_cities(m.get("location") or "", cands)
    hit = _pick(markers)
    if hit:
        return hit, "calendar_marker", hit in room_hits
    title_hits = []
    for m in day_meetings:
        title_hits += _cities_in(f"{m.get('summary') or ''} {m.get('location') or ''}", cands)
    hit = _pick(title_hits)
    if hit:
        return hit, "meeting_title", hit in room_hits
    hit = _pick(room_hits)
    if hit:
        return hit, "meeting_room", True
    hotel_hits = []
    for h in hotels:
        s = _start_date(h)
        last_night = max(s, _end_date(h) - timedelta(days=1))
        if s <= day <= last_night + timedelta(days=1):
            hotel_hits += _cities_in(f"{h.get('summary') or ''} {h.get('location') or ''}", cands)
    hit = _pick(hotel_hits)
    if hit:
        return hit, "hotel", False
    receipt_hits = []
    for text in receipt_texts:
        # Flyg-, flygplats- och parkeringskvitton nämner flygplatsens stad
        # (Finnair "Helsinki – Copenhagen") — inte arbetsorten.
        if _TRAVEL_RECEIPT_RE.search(text or ""):
            continue
        receipt_hits += _cities_in(text, cands)
    hit = _pick(receipt_hits)
    if hit:
        return hit, "receipt", False
    return None, "unconfirmed", False


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
    """Engelskt stadsnamn för flygets destination (eller None)."""
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
        **({"rebooked_from": f["_rebooked_from"]} if f.get("_rebooked_from") else {}),
    }


def _helsinki():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Europe/Helsinki")


def _aware_dt(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def travel_times(
    first_flight_start, first_origin: str | None,
    return_flight_end, homes: tuple[str, ...],
) -> tuple[datetime | None, datetime | None]:
    """Resans start/slut (Helsingfors-tid) med marginal på hemänden.

    Start = avgång − (före avgång + bilresa) om flyget går från hemorten
    (eller avreseort saknas — resans första flyg utgår hemifrån).
    Slut = landning + (efter landning + bilresa). Ingen hemresa → None.
    """
    before, drive, after = travel_margins()
    hel = _helsinki()
    start = _aware_dt(first_flight_start)
    if start is not None:
        if first_origin is None or first_origin in homes:
            start = start - timedelta(minutes=before + drive)
        start = start.astimezone(hel)
    end = _aware_dt(return_flight_end)
    if end is not None:
        end = (end + timedelta(minutes=after + drive)).astimezone(hel)
    return start, end


def _build_trip(
    raw: dict,
    hotels: list[dict],
    meetings: list[dict],
    next_start: date | None,
    homes: tuple[str, ...] = DEFAULT_HOME_IATA,
    receipts_by_date: dict[str, list[str]] | None = None,
) -> dict:
    from app.services.trip_english import combine_purposes, generate_purpose
    from app.services.trip_meals import meals_for_day

    flights = raw["flights"]
    first = flights[0]
    ret = raw["return_flight"]
    first_origin = raw["outbound"][0][1].get("origin")
    travel_start, travel_end = travel_times(
        first["start"], first_origin, ret["end"] if ret else None, homes,
    )
    start_date = _start_date(first)
    if travel_start is not None:
        start_date = min(start_date, travel_start.date())

    span_end = _end_date(ret) if ret else _end_date(flights[-1])
    trip_hotels = [
        h for h in hotels
        if _start_date(h) <= span_end and _end_date(h) >= start_date
    ]
    if ret:
        end_date = _end_date(ret)
        if travel_end is not None:
            end_date = max(end_date, travel_end.date())
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
    airport_cities = [name for _, name, _ in outbound if name]
    day_meetings_all = [m for m in meetings if is_relevant_meeting(m, airport_cities)]
    # Möten före landning på utresan / efter avgång på hemresan hålls
    # hemifrån (distans) — de räknas inte för arbetsort eller syfte.
    landed = _aware_dt(raw["outbound"][0][0]["end"])
    leaves = _aware_dt(ret["start"]) if ret else None

    def _on_site(m: dict) -> bool:
        ms, me = _aware_dt(m["start"]), _aware_dt(m["end"])
        if ms is None or me is None:
            return True  # heldagsevent
        if landed is not None and me <= landed:
            return False
        if leaves is not None and ms >= leaves:
            return False
        return True
    inferred_airports = airport_default_country()

    days = []
    d = start_date
    while d <= end_date:
        airport, airport_city = None, None
        for f, name, code in outbound:
            if _start_date(f) <= d:
                airport, airport_city = code, name
        if airport is None and outbound:
            airport, airport_city = outbound[0][2], outbound[0][1]
        day_flights = [f for f in flights if _start_date(f) == d]
        day_hotel = next((h for h in trip_hotels if _covers(h, d)), None)
        prev_hotel = next((h for h in trip_hotels if _covers(h, d - timedelta(days=1))), None)
        day_meetings = [m for m in day_meetings_all if _meeting_on(m, d)]
        on_site = [m for m in day_meetings if _on_site(m)]

        if airport in inferred_airports:
            hit, source, office = resolve_work_city(
                d, airport, on_site, meetings, trip_hotels,
                (receipts_by_date or {}).get(d.isoformat(), []),
            )
            city, country = hit if hit else (None, None)
        else:
            source = "airport"
            city, country = airport_city, iata_country(airport)
            office = False
            if city:
                office = any(
                    ROOM_OFFICES.get(code, ("",))[0] == city
                    for m in on_site
                    for code in _ROOM_OFFICE_RE.findall(m.get("location") or "")
                )

        source_ids = [f["id"] for f in day_flights]
        if day_hotel:
            source_ids.append(day_hotel["id"])
        source_ids.extend(m["id"] for m in day_meetings)

        days.append({
            "date": d.isoformat(),
            "start_time": (
                travel_start.strftime("%H:%M")
                if travel_start is not None and d == travel_start.date() else None
            ),
            "end_time": (
                travel_end.strftime("%H:%M")
                if travel_end is not None and d == travel_end.date() else None
            ),
            "airport": airport,
            "destination": city,
            "country": country,
            "destination_source": source,
            "_office": office,
            "_meetings_raw": on_site,
            "_prev_hotel": hotel_name(prev_hotel) if prev_hotel else None,
            # C44 — möten före avgång / efter hemkomst (hålls hemifrån)
            # listas inte på resdagen.
            "meetings": [_meeting_dict(m) for m in on_site],
            "hotel": (
                {"id": day_hotel["id"], "name": hotel_name(day_hotel)}
                if day_hotel else None
            ),
            "source_event_ids": source_ids,
        })
        d += timedelta(days=1)

    # Dag utan ledtråd ärver närmaste dag med samma flygplats (föregående
    # först). Finns ingen alls: tom ort, default-land, flagga.
    flags = list(raw["flags"])
    for i, day in enumerate(days):
        if day["destination_source"] != "unconfirmed":
            continue
        same = [x for x in days if x["airport"] == day["airport"]
                and x["destination_source"] not in ("unconfirmed", "carried")]
        prev = [x for x in same if x["date"] < day["date"]]
        nxt = [x for x in same if x["date"] > day["date"]]
        src = prev[-1] if prev else (nxt[0] if nxt else None)
        if src is not None:
            day["destination"], day["country"] = src["destination"], src["country"]
            day["destination_source"] = "carried"
        else:
            day["country"] = inferred_airports.get(day["airport"]) or iata_country(day["airport"])
            if FLAG_DESTINATION_UNCONFIRMED not in flags:
                flags.append(FLAG_DESTINATION_UNCONFIRMED)

    # C44 — fria måltider. Alla event på plats (inte bara "relevanta"
    # möten: en kick-off kan vara ett heldagsevent utan deltagare).
    on_site_all = [
        m for m in meetings
        if m.get("self_response") != "declined" and _on_site(m)
    ]
    # Hemresans avgång i LOKAL tid på avreseflygplatsen (CPH 17:00 = HEL
    # 18:00): flyger Mikko hem före kvällen blir det ingen middag på plats.
    ret_leaves_hel = None
    if leaves is not None:
        from app.services.receipt_details import _tz

        ret_origin = (parse_flight(ret) or {}).get("origin") if ret else None
        ret_leaves_hel = leaves.astimezone(_tz(ret_origin))
    for day in days:
        dd = date.fromisoformat(day["date"])
        summaries = [m.get("summary") or "" for m in on_site_all if _meeting_on(m, dd)]
        evening_away = not (
            ret_leaves_hel is not None and ret_leaves_hel.date() == dd
            and ret_leaves_hel.hour < EVENING_HOUR
        )
        day["free_meals"], day["free_meal_sources"] = meals_for_day(
            summaries, day.pop("_prev_hotel"), day["country"], evening_away,
        )
        day["meals_source"] = "calendar"

    for day in days:
        day["purpose"] = generate_purpose(
            day.pop("_meetings_raw"), day["destination"], office=day.pop("_office"),
        )

    dests: list[str] = []
    countries: list[str] = []
    for day in days:
        if day["destination"] and day["destination"] not in dests:
            dests.append(day["destination"])
        if day["country"] and day["country"] not in countries:
            countries.append(day["country"])

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

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "start_at": _iso(first["start"]),
        "end_at": _iso(ret["end"]) if ret else None,
        "travel_start_at": _iso(travel_start),
        "travel_end_at": _iso(travel_end),
        "destination": " → ".join(dests) if dests else None,
        "country": ",".join(countries) if countries else None,
        "purpose": combine_purposes([d["purpose"] for d in days], dests),
        "open_return": FLAG_MISSING_RETURN in flags,
        "flags": flags,
        "flights": [_flight_dict(f) for f in flights],
        "flight_options": flight_options,
        "hotels": [{"id": h["id"], "name": hotel_name(h)} for h in trip_hotels],
        "days": days,
        "source_event_ids": all_ids,
    }


def detect_trips(
    events: Iterable[dict],
    homes: Iterable[str] | None = None,
    receipts_by_date: dict[str, list[str]] | None = None,
    rebookings: list[dict] | None = None,
) -> dict:
    """Huvudfunktion. Returnerar {'trips', 'ungrouped', 'stats'}.

    receipts_by_date: {'YYYY-MM-DD': [kvittotext, …]} — kvitton i boten,
    sista ledtråden för arbetsort (C41).
    rebookings: flygsegment ur Finnairs "Bekräftelse på din bokningsändring"
    (C44) — ersätter kalenderns gamla flyg."""
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
    rebooked = apply_rebookings(flights, rebookings or [])
    flights.sort(key=lambda fi: _sort_key(fi[0]))
    raw_trips, ungrouped_raw = _group_flights(flights, home_codes)
    trips = []
    for i, r in enumerate(raw_trips):
        nxt = _start_date(raw_trips[i + 1]["flights"][0]) if i + 1 < len(raw_trips) else None
        trips.append(_build_trip(r, hotels, meetings, nxt, home_codes, receipts_by_date))

    used_hotels = {h["id"] for t in trips for h in t["hotels"]}
    for h in hotels:
        if h["id"] in used_hotels:
            continue
        dom = _domestic_trip(h, meetings, trips)
        if dom is not None:
            trips.append(dom)
        else:
            ungrouped_raw.append({"event": h, "reason": "hotel_without_flight"})
    trips.sort(key=lambda t: t["start_date"])

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
            "destination_unconfirmed": sum(
                1 for t in trips if FLAG_DESTINATION_UNCONFIRMED in t["flags"]
            ),
            "domestic_unconfirmed": sum(
                1 for t in trips if FLAG_DOMESTIC_UNCONFIRMED in t["flags"]
            ),
            "rebooked_flights": rebooked,
            "ungrouped": len(ungrouped),
        },
    }


# ---------- C44: ombokade flyg ----------


def _parse_iso(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _norm_no(value: str | None) -> str | None:
    m = re.match(r"^\s*([A-Z0-9]{2})\s*0*(\d{1,4})\s*$", (value or "").upper())
    return f"{m.group(1)} {m.group(2)}" if m else None


REBOOK_WINDOW_DAYS = 3


def apply_rebookings(flights: list[tuple[dict, dict]], segments: list[dict]) -> int:
    """Ersätt kalenderns flyg med flygen ur Finnairs ombokningsmail.

    Ett segment ersätter ett kalenderflyg med samma avrese- och
    ankomstflygplats inom ±3 dygn när flightnummer eller avgång skiljer
    sig (13 okt: kalendern AY 864 14 okt → AY 868 13 okt 20:55). Segment
    som redan finns i kalendern (samma nummer, samma dag) rör ingenting.
    Ändrar eventen på plats. Returnerar antal ersatta flyg."""
    replaced = 0
    for seg in segments or []:
        dep, arr = _parse_iso(seg.get("dep")), _parse_iso(seg.get("arr"))
        no = _norm_no(seg.get("flight_no"))
        if dep is None or arr is None or not no:
            continue
        if any(
            _norm_no(i.get("flight_no")) == no and _start_date(ev) == dep.date()
            for ev, i in flights
        ):
            continue
        best = None
        for ev, info in flights:
            if ev.get("_rebooked_from"):
                continue  # redan ombokat av ett nyare ändringsmail
            if info.get("origin") != seg.get("origin") or info.get("dest") != seg.get("dest"):
                continue
            start = _aware_dt(ev["start"])
            if start is None:
                continue
            gap = abs((start - dep).total_seconds())
            if gap > REBOOK_WINDOW_DAYS * 86400 or gap < 300 and _norm_no(info.get("flight_no")) == no:
                continue
            if best is None or gap < best[0]:
                best = (gap, ev, info)
        if best is None:
            continue
        _, ev, info = best
        ev["_rebooked_from"] = {
            "flight_no": info.get("flight_no"),
            "start": _iso(ev["start"]),
            "end": _iso(ev["end"]),
        }
        ev["start"], ev["end"] = dep, arr
        summary = ev.get("summary") or ""
        if re.search(r"\([A-Z0-9]{2}\s?\d{1,4}\)", summary):
            ev["summary"] = re.sub(r"\([A-Z0-9]{2}\s?\d{1,4}\)", f"({no})", summary)
        info["flight_no"] = no
        replaced += 1
    return replaced


# ---------- C44: hotell utan flyg ----------


def _domestic_trip(hotel: dict, meetings: list[dict], trips: list[dict]) -> dict | None:
    """Hotell-event utan flyg → resa flaggad 'domestic_unconfirmed' (t.ex.
    NH Helsinki Grand Hansa 19 nov, Scandic Go Saaristonkatu 21 dec).
    Hotell utomlands utan flyg lämnas som ogrupperade."""
    from app.services.trip_meals import meals_for_day

    start = _start_date(hotel)
    end = max(start + timedelta(days=1), _end_date(hotel) + (
        timedelta(days=0) if isinstance(hotel["end"], datetime) else timedelta(days=1)
    ))
    if any(date.fromisoformat(t["start_date"]) <= end and date.fromisoformat(t["end_date"]) >= start
           for t in trips):
        return None
    text = f"{hotel.get('summary') or ''} {hotel.get('location') or ''}"
    code = city_to_iata(text)
    country = iata_country(code) if code else None
    if country not in (None, "FI"):
        return None
    city = iata_name(code) if code else None
    name = hotel_name(hotel)
    days = []
    d = start
    while d <= end:
        day_meetings = [m for m in meetings if _meeting_on(m, d) and is_relevant_meeting(m)]
        summaries = [m.get("summary") or "" for m in meetings
                     if _meeting_on(m, d) and m.get("self_response") != "declined"]
        free, sources = meals_for_day(summaries, name if d > start else None, "FI")
        days.append({
            "date": d.isoformat(),
            "start_time": None,
            "end_time": None,
            "airport": None,
            "destination": city,
            "country": "FI",
            "destination_source": "hotel" if city else "unconfirmed",
            "meetings": [_meeting_dict(m) for m in day_meetings],
            "hotel": {"id": hotel["id"], "name": name} if d < end else None,
            "source_event_ids": [hotel["id"]] + [m["id"] for m in day_meetings],
            "purpose": None,
            "free_meals": free,
            "free_meal_sources": sources,
            "meals_source": "calendar",
        })
        d += timedelta(days=1)
    flags = [FLAG_DOMESTIC_UNCONFIRMED]
    if not city:
        flags.append(FLAG_DESTINATION_UNCONFIRMED)
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "start_at": None,
        "end_at": None,
        "travel_start_at": None,
        "travel_end_at": None,
        "destination": city,
        "country": "FI",
        "purpose": None,
        "open_return": False,
        "flags": flags,
        "flights": [],
        "flight_options": [],
        "hotels": [{"id": hotel["id"], "name": name}],
        "days": days,
        "source_event_ids": [hotel["id"]],
    }
