"""C41 — engelska värden för allt som går till Netvisor (ren logik, ingen IO).

Databasen och MCP-svaren har kanoniska engelska värden:
  * Stad: engelsk exonym där en sådan finns (Copenhagen, Gothenburg,
    Helsinki), annars ortnamnet (Malmö, Växjö, Ljungby).
  * Land: ISO 3166-1 alpha-2 (SE) + engelskt namn (Sweden).
  * Syfte: alltid engelska.
Svenska visas bara i UI (frontend/src/i18n översätter stad/land).

Här finns också:
  * `generate_purpose` — en mening på engelska ur dagens möten.
  * `non_english_warnings` — varningar (blockerar inte) när syfte eller
    destination ser ut att vara på svenska/finska.
  * `netvisor_line_description` — förberedd Netvisor-text (beräknas,
    sparas inte), t.ex. "Business trip Malmö, Sweden - Customer day Agda PS".
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

COUNTRY_NAMES_EN: dict[str, str] = {
    "FI": "Finland", "SE": "Sweden", "NO": "Norway", "DK": "Denmark",
    "IS": "Iceland", "EE": "Estonia", "LV": "Latvia", "LT": "Lithuania",
    "GB": "United Kingdom", "IE": "Ireland", "NL": "Netherlands",
    "BE": "Belgium", "DE": "Germany", "FR": "France", "CH": "Switzerland",
    "AT": "Austria", "PL": "Poland", "ES": "Spain", "PT": "Portugal",
    "IT": "Italy", "CZ": "Czechia", "HU": "Hungary", "TR": "Turkey",
    "US": "United States",
}

# Svenska (och finska/danska) namn → kanoniskt engelskt namn.
# Används för migrering av gamla rader och för MCP-varningar.
_CITY_EXONYMS: dict[str, str] = {
    "Köpenhamn": "Copenhagen", "København": "Copenhagen", "Kööpenhamina": "Copenhagen",
    "Göteborg": "Gothenburg", "Helsingfors": "Helsinki", "Tukholma": "Stockholm",
    "Göteborgs": "Gothenburg", "Århus": "Aarhus", "München": "Munich",
    "Bryssel": "Brussels", "Wien": "Vienna", "Warszawa": "Warsaw", "Rom": "Rome",
    "Milano": "Milan", "Lissabon": "Lisbon", "Prag": "Prague", "Zürich": "Zurich",
    "Uleåborg": "Oulu", "Åbo": "Turku", "Tammerfors": "Tampere",
    "Rovaniemi": "Rovaniemi", "Reykjavík": "Reykjavik",
}


def _fold(text: str) -> str:
    text = (text or "").replace("ø", "o").replace("Ø", "O").replace("æ", "ae")
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()


_EXONYMS_FOLDED = {_fold(k): v for k, v in _CITY_EXONYMS.items()}


def country_name(code: str | None) -> str | None:
    """'SE' → 'Sweden', 'DK,NO' → 'Denmark, Norway'. Okänd kod → koden."""
    if not code:
        return None
    parts = [c.strip().upper() for c in code.split(",") if c.strip()]
    return ", ".join(COUNTRY_NAMES_EN.get(c, c) for c in parts) or None


def city_to_english(name: str | None) -> str | None:
    """'Köpenhamn' → 'Copenhagen'. Redan engelskt/okänt → oförändrat."""
    if not name:
        return name
    raw = name.strip()
    return _EXONYMS_FOLDED.get(_fold(raw), raw)


def destination_to_english(value: str | None) -> str | None:
    """'Köpenhamn → Oslo' → 'Copenhagen → Oslo'."""
    if not value:
        return value
    return " → ".join(city_to_english(p) or p for p in value.split(" → "))


# ---------- Varningar (blockerar inte) ----------

_NON_ENGLISH_WORDS = {
    # svenska
    "och", "med", "for", "fran", "till", "mote", "moten", "kunddag", "kunddagar",
    "resa", "affarsresa", "tjansteresa", "kontoret", "kontor", "ledningsgrupp",
    "ledartraff", "halvdag", "heldag", "kund", "kunder", "besok", "pa", "att",
    "utbildning", "konferens", "synk", "styrgrupp", "styrgruppsmote", "traff",
    # finska
    "ja", "matka", "tyomatka", "kokous", "asiakas", "asiakaspaiva", "toimisto",
    "palaveri", "koulutus", "johtoryhma", "tapaaminen",
}
_SAFE_WORDS = {_fold(c) for c in (
    "Malmö", "Växjö", "Ljungby", "Helsingborg", "Göteborg", "Köpenhamn",
)}


def non_english_warnings(field: str, value: str | None) -> list[str]:
    """Varna (men blockera inte) när ett Netvisor-fält inte ser engelskt ut."""
    if not value:
        return []
    if field == "destination":
        out = []
        for part in value.split(" → "):
            en = city_to_english(part)
            if en and en != part.strip():
                out.append(
                    f"destination should be in English for Netvisor: use '{en}' "
                    f"instead of '{part.strip()}'."
                )
        return out
    words = re.findall(r"[a-z]+", _fold(value))
    hits = [w for w in words if w in _NON_ENGLISH_WORDS and w not in _SAFE_WORDS]
    # Ett ensamt "pa"/"ja" kan vara en förkortning — kräv två träffar för dem.
    strong = [w for w in hits if w not in ("pa", "ja", "att")]
    if strong or len(hits) >= 2:
        return [f"{field} should be in English for Netvisor (found: "
                f"{', '.join(sorted(set(hits)))})."]
    return []


# ---------- Syfte ----------

# (regex på vikt titel, engelsk fras, ta med "subjekt" ur titeln)
_ACTIVITY_RULES: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"\bkunddag(ar)?\b|\bcustomer days?\b|\basiakaspaiv"), "Customer day", True),
    (re.compile(r"\binternrevision\b|\binternal audit\b|\baudit\b"), "Internal audit", False),
    (re.compile(r"\bledartraff\b|\bledardag(en)?\b|\bleadership\b|\bledarmote\b"),
     "Leadership meeting", False),
    (re.compile(r"\bmanagement meeting\b|\bledningsgrupp|\bhalvdag lg\b|\blg[- ]mote\b|\blg\b"),
     "Management meeting", False),
    (re.compile(r"\bstyrgrupp|\bsteering\b"), "Steering group meeting", False),
    (re.compile(r"\bworkshop\b"), "Workshop", False),
    (re.compile(r"\bkick ?off\b"), "Kick-off", False),
    (re.compile(r"\bkonferens\b|\bconference\b"), "Conference", False),
    (re.compile(r"\butbildning\b|\btraining\b"), "Training", False),
]
_SYNC_RE = re.compile(r"\bsynk\b|\bsync\b|\b1:1\b|\b\w+\s*/\s*\w+|\bcheck[- ]?in\b")
_NOISE_WORDS = {"on", "site", "live", "pa", "plats", "i", "in", "-", "the"}


def _subject(title: str, pattern: re.Pattern, cities: Iterable[str]) -> str:
    """'Agda PS Kunddag Malmö' → 'Agda PS' (orden runt nyckelordet, utan ort)."""
    city_folds = {_fold(c) for c in cities if c}
    words = re.split(r"\s+", title.strip())
    kept = []
    for w in words:
        f = _fold(w).strip(".,:;()-–")
        if not f or f in city_folds or f in _NOISE_WORDS or pattern.search(f):
            continue
        kept.append(w.strip(".,:;()-–"))
    subject = " ".join(k for k in kept if k)
    # Bara korta egennamn ("Agda PS"), aldrig hela meningar.
    if subject and len(kept) <= 3 and all(k[:1].isupper() for k in kept if k):
        return subject
    return ""


def generate_purpose(
    meetings: Iterable[dict],
    city: str | None,
    office: bool = False,
    extra_cities: Iterable[str] = (),
) -> str | None:
    """En mening på engelska ur dagens möten.

    'Agda PS Kunddag Malmö' → 'Customer day Agda PS, Malmö'.
    Management meeting + 1:1-synkar i GOT-rum → 'Management meeting and
    team syncs, Gothenburg office'. Inga personnamn tas med.
    """
    titles = [(m.get("summary") or "").strip() for m in meetings]
    titles = [t for t in titles if t]
    cities = [c for c in [city, *extra_cities] if c]
    phrases: list[str] = []
    for title in titles:
        folded = _fold(title)
        for pattern, phrase, with_subject in _ACTIVITY_RULES:
            if pattern.search(folded):
                text = phrase
                if with_subject:
                    subj = _subject(title, pattern, cities)
                    if subj:
                        text = f"{phrase} {subj}"
                if not any(p.split(" ")[0:2] == text.split(" ")[0:2] for p in phrases):
                    phrases.append(text)
                break
    has_syncs = any(_SYNC_RE.search(_fold(t)) for t in titles)
    if has_syncs and len(phrases) < 2:
        phrases.append("team syncs")
    if not phrases:
        if not titles:
            return None
        phrases.append("Meetings")
    phrases = phrases[:2]
    head = " and ".join([phrases[0]] + [p[0].lower() + p[1:] for p in phrases[1:]])
    head = head[0].upper() + head[1:]
    if city:
        return f"{head}, {city} office" if office else f"{head}, {city}"
    return head


def _purpose_core(part: str, cities: Iterable[str] = ()) -> str:
    """Posten utan ort ('…, Oslo office' / '…, Oslo'), gemener, utan skiljetecken."""
    text = _fold(part).strip()
    for city in sorted({_fold(c) for c in cities if c}, key=len, reverse=True):
        text = re.sub(rf",?\s*{re.escape(city)}(\s+office)?\s*$", "", text)
    text = re.sub(r",\s*[a-z .\-]+ office\s*$", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def normalize_purpose(purpose: str | None, cities: Iterable[str] = ()) -> str | None:
    """C48 Del 4 — dela på ';', ta bort poster som redan ingår i en annan
    post (gemener, orten ignoreras), behåll ordningen.

    'Workshop and team syncs, Oslo office; Team syncs, Oslo office'
    → 'Workshop and team syncs, Oslo office'."""
    if not purpose:
        return purpose
    parts = [p.strip() for p in purpose.split(";") if p.strip()]
    if len(parts) <= 1:
        return purpose.strip()
    cores = [_purpose_core(p, cities) for p in parts]
    keep: list[str] = []
    for i, (part, core) in enumerate(zip(parts, cores)):
        dup = False
        for j, other in enumerate(cores):
            if i == j or not core:
                continue
            contained = re.search(rf"(?<![a-z0-9]){re.escape(core)}(?![a-z0-9])", other)
            if core == other and j < i:
                dup = True  # exakt samma — behåll den första
            elif core != other and contained:
                dup = True  # ingår i en längre post
            if dup:
                break
        if not dup:
            keep.append(part)
    return "; ".join(keep) if keep else parts[0]


def combine_purposes(day_purposes: list[str | None],
                     cities: Iterable[str] = ()) -> str | None:
    """Resans syfte ur dagarnas: unika, i ordning, högst två. C48: poster
    som redan ingår i en annan tas bort."""
    seen: list[str] = []
    for p in day_purposes:
        if p and p not in seen:
            seen.append(p)
    if not seen:
        return None
    return normalize_purpose("; ".join(seen[:2]), cities)


def is_repeated_purpose(purpose: str | None, cities: Iterable[str] = ()) -> bool:
    return bool(purpose) and normalize_purpose(purpose, cities) != purpose.strip()


# ---------- Netvisor ----------


def meals_note(days: Iterable[tuple[str, int | None, list | None, str | None]]) -> str | None:
    """C44 — '(1 free meal: dinner)' eller, för flera dagar med måltider,
    '(3 free meals: 7 May dinner; 8 May breakfast, lunch)'.
    days: (YYYY-MM-DD, free_meals, free_meal_sources, country)."""
    from datetime import date as _date

    from app.services.trip_meals import meal_names

    rows = []
    total = 0
    for day, n, sources, cc in days:
        if not n:
            continue
        names = meal_names(sources, cc)
        total += n
        rows.append((day, n, names))
    if not total:
        return None
    word = "meal" if total == 1 else "meals"
    if len(rows) == 1:
        names = rows[0][2]
        return f"({total} free {word}: {', '.join(names)})" if names else f"({total} free {word})"
    parts = []
    for day, n, names in rows:
        d = _date.fromisoformat(day)
        label = f"{d.day} {d.strftime('%b')}"
        parts.append(f"{label} {', '.join(names)}" if names else f"{label} {n}")
    return f"({total} free {word}: {'; '.join(parts)})"


def netvisor_line_description(
    cities: Iterable[str | None],
    country: str | None,
    purpose: str | None,
    meals: str | None = None,
) -> str:
    """'Business trip Malmö, Sweden - Customer day Agda PS'.

    Beräknas när den behövs (R3), sparas inte. Orten i syftet tas bort om
    den redan står i början ("…, Malmö" / "…, Malmö office").
    """
    # C46 — en dag kan ha flera orter ("Ängelholm, Ljungby"): dela upp,
    # ta bort dubbletter och behåll ordningen.
    uniq: list[str] = []
    for raw in cities:
        for part in re.split(r"\s*(?:,|→|->|/|\band\b)\s*", raw or ""):
            c = city_to_english(part.strip()) if part and part.strip() else None
            if c and c not in uniq:
                uniq.append(c)
    place = uniq[0] if len(uniq) == 1 else (
        ", ".join(uniq[:-1]) + " and " + uniq[-1] if uniq else ""
    )
    cname = country_name(country)
    where = ", ".join(p for p in (place, cname) if p)
    text = "Business trip" + (f" {where}" if where else "")
    if purpose:
        p = purpose.strip()
        for c in uniq:
            p = re.sub(rf",\s*{re.escape(c)}( office)?\s*$", "", p)
        text += f" - {p}"
    if meals:
        text += f" {meals}"
    from app.services.text_clean import plain_dashes

    return plain_dashes(text)
