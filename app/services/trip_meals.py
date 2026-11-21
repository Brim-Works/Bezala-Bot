"""C44 — fria måltider per resdag (ren logik, ingen DB/IO).

Mikkos regler:
  * kick-off (kick-off/kickoff) = middag betald
  * kunddag (kunddag/customer day/asiakaspäivä) = lunch betald
  * kalender-event med dinner/middag/illallinen = betald middag
    (t.ex. "Board #4 … (on site with dinner)")
  * kalender-event med lunch/lounas = betald lunch
  * hotellet har frukost (natten före) utom hotell i
    TRIP_HOTELS_WITHOUT_BREAKFAST (default "Scandic Go")
Skatteförvaltningen: inrikes räknas hotellfrukost aldrig; utrikes styrs av
TRIP_BREAKFAST_COUNTS_AS_MEAL_ABROAD (default false). Frukosten loggas
alltid i källorna men räknas bara när inställningen är true och dagen är
utrikes.
"""

from __future__ import annotations

import os
import re
import unicodedata

SOURCES = (
    "hotel_breakfast", "lunch_customer_day", "dinner_kickoff",
    "dinner_calendar", "lunch_calendar", "ticket",
)
_MEAL_OF = {
    "hotel_breakfast": "breakfast",
    "lunch_customer_day": "lunch",
    "lunch_calendar": "lunch",
    "dinner_kickoff": "dinner",
    "dinner_calendar": "dinner",
    "ticket": "ticket",
}
HOME_COUNTRY = "FI"

_KICKOFF_RE = re.compile(r"\bkick\s?-?\s?off\b")
_CUSTOMER_DAY_RE = re.compile(r"\bkunddag\w*|\bcustomer\s?days?\b|\basiakaspaiv\w*")
_DINNER_RE = re.compile(r"\b(dinner|middag|illallinen|illallis\w*)\b")
_LUNCH_RE = re.compile(r"\b(lunch|lounas)\b")


def _fold(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


def breakfast_counts_abroad() -> bool:
    raw = os.environ.get("TRIP_BREAKFAST_COUNTS_AS_MEAL_ABROAD", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def hotels_without_breakfast() -> list[str]:
    raw = os.environ.get("TRIP_HOTELS_WITHOUT_BREAKFAST")
    if raw is None:
        raw = "Scandic Go"
    return [h.strip() for h in raw.split(",") if h.strip()]


def hotel_has_breakfast(name: str | None) -> bool:
    folded = _fold(name or "")
    return not any(_fold(h) in folded for h in hotels_without_breakfast())


def event_sources(summary: str) -> list[str]:
    """Måltidskällor i en kalendertitel."""
    t = _fold(summary)
    out: list[str] = []
    if _KICKOFF_RE.search(t):
        out.append("dinner_kickoff")
    if _CUSTOMER_DAY_RE.search(t):
        out.append("lunch_customer_day")
    if _DINNER_RE.search(t) and "dinner_kickoff" not in out:
        out.append("dinner_calendar")
    if _LUNCH_RE.search(t) and "lunch_customer_day" not in out:
        out.append("lunch_calendar")
    return out


def count_meals(sources: list[str], country: str | None) -> int:
    """Antal fria måltider (0–3) av källorna. Frukost räknas bara utrikes och
    bara om TRIP_BREAKFAST_COUNTS_AS_MEAL_ABROAD=true. 'ticket' (måltid i
    biljetten, satt manuellt) räknas som en måltid."""
    abroad = bool(country) and HOME_COUNTRY not in (country or "").upper().split(",")
    meals: set[str] = set()
    extra = 0
    for s in sources or []:
        meal = _MEAL_OF.get(s)
        if meal == "breakfast":
            if abroad and breakfast_counts_abroad():
                meals.add("breakfast")
        elif meal == "ticket":
            extra += 1
        elif meal:
            meals.add(meal)
    return min(3, len(meals) + extra)


def meals_for_day(summaries: list[str], prev_night_hotel: str | None,
                  country: str | None, evening_away: bool = True) -> tuple[int, list[str]]:
    """(free_meals, free_meal_sources) för en resdag.

    summaries: titlar på dagens event på plats (ej avböjda).
    prev_night_hotel: hotellnamn för natten före (None = ingen hotellnatt).
    evening_away: False på hemresedagen när Mikko flyger hem före kvällen —
    då räknas ingen middag."""
    sources: list[str] = []
    if prev_night_hotel and hotel_has_breakfast(prev_night_hotel):
        sources.append("hotel_breakfast")
    for s in summaries:
        for src in event_sources(s):
            if src.startswith("dinner") and not evening_away:
                continue
            if src not in sources:
                sources.append(src)
    return count_meals(sources, country), sources


def meal_names(sources: list[str] | None, country: str | None) -> list[str]:
    """Engelska namn på de måltider som räknas (för Netvisor-texten)."""
    abroad = bool(country) and HOME_COUNTRY not in (country or "").upper().split(",")
    out: list[str] = []
    for s in sources or []:
        meal = _MEAL_OF.get(s)
        if meal == "breakfast" and not (abroad and breakfast_counts_abroad()):
            continue
        name = "meal in ticket" if meal == "ticket" else meal
        if name and name not in out:
            out.append(name)
    order = {"breakfast": 0, "lunch": 1, "dinner": 2, "meal in ticket": 3}
    return sorted(out, key=lambda n: order.get(n, 9))


def validate_sources(values) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = str(v).strip()
        if v not in SOURCES:
            raise ValueError(
                f"Unknown free_meal_source '{v}'. Use: {', '.join(SOURCES)}."
            )
        if v not in out:
            out.append(v)
    return out
