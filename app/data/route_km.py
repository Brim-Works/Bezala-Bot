"""C54 - rutttabell för markbunden transport (km mellan två kända platser).

Tabellen ligger i `route_km.json` (läsbar för Mikko, exporteras via
export_trip_legs(format='route_table')). Nyckel = (operatör, från, till,
symmetrisk). Värden härifrån märks alltid is_estimate=true och
source='route_table'. Kvittots egen km vinner alltid över tabellen.

Platsnamn är engelska/ASCII (de exporteras): 'Skoyen', 'Malmo C'.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_PATH = Path(__file__).with_name("route_km.json")

# Kanoniskt namn -> alias (gemener, utan diakritiska tecken).
PLACE_ALIASES: dict[str, tuple[str, ...]] = {
    "Oslo Airport": ("oslo lufthavn", "oslo airport", "gardermoen", "osl airport"),
    "Skoyen": ("skoyen", "sk yen", "skoeyen"),
    "Oslo S": ("oslo s ", "oslo sentralstasjon", "oslo central"),
    "Nationaltheatret": ("nationaltheatret", "nasjonaltheatret"),
    "Lysaker": ("lysaker",),
    "Arlanda Airport": ("arlanda",),
    "Stockholm C": ("stockholm c", "stockholm central", "stockholm"),
    "Copenhagen Airport": ("cph airport", "cph kastrup", "kastrup", "copenhagen airport",
                           "kobenhavns lufthavn", "cph"),
    "Malmo C": ("malmo c", "malmo"),
    "Helsingborg C": ("helsingborg",),
    "Angelholm": ("angelholm",),
    "Bastad": ("bastad",),
    "Alvesta": ("alvesta",),
    "Ljungby": ("ljungby",),
    "Lund C": ("lund c", "lund "),
}

AIRPORTS = {"Oslo Airport", "Arlanda Airport", "Copenhagen Airport"}

# Operatörens ord (tas bort före platssökning: 'Arlanda Express' är ingen plats).
OPERATORS: dict[str, tuple[str, ...]] = {
    "flytoget": ("flytoget", "airport express train", "flytoget.no"),
    "arlanda_express": ("arlanda express", "arlandaexpress", "arlanda-express"),
    "skanetrafiken": ("skanetrafiken", "oresundstag", "oresundstrain"),
}

# Platser operatören alltid trafikerar (den andra änden får komma från kvittot).
OPERATOR_FIXED: dict[str, tuple[str, ...]] = {
    "flytoget": ("Oslo Airport",),
    "arlanda_express": ("Arlanda Airport", "Stockholm C"),
}

_ROUND_TRIP = ("tur och retur", "tur-retur", "tur/retur", "round trip", "return ticket",
               "edestakainen", "t/r")


def fold(text: str | None) -> str:
    """Gemener utan diakritiska tecken: 'Skøyen' -> 'skoyen', 'Malmö' -> 'malmo'."""
    t = (text or "").lower().replace("ø", "o").replace("æ", "ae").replace("đ", "d")
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.replace("�", " ")


def ascii_text(text):
    """Exporttext: bara ASCII och vanligt bindestreck."""
    if not isinstance(text, str):
        return text
    t = text.replace("–", "-").replace("—", "-").replace("→", "-").replace("ø", "o") \
        .replace("Ø", "O").replace("æ", "ae").replace("Æ", "Ae")
    t = unicodedata.normalize("NFKD", t)
    return "".join(c for c in t if not unicodedata.combining(c)).encode("ascii", "ignore").decode()


@lru_cache(maxsize=1)
def load_table() -> dict:
    with _PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


def routes() -> list[dict]:
    return list(load_table()["routes"])


def operator_of(text: str | None) -> str | None:
    """C59 - operatörens eget namn vinner över allmänna ord: 'Arlanda Express
    ... airport express train' är Arlanda Express, inte Flytoget."""
    t = fold(text)
    best, best_len = None, 0
    for op, words in OPERATORS.items():
        for w in words:
            if w in t and len(w) > best_len and w != "airport express train":
                best, best_len = op, len(w)
    if best:
        return best
    for op, words in OPERATORS.items():
        if any(w in t for w in words):
            return op
    return None


def is_round_trip(text: str | None) -> bool:
    t = fold(text)
    return any(w in t for w in _ROUND_TRIP)


def places_in(text: str | None) -> list[str]:
    """Kanoniska platser i textordning (utan dubbletter)."""
    t = f" {fold(text)} "
    for words in OPERATORS.values():
        for w in words:
            t = t.replace(w, " ")
    t = re.sub(r"[_./\\-]", " ", t)
    t = re.sub(r"\s+", " ", t)
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for canon, aliases in PLACE_ALIASES.items():
        for a in sorted(aliases, key=len, reverse=True):
            for m in re.finditer(rf"(?<![a-z]){re.escape(a.strip())}(?![a-z])", t):
                span = (m.start(), m.end())
                if any(s < span[1] and span[0] < e for s, e in taken):
                    continue
                taken.append(span)
                found.append((m.start(), canon))
    out: list[str] = []
    for _, canon in sorted(found):
        if canon not in out:
            out.append(canon)
    return out


def direction_from(fields: list[str | None]) -> tuple[str | None, str | None, list[str]]:
    """(från, till, alla platser). Första fält med två platser ger riktningen
    (filnamn före ämne före beskrivning)."""
    seen: list[str] = []
    for f in fields:
        p = places_in(f)
        for x in p:
            if x not in seen:
                seen.append(x)
        if len(p) >= 2:
            return p[0], p[1], seen
    return None, None, seen


def lookup_km(frm: str | None, to: str | None, operator: str | None = None) -> dict | None:
    """Tabellrad för (från, till) - operatörens först, sedan vilken som helst."""
    if not frm or not to:
        return None
    hits = []
    for r in routes():
        if r.get("km") is None:
            continue
        same = r["from"] == frm and r["to"] == to
        rev = r.get("symmetric") and r["from"] == to and r["to"] == frm
        if same or rev:
            hits.append(r)
    if not hits:
        return None
    for r in hits:
        if operator and r.get("operator") == operator:
            return r
    return hits[0]
