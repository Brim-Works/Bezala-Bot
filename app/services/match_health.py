"""Match Health-rapport — analysverktyg som klassificerar varför varje
saknat Bezala-kvitto inte är matchat med ett kort-trans.

Endpoint kör HELA pipelinen läs-bara: Bezala missing_receipts +
ProcessedMessages + Gmail-historik. Returnerar en lista där varje rad
har:

  - bill_line: själva korttrans:en
  - best_match + top_3_suggestions: från `find_matches` (samma som UI:t
    redan visar)
  - fuzzy_candidates: räkningar per dimension (±10% belopp, ±7d datum,
    vendor-fuzzy) — visar om det FINNS kvitton vi kunde matcha även om
    de inte når MIN_DISPLAY_SCORE
  - gmail_status: vad Gmail-sökning historiskt mot vendor + datum-fönster
    säger (träffar med och utan has:attachment, för att fånga
    Skånetrafiken-fall där has:attachment-filtret döljer mailen)
  - verdict: en av matched_correctly | gmail_miss | no_receipt_exists |
    ai_extraction_wrong | match_algorithm_failed | gmail_error

Cache: per process, TTL CACHE_TTL_SECONDS. ?refresh=true bypassar.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models import ProcessedMessage
from app.services.receipt_matcher import (
    AMOUNT_BONUS,
    MIN_DISPLAY_SCORE,
    VENDOR_BONUS_MAX,
    find_matches,
    vendor_similarity,
)

logger = logging.getLogger(__name__)


# Cache: data + fetched_at (epoch). Mutable dict, modulnivå — räcker för
# vår single-process Railway-deploy. TTL räknat från fetched_at.
CACHE_TTL_SECONDS = 300
_CACHE: dict = {"data": None, "fetched_at": 0.0}

# Tröskel för att en match räknas som "korrekt". Räcker för det vanliga
# fallet "samma belopp + samma datum + vendor stark match" (50+30+~25 = 105)
# men släpper igenom "samma belopp + nära datum + vendor svag" (50+25+10 = 85).
STRONG_MATCH_THRESHOLD = 80

# Tröskel för "vendor är stark match" (i kombination med score för verdict).
STRONG_VENDOR_BONUS = 18  # ~60% similarity * VENDOR_BONUS_MAX

# Fuzzy-tröskelvärden (för fuzzy_candidates-räkningar)
FUZZY_AMOUNT_PCT = 0.10        # ±10%
FUZZY_DATE_DAYS = 7            # ±7d
FUZZY_VENDOR_SIM = 0.45        # SequenceMatcher-tröskel för "ungefär samma"

# Gmail-fönster för historisk sökning runt bill_line.date.
GMAIL_WINDOW_DAYS = 7

# Bezalas missing_receipt-beskrivningsformat:
#   "MIKKO KEINONEN: LOVABLE, DOVER, US 100.00 EUR"
# Plocka ut leverantörsnamnet (mellan ': ' och första ',').
_VENDOR_FROM_MERCHANT_RE = re.compile(r":\s*([^,]+?)(?:,|\s+\d|$)")


def _normalize_merchant_to_vendor(merchant: str | None) -> str | None:
    """Extrahera leverantörsnamn från Bezala-merchant-strängen."""
    if not merchant:
        return None
    m = _VENDOR_FROM_MERCHANT_RE.search(str(merchant))
    if m:
        v = m.group(1).strip()
        if v:
            return v
    # Fallback: rensa siffror + valuta i slutet
    cleaned = re.sub(r"\s+\d[\d.,]*\s*[A-Z]{3}\s*$", "", str(merchant)).strip()
    return cleaned or None


def _parse_iso_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10])
    except (ValueError, TypeError):
        return None


def _ymd(dt: datetime) -> str:
    """Gmail-query använder slash-datum: YYYY/MM/DD."""
    return dt.strftime("%Y/%m/%d")


def _build_gmail_query_for_vendor(
    vendor: str | None, bill_date: datetime | None,
    *, with_attachment: bool,
) -> str | None:
    """Bygg en Gmail-query för 'historiska kvitton från denna vendor'.

    Returnerar None om vendor saknas — vi har inget att söka på då.
    """
    if not vendor:
        return None
    parts: list[str] = []
    vendor_q = vendor.split()[0].lower() if vendor else ""
    if vendor_q:
        parts.append(f"from:{vendor_q}")
    if bill_date is not None:
        after = bill_date - timedelta(days=GMAIL_WINDOW_DAYS)
        before = bill_date + timedelta(days=GMAIL_WINDOW_DAYS + 1)
        parts.append(f"after:{_ymd(after)}")
        parts.append(f"before:{_ymd(before)}")
    if with_attachment:
        parts.append("has:attachment")
    return " ".join(parts) if parts else None


def _count_fuzzy_candidates(
    bill_line: dict, all_messages: list[dict],
) -> dict:
    """Räkna ProcessedMessages som matchar bill_line på olika dimensioner.

    Användbart för att skilja 'inget i Gmail' (no_receipt_exists) från
    'kvitton finns men algoritmen missade' (ai_extraction_wrong).
    """
    amt = bill_line.get("amount")
    bill_date = _parse_iso_date(bill_line.get("date"))
    merchant = bill_line.get("description") or bill_line.get("merchant")
    vendor_name = _normalize_merchant_to_vendor(merchant)

    by_amount = 0
    by_date = 0
    by_vendor = 0
    for cand in all_messages:
        cand_amt = cand.get("amount")
        if amt is not None and cand_amt is not None and amt > 0:
            try:
                if abs(float(cand_amt) - float(amt)) / float(amt) <= FUZZY_AMOUNT_PCT:
                    by_amount += 1
            except (ValueError, ZeroDivisionError):
                pass
        cand_date = (
            _parse_iso_date(cand.get("receipt_date"))
            or _parse_iso_date(cand.get("received_at"))
        )
        if bill_date is not None and cand_date is not None:
            if abs((cand_date - bill_date).days) <= FUZZY_DATE_DAYS:
                by_date += 1
        if vendor_name:
            sim = vendor_similarity(vendor_name, cand.get("vendor"))
            if sim >= FUZZY_VENDOR_SIM:
                by_vendor += 1

    return {
        "by_amount_window_10pct": by_amount,
        "by_date_window_7d": by_date,
        "by_vendor_fuzzy": by_vendor,
    }


def _gmail_status_for_bill_line(
    gmail_client, vendor: str | None, bill_date: datetime | None,
) -> dict:
    """Hämta Gmail-trafik runt vendor + datum-fönster, med/utan attachment.

    Cancellerar tyst om gmail_client saknas (t.ex. OAuth ej klart). Vid
    fel: returnera category='gmail_error' så frontend kan visa varning
    men inte krascha.
    """
    if vendor is None:
        return {
            "category": "not_searched",
            "details": "Vendor kunde inte extraheras från bill_line.description",
            "search_query_used": None,
            "would_match_without_attachment_filter": 0,
            "hits_with_attachment": 0,
            "hits_without_attachment": 0,
        }
    if gmail_client is None:
        return {
            "category": "not_searched",
            "details": "Gmail-klienten är inte initialiserad (OAuth saknas)",
            "search_query_used": None,
            "would_match_without_attachment_filter": 0,
            "hits_with_attachment": 0,
            "hits_without_attachment": 0,
        }

    q_with = _build_gmail_query_for_vendor(
        vendor, bill_date, with_attachment=True,
    )
    q_without = _build_gmail_query_for_vendor(
        vendor, bill_date, with_attachment=False,
    )
    if not q_with or not q_without:
        return {
            "category": "not_searched",
            "details": "Inte tillräcklig info för Gmail-sökning",
            "search_query_used": None,
            "would_match_without_attachment_filter": 0,
            "hits_with_attachment": 0,
            "hits_without_attachment": 0,
        }

    try:
        hits_with = gmail_client.list_candidate_message_ids(
            query=q_with, max_results=20,
        )
        hits_without = gmail_client.list_candidate_message_ids(
            query=q_without, max_results=20,
        )
    except Exception as exc:  # noqa: BLE001 — vi vill se exakt vad Gmail gav
        logger.warning(
            "Gmail-sökning misslyckades för vendor=%r: %s", vendor, exc,
        )
        return {
            "category": "gmail_error",
            "details": f"Gmail API-fel: {exc}",
            "search_query_used": q_with,
            "would_match_without_attachment_filter": 0,
            "hits_with_attachment": 0,
            "hits_without_attachment": 0,
        }

    n_with = len(hits_with)
    n_without = len(hits_without)
    extra_without = max(0, n_without - n_with)

    if n_without == 0:
        category = "no_hits"
        details = (
            f"Inga Gmail-mail från {vendor!r} i ±{GMAIL_WINDOW_DAYS}d-fönstret"
        )
    elif n_with == 0 and n_without > 0:
        category = "filtered"
        details = (
            f"Hittade {n_without} mail från {vendor!r} men 0 hade "
            f"has:attachment — has:attachment-filtret döljer alla"
        )
    elif extra_without > 0:
        category = "found"
        details = (
            f"Hittade {n_with} med attachment, {extra_without} ytterligare "
            f"utan — fortfarande inom 'found'"
        )
    else:
        category = "found"
        details = (
            f"Hittade {n_with} mail från {vendor!r} (alla med attachment)"
        )

    return {
        "category": category,
        "details": details,
        "search_query_used": q_with,
        "would_match_without_attachment_filter": extra_without,
        "hits_with_attachment": n_with,
        "hits_without_attachment": n_without,
    }


def _classify_verdict(
    *,
    best_match: dict | None,
    fuzzy: dict,
    gmail: dict,
    vendor_name: str | None,
) -> dict:
    """Klassificera bill_line:n. Returnerar
    {category, confidence, suggested_action}."""
    fuzzy_total = (
        fuzzy["by_amount_window_10pct"]
        + fuzzy["by_date_window_7d"]
        + fuzzy["by_vendor_fuzzy"]
    )
    has_fuzzy = fuzzy_total > 0
    best_score = best_match["score"] if best_match else 0
    best_vendor_score = (
        (best_match.get("score_breakdown") or {}).get("vendor", 0)
        if best_match else 0
    )

    if gmail["category"] == "gmail_error":
        return {
            "category": "gmail_error",
            "confidence": "low",
            "suggested_action": (
                "Gmail API svarade inte — testa igen om en stund eller "
                "kolla OAuth-status."
            ),
        }

    if best_match is not None and best_score >= STRONG_MATCH_THRESHOLD \
            and best_vendor_score >= STRONG_VENDOR_BONUS:
        return {
            "category": "matched_correctly",
            "confidence": "high",
            "suggested_action": (
                "Klicka Match i Travel Tinder för att bekräfta kopplingen."
            ),
        }

    if not has_fuzzy and gmail["category"] == "filtered":
        return {
            "category": "gmail_miss",
            "confidence": "high",
            "suggested_action": (
                f"Lägg till {vendor_name or '<vendor>'} i link_fetch_senders "
                f"så Bezala Bot hämtar PDF från länk istället för bilaga "
                f"({gmail['would_match_without_attachment_filter']} sådana "
                f"mail finns)."
            ),
        }

    if not has_fuzzy and gmail["category"] == "no_hits":
        return {
            "category": "no_receipt_exists",
            "confidence": "medium",
            "suggested_action": (
                f"Kvitto för {vendor_name or '<vendor>'} finns sannolikt "
                f"inte i Gmail — kan vara fysiskt kvitto, app-store-faktura "
                f"eller PDF endast i kundportalen."
            ),
        }

    if has_fuzzy and (best_match is None or best_score < MIN_DISPLAY_SCORE):
        return {
            "category": "ai_extraction_wrong",
            "confidence": "medium",
            "suggested_action": (
                f"Det finns {fuzzy_total} fuzzy-kandidater i datum/belopp/"
                f"vendor-fönstret men ingen når matchningströskeln. "
                f"Kontrollera AI-extraktionen av datum/belopp på dessa "
                f"kvitton i Granska-vyn."
            ),
        }

    if has_fuzzy and best_match is not None \
            and best_score < STRONG_MATCH_THRESHOLD:
        return {
            "category": "match_algorithm_failed",
            "confidence": "medium",
            "suggested_action": (
                f"Bästa förslaget har score {best_score} — top-3 har möjligen "
                f"bättre kandidater. Kolla score-breakdown och eventuellt "
                f"justera vendor-overrides i receipt_matcher.py."
            ),
        }

    return {
        "category": "matched_correctly" if best_match else "no_receipt_exists",
        "confidence": "low",
        "suggested_action": (
            "Ingen tydlig signal — manuell genomgång rekommenderas."
        ),
    }


def _strip_serialized_for_suggestion(d: dict) -> dict:
    """Plocka ut bara fälten vi vill ha i top-3-listan."""
    return {
        "message_id": d.get("message_id"),
        "id": d.get("id"),
        "vendor": d.get("vendor"),
        "file_name": d.get("file_name"),
        "amount": d.get("amount"),
        "currency": d.get("currency"),
        "receipt_date": d.get("receipt_date"),
        "received_at": d.get("received_at"),
    }


def build_match_health_report(
    db: Session,
    *,
    bezala_client,
    gmail_client,
    rate_provider=None,
    normalize_missing_receipt,
    serialize_message,
    refresh: bool = False,
) -> dict:
    """Bygg hela Match Health-rapporten.

    Argumenten `bezala_client`, `gmail_client`, `rate_provider`,
    `normalize_missing_receipt` och `serialize_message` injiceras från
    endpointen så modulen kan testas isolerat utan att importera tunga
    beroenden (cykliska imports m.m.).

    Returnerar:
      {
        "cache_age_seconds": float | None,
        "generated_at": "<iso>",
        "rows": [{bill_line, best_match, top_3_suggestions,
                  fuzzy_candidates, gmail_status, verdict}, ...],
        "stats": {total, matched_correctly, gmail_miss, ai_extraction_wrong,
                  match_algorithm_failed, no_receipt_exists, gmail_error},
      }
    """
    now = time.time()
    cached = _CACHE.get("data")
    cached_at = _CACHE.get("fetched_at", 0.0)
    if not refresh and cached is not None and (now - cached_at) < CACHE_TTL_SECONDS:
        out = dict(cached)
        out["cache_age_seconds"] = round(now - cached_at, 1)
        return out

    # 1. Hämta saknade kvitton från Bezala
    missing_raw = bezala_client.list_missing_receipts() or []

    # 2. Hämta alla okopplade ProcessedMessages (kandidater för matchning)
    candidates_q = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.status == "saved")
        .filter(ProcessedMessage.bezala_upload_status != "success")
        .filter(ProcessedMessage.bezala_transaction_id.is_(None))
        .order_by(desc(ProcessedMessage.received_at))
        .limit(500)
    )
    candidate_dicts = [serialize_message(r) for r in candidates_q.all()]

    rows: list[dict] = []
    stats = {
        "total": 0,
        "matched_correctly": 0,
        "gmail_miss": 0,
        "ai_extraction_wrong": 0,
        "match_algorithm_failed": 0,
        "no_receipt_exists": 0,
        "gmail_error": 0,
    }

    for raw in missing_raw:
        missing = normalize_missing_receipt(raw)
        merchant = missing.get("description") or ""
        vendor_name = _normalize_merchant_to_vendor(merchant)
        bill_date = _parse_iso_date(missing.get("date"))

        suggestions = find_matches(
            missing, candidate_dicts, rate_provider=rate_provider,
        )
        top_3 = []
        for s in suggestions[:3]:
            entry = {
                "score": s["score"],
                "score_breakdown": s["score_breakdown"],
                **_strip_serialized_for_suggestion(s["message"]),
            }
            if "conversion" in s:
                entry["conversion"] = s["conversion"]
            top_3.append(entry)
        best_match = top_3[0] if top_3 else None

        fuzzy = _count_fuzzy_candidates(missing, candidate_dicts)
        gmail = _gmail_status_for_bill_line(
            gmail_client, vendor_name, bill_date,
        )
        verdict = _classify_verdict(
            best_match=best_match,
            fuzzy=fuzzy,
            gmail=gmail,
            vendor_name=vendor_name,
        )
        stats[verdict["category"]] = stats.get(verdict["category"], 0) + 1
        stats["total"] += 1

        rows.append({
            "bill_line": {
                "id": missing.get("id"),
                "merchant": merchant,
                "vendor_normalized": vendor_name,
                "amount": missing.get("amount"),
                "currency": missing.get("currency"),
                "date": missing.get("date"),
            },
            "best_match": best_match,
            "top_3_suggestions": top_3,
            "fuzzy_candidates": fuzzy,
            "gmail_status": gmail,
            "verdict": verdict,
        })

    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "cache_age_seconds": 0.0,
        "rows": rows,
        "stats": stats,
    }
    _CACHE["data"] = report
    _CACHE["fetched_at"] = now
    logger.info(
        "match-health: byggde rapport — %d bill_lines, stats=%s",
        stats["total"], stats,
    )
    return report


def clear_cache() -> None:
    """Test-helper: rensa modulnivå-cachen."""
    _CACHE["data"] = None
    _CACHE["fetched_at"] = 0.0
