"""Match-algoritm för FAS 5.4 — kortmatchning.

Bezala har en endpoint för korttransaktioner utan kvitto. För varje
saknat kvitto försöker vi hitta matchande ProcessedMessage-rader i
vår DB baserat på belopp, datum och vendor-namn.

Pure functions — inga sidoeffekter, lätt att testa.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


# Tröskel för att ett förslag ska visas i UI:t.
MIN_DISPLAY_SCORE = 50
MAX_SUGGESTIONS_PER_MISSING = 5

# Datum-bonus: exakt match = 30, ±1 dag = 25, ±2 = 20, ±3 = 15, >3 = 0
DATE_TOLERANCE_DAYS = 3
DATE_BASE_SCORE = 30
DATE_PENALTY_PER_DAY = 5

# Belopp-tolerans: ±5% (valutakurser + avrundning)
AMOUNT_TOLERANCE = 0.05
AMOUNT_BONUS = 50

# Vendor-fuzzy: SequenceMatcher → 0..30
VENDOR_BONUS_MAX = 30

# Vendor-overrides: missing-receipt-beskrivning (substring) → kanonisk vendor
# som matchas mot ProcessedMessage.vendor. Bygger med erfarenhet av Bezala-
# korttransaktionsformat (versaler, leverantör + suffix).
VENDOR_OVERRIDES: tuple[tuple[str, str], ...] = (
    ("claude.ai", "anthropic"),
    ("anthropic", "anthropic"),
    ("openai", "openai"),
    ("airport lrs", "arlanda express"),
    ("arlandaexpress", "arlanda express"),
    ("uber", "uber"),
    ("finnair", "finnair"),
    ("scandic", "scandic"),
    ("clas ohlson", "clas ohlson"),
    ("moovy", "moovy"),
    ("sl ", "sl"),
    ("skanetraf", "skånetrafiken"),
    ("flytoget", "flytoget"),
    ("strawberry", "strawberry"),
)


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # Stöd både 'YYYY-MM-DD' och ISO-datetimes
        if len(raw) == 10:
            return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _normalize_vendor(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.strip().lower())


def _vendor_canonical(missing_description: str) -> str | None:
    """Mappa kort-transaktionsbeskrivning till kanoniskt vendor-namn via
    overrides. Ex: 'CLAUDE.AI SUBSCRIPTION' → 'anthropic'."""
    text = _normalize_vendor(missing_description)
    if not text:
        return None
    for needle, canonical in VENDOR_OVERRIDES:
        if needle in text:
            return canonical
    return None


def vendor_similarity(
    missing_description: str | None, candidate_vendor: str | None,
) -> float:
    """Returnerar 0..1-similarity mellan beskrivning och vendor-namn."""
    a = _normalize_vendor(missing_description)
    b = _normalize_vendor(candidate_vendor)
    if not a or not b:
        return 0.0
    # Exact substring → max
    if b in a or a in b:
        return 1.0
    # Override-match räknas som hög likhet
    canonical = _vendor_canonical(a)
    if canonical and canonical in b:
        return 0.95
    return SequenceMatcher(None, a, b).ratio()


def _amount_matches(missing_amount: float | None, candidate_amount: float | None) -> bool:
    if missing_amount is None or candidate_amount is None:
        return False
    if missing_amount == 0:
        return False
    diff_pct = abs(missing_amount - candidate_amount) / abs(missing_amount)
    return diff_pct <= AMOUNT_TOLERANCE


def _date_score(missing_date: str | None, candidate_date: str | None) -> int:
    md = _parse_date(missing_date)
    cd = _parse_date(candidate_date)
    if md is None or cd is None:
        return 0
    days = abs((md - cd).days)
    if days > DATE_TOLERANCE_DAYS:
        return 0
    return max(0, DATE_BASE_SCORE - days * DATE_PENALTY_PER_DAY)


def score_match(missing: dict, candidate: dict) -> dict:
    """Räkna ut total score 0..110+ för en kandidat mot ett saknat kvitto.

    missing förväntar sig: {amount, currency, date, description}
    candidate förväntar sig: ProcessedMessage-fält (amount, currency,
        receipt_date, vendor)

    Returnerar dict {total, breakdown: {amount, date, vendor}}."""
    breakdown = {"amount": 0, "date": 0, "vendor": 0}

    if _amount_matches(missing.get("amount"), candidate.get("amount")):
        breakdown["amount"] = AMOUNT_BONUS

    breakdown["date"] = _date_score(
        missing.get("date"), candidate.get("receipt_date"),
    )

    sim = vendor_similarity(
        missing.get("description"), candidate.get("vendor"),
    )
    breakdown["vendor"] = int(round(sim * VENDOR_BONUS_MAX))

    total = sum(breakdown.values())
    return {"total": total, "breakdown": breakdown}


def find_matches(missing: dict, candidates: list[dict]) -> list[dict]:
    """För ett saknat kvitto: returnera top N kandidater som passar
    tröskeln, sorterat på score desc."""
    scored: list[dict] = []
    for cand in candidates:
        s = score_match(missing, cand)
        if s["total"] >= MIN_DISPLAY_SCORE:
            scored.append({
                "message": cand,
                "score": s["total"],
                "score_breakdown": s["breakdown"],
            })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:MAX_SUGGESTIONS_PER_MISSING]
