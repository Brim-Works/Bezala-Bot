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

from sqlalchemy import desc, or_
from sqlalchemy.orm import Session

from app.models import ProcessedMessage
from app.services.receipt_matcher import (
    AMOUNT_BONUS,
    DATE_BUCKETS,
    MIN_DISPLAY_SCORE,
    VENDOR_BONUS_MAX,
    VENDOR_OVERRIDES,
    _normalize_vendor,
    _vendor_canonical,
    find_matches,
    score_match,
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
    *, html_only_patterns: list[str] | None = None,
) -> dict:
    """Hämta Gmail-trafik runt vendor + datum-fönster, med/utan attachment.

    Cancellerar tyst om gmail_client saknas (t.ex. OAuth ej klart). Vid
    fel: returnera category='gmail_error' så frontend kan visa varning
    men inte krascha.

    `html_only_patterns`: aktiva html-only-senders. Om vendor matchar
    någon av dem → pipeline hämtar mailen via andra-passet UTAN
    has:attachment-filter. Då är "filtered"-klassificeringen MISSVISANDE
    (mailen plockas faktiskt upp). Vi använder då utan-attachment-queryn
    som primär och rapporterar 'found'/'no_hits' istället.
    """
    from app.services.html_only_senders import is_html_only_sender

    via_html_only = is_html_only_sender(
        vendor, html_only_patterns or [],
    )

    if vendor is None:
        return {
            "category": "not_searched",
            "details": "Vendor kunde inte extraheras från bill_line.description",
            "search_query_used": None,
            "would_match_without_attachment_filter": 0,
            "hits_with_attachment": 0,
            "hits_without_attachment": 0,
            "via_html_only_pipeline": False,
        }
    if gmail_client is None:
        return {
            "category": "not_searched",
            "details": "Gmail-klienten är inte initialiserad (OAuth saknas)",
            "search_query_used": None,
            "would_match_without_attachment_filter": 0,
            "hits_with_attachment": 0,
            "hits_without_attachment": 0,
            "via_html_only_pipeline": via_html_only,
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
            "via_html_only_pipeline": via_html_only,
        }

    try:
        # För html-only-senders sparar vi ett Gmail-anrop genom att bara
        # köra utan-attachment-queryn (det är vad pipeline gör).
        if via_html_only:
            hits_with: list = []
            hits_without = gmail_client.list_candidate_message_ids(
                query=q_without, max_results=20,
            )
        else:
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
            "search_query_used": q_without if via_html_only else q_with,
            "would_match_without_attachment_filter": 0,
            "hits_with_attachment": 0,
            "hits_without_attachment": 0,
            "via_html_only_pipeline": via_html_only,
        }

    n_with = len(hits_with)
    n_without = len(hits_without)
    extra_without = max(0, n_without - n_with)

    if via_html_only:
        # html-only-senders: pipelinen tar in mail via passet utan
        # attachment. "Filtered" är aldrig en korrekt klassning här.
        if n_without == 0:
            category = "no_hits"
            details = (
                f"Inga Gmail-mail från {vendor!r} i "
                f"±{GMAIL_WINDOW_DAYS}d-fönstret (html-only-pass)"
            )
        else:
            category = "found"
            details = (
                f"Hittade {n_without} mail från {vendor!r} via "
                f"html-only-pipelinen (has:attachment-filter skippas "
                f"för denna vendor)"
            )
        # Den effektivt använda queryn är utan-attachment-varianten.
        primary_query = q_without
    else:
        if n_without == 0:
            category = "no_hits"
            details = (
                f"Inga Gmail-mail från {vendor!r} i "
                f"±{GMAIL_WINDOW_DAYS}d-fönstret"
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
        primary_query = q_with

    return {
        "category": category,
        "details": details,
        "search_query_used": primary_query,
        "would_match_without_attachment_filter": (
            0 if via_html_only else extra_without
        ),
        "hits_with_attachment": n_with,
        "hits_without_attachment": n_without,
        "via_html_only_pipeline": via_html_only,
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
                f"Lägg till {vendor_name or '<vendor>'} i HTML-only "
                f"avsändare i Inställningar — Bezala Bot kommer då plocka "
                f"upp dem via html_to_pdf-pipelinen "
                f"({gmail['would_match_without_attachment_filter']} sådana "
                f"mail finns)."
            ),
        }

    # html-only-pipeline har redan plockat upp mailen — om Gmail "found"
    # men inget ProcessedMessage ännu så är det väntat (nästa scan-cykel
    # processar dem) eller html_to_pdf failade.
    if not has_fuzzy and gmail.get("via_html_only_pipeline") \
            and gmail["category"] == "found":
        return {
            "category": "matched_correctly",
            "confidence": "medium",
            "suggested_action": (
                f"{gmail['hits_without_attachment']} mail från "
                f"{vendor_name or '<vendor>'} finns i Gmail — html-only-"
                f"pipelinen plockar upp dem vid nästa scan. Om de fortfarande "
                f"saknas efter scan: kolla html_to_pdf-loggar för fel."
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


# ---------------------------------------------------------------------------
# Match Health 2.0 — utökade hjälpfunktioner
# ---------------------------------------------------------------------------
#
# Spec:
#   - processed_receipts[]: bredare kandidatlista (±20% belopp / ±21d /
#     vendor-substring / redan kopplade till denna bill_line) med fullt
#     score-breakdown per rad
#   - gmail_messages[]: alla Gmail-träffar (med + utan attachment), med
#     fält om de är processade i vår DB eller filtrerade som not_receipt
#   - diagnostic_summary: kort sammanfattning för UI:t
#   - Utvidgad verdict-lista: multiple_candidates_above_threshold,
#     best_below_threshold, processed_but_no_candidate,
#     gmail_found_not_processed, gmail_filtered_or_excluded,
#     already_matched

# Bredare fönster för "kandidat-listan" i diagnostiken.
EXTENDED_AMOUNT_PCT = 0.20      # ±20%
EXTENDED_DATE_DAYS = 21         # ±21d

# Hur många icke-processade Gmail-träffar vi anropar
# `fetch_message_metadata` för per bill_line — Gmail-quota-styrning.
MAX_METADATA_FETCH_PER_BILL_LINE = 5


def _vendor_match_method(
    missing_description: str | None, candidate_vendor: str | None,
) -> tuple[str, int]:
    """Klassificera vendor-matchningen: ('substring' | 'override' |
    'fuzzy' | 'none', similarity_pct_int)."""
    a = _normalize_vendor(missing_description)
    b = _normalize_vendor(candidate_vendor)
    if not a or not b:
        return "none", 0
    if b in a or a in b:
        return "substring", 100
    canonical = _vendor_canonical(a)
    if canonical and canonical in b:
        return "override", 95
    pct = int(round(vendor_similarity(missing_description, candidate_vendor) * 100))
    return ("fuzzy" if pct > 0 else "none"), pct


def _enrich_score_breakdown(
    missing: dict, cand: dict, *, base_breakdown: dict,
) -> dict:
    """Bygg ut score_breakdown med diagnostik-vänliga fält:
    amount_diff/_pct, vendor_method/_similarity_pct, date_matched_field
    (redan i base). Behåller alla befintliga nycklar."""
    out = dict(base_breakdown)
    # Datum-diff (already in base_breakdown som date_days_off)
    out.setdefault("date_diff_days", base_breakdown.get("date_days_off"))
    # Belopps-diff
    m_amt = missing.get("amount")
    c_amt = cand.get("amount")
    if m_amt is not None and c_amt is not None:
        try:
            diff = float(c_amt) - float(m_amt)
            out["amount_diff"] = round(diff, 2)
            if float(m_amt) > 0:
                out["amount_diff_pct"] = round(
                    abs(diff) / float(m_amt) * 100.0, 1,
                )
            else:
                out["amount_diff_pct"] = None
        except (ValueError, TypeError):
            out["amount_diff"] = None
            out["amount_diff_pct"] = None
    else:
        out["amount_diff"] = None
        out["amount_diff_pct"] = None
    # Vendor-method + similarity
    method, sim_pct = _vendor_match_method(
        missing.get("description"), cand.get("vendor"),
    )
    out["vendor_match_method"] = method
    out["vendor_similarity_pct"] = sim_pct
    return out


def _find_extended_candidates(
    db: Session,
    missing: dict,
    *,
    vendor_name: str | None,
    serialize_message,
) -> list[dict]:
    """Bred kandidatlista för diagnostik. Match Health 2.0:
      - amount ±20% (ej ±5% som matchern)
      - date ±21d (ej ±7d som matchern)
      - vendor case-insensitive substring (båda håll)
      - bezala_transaction_id == bill_line.id (redan kopplade)

    Returnerar deserialized dicts (samma shape som matchern förväntar).
    """
    bill_id = missing.get("id")
    bill_id_str = str(bill_id) if bill_id is not None else None

    # Fetch broadly, filter in Python — enklare och tabell-storlek är liten.
    q = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.status == "saved")
        .order_by(desc(ProcessedMessage.received_at))
        .limit(1000)
    )
    rows = q.all()

    bill_amt = missing.get("amount")
    bill_date = _parse_iso_date(missing.get("date"))
    vendor_lower = (vendor_name or "").strip().lower()

    candidates: list[dict] = []
    for r in rows:
        matched = False
        # 1. Already coupled to THIS bill_line
        if bill_id_str and r.bezala_transaction_id is not None \
                and str(r.bezala_transaction_id) == bill_id_str:
            matched = True
        # 2. Amount ±20%
        if not matched and bill_amt is not None and r.amount is not None:
            try:
                if abs(float(r.amount) - float(bill_amt)) / float(bill_amt) \
                        <= EXTENDED_AMOUNT_PCT:
                    matched = True
            except (ValueError, ZeroDivisionError, TypeError):
                pass
        # 3. Date ±21d
        if not matched and bill_date is not None:
            r_date = (
                _parse_iso_date(r.receipt_date)
                or _parse_iso_date(
                    r.received_at.isoformat()
                    if r.received_at is not None else None,
                )
            )
            if r_date is not None and abs((r_date - bill_date).days) <= EXTENDED_DATE_DAYS:
                matched = True
        # 4. Vendor case-insensitive substring (båda håll)
        if not matched and vendor_lower and r.vendor:
            r_vendor_lower = r.vendor.strip().lower()
            if r_vendor_lower and (
                vendor_lower in r_vendor_lower or r_vendor_lower in vendor_lower
            ):
                matched = True
        if matched:
            candidates.append(serialize_message(r))
    return candidates


def _score_candidates_for_diagnostic(
    missing: dict, candidates: list[dict],
    *, rate_provider, strong_threshold: int,
) -> list[dict]:
    """Score VARJE kandidat oavsett MIN_DISPLAY_SCORE (för diagnostik).
    Returnerar lista sorterad på total desc, varje rad har:
        { ...candidate fields..., match_score_total, match_score_breakdown,
          above_threshold, why_not_best }
    """
    scored: list[dict] = []
    for cand in candidates:
        s = score_match(missing, cand, rate_provider=rate_provider)
        breakdown = _enrich_score_breakdown(
            missing, cand, base_breakdown=s["breakdown"],
        )
        entry = {
            **_strip_serialized_for_suggestion(cand),
            "sender_full": cand.get("sender"),
            "subject": cand.get("subject"),
            "category": cand.get("category"),
            "ai_confidence": cand.get("ai_confidence"),
            "ai_summary": cand.get("summary"),
            "drive_link": cand.get("drive_link"),
            "drive_file_id": cand.get("drive_file_id"),
            "bezala_upload_status": cand.get("bezala_upload_status"),
            "bezala_transaction_id": cand.get("bezala_transaction_id"),
            "match_score_total": s["total"],
            "match_score_breakdown": breakdown,
            "above_threshold": s["total"] >= strong_threshold,
        }
        if "conversion" in s:
            entry["conversion"] = s["conversion"]
        scored.append(entry)
    # Sort desc by total
    scored.sort(key=lambda e: e["match_score_total"], reverse=True)
    # Mark why_not_best för icke-toppen
    if scored:
        top_score = scored[0]["match_score_total"]
        for i, entry in enumerate(scored):
            if i == 0:
                entry["why_not_best"] = None
            else:
                gap = top_score - entry["match_score_total"]
                entry["why_not_best"] = (
                    f"Annat kvitto har högre score ({top_score} vs {entry['match_score_total']}, "
                    f"diff {gap})"
                )
    return scored


def _enrich_gmail_messages(
    gmail_client, db: Session, vendor_name: str | None,
    bill_date: datetime | None, *,
    html_only_patterns: list[str] | None,
) -> list[dict]:
    """Hämta Gmail-träffar runt vendor + datum-fönster och annotera dem:
      - has_attachment (via vilken query som matchade)
      - via_html_only_pipeline (om vendor matchar pattern)
      - is_processed + processed_message_id (lookup i ProcessedMessage)
      - is_filtered_not_receipt (om processade och deleted via not_receipt)
      - sender/subject/received_at (från DB om processad, annars
        fetch_message_metadata upp till MAX_METADATA_FETCH_PER_BILL_LINE)

    Returnerar [] vid Gmail-fel eller avsaknad av klient.
    """
    if gmail_client is None or vendor_name is None:
        return []
    from app.services.html_only_senders import is_html_only_sender
    via_html_only = is_html_only_sender(
        vendor_name, html_only_patterns or [],
    )
    q_with = _build_gmail_query_for_vendor(
        vendor_name, bill_date, with_attachment=True,
    )
    q_without = _build_gmail_query_for_vendor(
        vendor_name, bill_date, with_attachment=False,
    )
    if not q_without:
        return []
    try:
        # Vi behöver båda för has_attachment-fältet, även för html-only.
        if via_html_only:
            ids_with: set = set()
        else:
            ids_with = set(gmail_client.list_candidate_message_ids(
                query=q_with, max_results=20,
            ))
        ids_all = list(gmail_client.list_candidate_message_ids(
            query=q_without, max_results=20,
        ))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Gmail-enrichment misslyckades för vendor=%r: %s", vendor_name, exc,
        )
        return []

    # Lookup processed messages via gmail message_id (en query)
    processed_by_msg_id: dict = {}
    if ids_all:
        try:
            for row in (
                db.query(ProcessedMessage)
                .filter(ProcessedMessage.message_id.in_(ids_all))
                .all()
            ):
                processed_by_msg_id[row.message_id] = row
        except Exception:  # noqa: BLE001
            logger.exception("ProcessedMessage-lookup failed for Gmail enrichment")

    out: list[dict] = []
    metadata_calls_remaining = MAX_METADATA_FETCH_PER_BILL_LINE
    for mid in ids_all:
        proc = processed_by_msg_id.get(mid)
        has_attachment = (mid in ids_with) and not via_html_only

        if proc is not None:
            sender = proc.sender
            subject = proc.subject
            received_at_iso = (
                proc.received_at.isoformat()
                if proc.received_at is not None else None
            )
            is_processed = True
            processed_message_id = proc.id
            is_filtered_not_receipt = (
                proc.deleted_at is not None
                and proc.delete_reason == "user_marked_not_receipt"
            )
            filter_reason = proc.delete_reason if proc.deleted_at else None
        else:
            sender = subject = received_at_iso = None
            is_processed = False
            processed_message_id = None
            is_filtered_not_receipt = None
            filter_reason = None
            if metadata_calls_remaining > 0:
                try:
                    md = gmail_client.fetch_message_metadata(mid)
                    metadata_calls_remaining -= 1
                    headers = md.get("headers") or {}
                    sender = headers.get("from") or headers.get("From")
                    subject = headers.get("subject") or headers.get("Subject")
                    received_at_iso = (
                        headers.get("date") or headers.get("Date")
                    )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "fetch_message_metadata failed for mid=%s — fortsätter",
                        mid,
                    )

        out.append({
            "message_id": mid,
            "sender": sender,
            "subject": subject,
            "received_at": received_at_iso,
            "has_attachment": has_attachment,
            "via_html_only_pipeline": via_html_only,
            "is_processed": is_processed,
            "processed_message_id": processed_message_id,
            "is_excluded": None,  # framtida: koppla mot excluded_vendors
            "is_filtered_not_receipt": is_filtered_not_receipt,
            "filter_reason": filter_reason,
        })
    return out


def _classify_diagnostic_v2(
    *,
    scored_candidates: list[dict],
    gmail_messages: list[dict],
    gmail_status: dict,
    vendor_name: str | None,
    strong_threshold: int,
) -> tuple[dict, dict]:
    """Klassificera bill_line:n med utökad verdict-lista (Match Health 2.0).

    Returnerar (verdict_dict, diagnostic_summary_dict).
    """
    above = [c for c in scored_candidates if c["above_threshold"]]
    has_processed = bool(scored_candidates)
    best = scored_candidates[0] if scored_candidates else None
    best_score = best["match_score_total"] if best else 0
    has_gmail = bool(gmail_messages)
    n_processed_in_gmail = sum(
        1 for g in gmail_messages if g.get("is_processed")
    )
    n_filtered = sum(
        1 for g in gmail_messages if g.get("is_filtered_not_receipt")
    )
    # already_matched: någon kandidat har redan denna bill_line som
    # bezala_transaction_id.
    already_matched = next(
        (c for c in scored_candidates
         if c.get("bezala_transaction_id")
         and str(c["bezala_transaction_id"])
             == str(scored_candidates[0].get("bezala_transaction_id"))
         and c.get("match_score_total", 0) > 0),
        None,
    )
    coupled_to_this = [
        c for c in scored_candidates
        if c.get("bezala_transaction_id")
    ]

    summary = {
        "gmail_status": gmail_status.get("category") or "not_searched",
        "gmail_count": len(gmail_messages),
        "processed_count": n_processed_in_gmail,
        "processed_failed_count": max(
            0, len(gmail_messages) - n_processed_in_gmail,
        ),
        "candidate_count": len(scored_candidates),
        "above_threshold_count": len(above),
        "best_score": best_score,
        "threshold": strong_threshold,
    }

    if coupled_to_this:
        coupled = coupled_to_this[0]
        verdict = {
            "category": "already_matched",
            "confidence": "high",
            "suggested_action": (
                f"Korttrans redan kopplad till '{coupled.get('vendor') or '—'}' "
                f"(ProcessedMessage #{coupled.get('id')})."
            ),
        }
        summary["next_action"] = verdict["suggested_action"]
        return verdict, summary

    if gmail_status.get("category") == "gmail_error":
        verdict = {
            "category": "gmail_error",
            "confidence": "low",
            "suggested_action": "Gmail API svarade inte — testa igen.",
        }
        summary["next_action"] = verdict["suggested_action"]
        return verdict, summary

    if len(above) >= 2:
        verdict = {
            "category": "multiple_candidates_above_threshold",
            "confidence": "medium",
            "suggested_action": (
                f"{len(above)} kvitton över tröskeln {strong_threshold}. "
                "Välj manuellt i Travel Tinder."
            ),
        }
    elif len(above) == 1:
        verdict = {
            "category": "matched_correctly",
            "confidence": "high",
            "suggested_action": (
                "Klicka Match i Travel Tinder för att bekräfta kopplingen."
            ),
        }
    elif has_processed and best_score > 0:
        gap = strong_threshold - best_score
        verdict = {
            "category": "best_below_threshold",
            "confidence": "medium",
            "suggested_action": (
                f"Bästa kandidat har score {best_score}, behöver {strong_threshold}. "
                f"Saknar {gap} poäng — kolla score-breakdown nedan."
            ),
        }
    elif has_processed:
        verdict = {
            "category": "processed_but_no_candidate",
            "confidence": "medium",
            "suggested_action": (
                f"{len(scored_candidates)} kvitton finns i DB men ingen är "
                "ens nära matchning. Sannolikt AI-extraktion fel (datum/belopp). "
                "Kolla kvitton-listan nedan."
            ),
        }
    elif has_gmail and n_processed_in_gmail == 0:
        verdict = {
            "category": "gmail_found_not_processed",
            "confidence": "medium",
            "suggested_action": (
                f"{len(gmail_messages)} mail finns i Gmail men ingen är "
                "processad i Bezala Bot — html_to_pdf-fel eller annan pipeline-fail."
            ),
        }
    elif has_gmail and n_filtered > 0:
        verdict = {
            "category": "gmail_filtered_or_excluded",
            "confidence": "medium",
            "suggested_action": (
                f"{n_filtered} mail har markerats som inte-kvitto. "
                "Återställ via Papperskorgen om felaktigt."
            ),
        }
    elif gmail_status.get("category") == "filtered":
        verdict = {
            "category": "gmail_miss",
            "confidence": "high",
            "suggested_action": (
                f"Lägg till {vendor_name or '<vendor>'} i HTML-only "
                "avsändare i Inställningar — Bezala Bot kommer då plocka "
                f"upp dem via html_to_pdf-pipelinen "
                f"({gmail_status.get('would_match_without_attachment_filter', 0)} "
                "sådana mail finns)."
            ),
        }
    elif gmail_status.get("category") == "no_hits":
        verdict = {
            "category": "no_receipt_exists",
            "confidence": "medium",
            "suggested_action": (
                f"Kvitto för {vendor_name or '<vendor>'} finns sannolikt "
                "inte i Gmail — fysiskt eller app-store-faktura."
            ),
        }
    else:
        verdict = {
            "category": "no_receipt_exists",
            "confidence": "low",
            "suggested_action": "Ingen tydlig signal — manuell genomgång.",
        }

    summary["next_action"] = verdict["suggested_action"]
    return verdict, summary


# ---------------------------------------------------------------------------


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

    # 3. Hämta aktiva html_only_senders så Gmail-status-byggaren kan
    # respektera samma logik som scan-pipelinen (skip has:attachment).
    # Säker att kalla även om tabellen inte finns (gör catch).
    try:
        from app.services.html_only_senders import list_active_patterns
        html_only_patterns = list_active_patterns(db)
    except Exception:  # noqa: BLE001 — får aldrig krascha hela rapporten
        logger.exception("html_only_senders fetch misslyckades — fortsätter utan")
        html_only_patterns = []

    rows: list[dict] = []
    stats: dict = {
        "total": 0,
        # Behåll legacy-nycklar så befintliga frontend-vyer + tester inte
        # bryts; UI:t använder dem för stats-baren.
        "matched_correctly": 0,
        "gmail_miss": 0,
        "ai_extraction_wrong": 0,
        "match_algorithm_failed": 0,
        "no_receipt_exists": 0,
        "gmail_error": 0,
        # Match Health 2.0 — nya kategorier
        "multiple_candidates_above_threshold": 0,
        "best_below_threshold": 0,
        "processed_but_no_candidate": 0,
        "gmail_found_not_processed": 0,
        "gmail_filtered_or_excluded": 0,
        "already_matched": 0,
    }

    for raw in missing_raw:
        missing = normalize_missing_receipt(raw)
        merchant = missing.get("description") or ""
        vendor_name = _normalize_merchant_to_vendor(merchant)
        bill_date = _parse_iso_date(missing.get("date"))

        # Standard top-3 (behåller befintligt schema)
        suggestions = find_matches(
            missing, candidate_dicts, rate_provider=rate_provider,
        )
        top_3 = []
        for s in suggestions[:3]:
            entry = {
                "score": s["score"],
                "score_breakdown": _enrich_score_breakdown(
                    missing, s["message"],
                    base_breakdown=s["score_breakdown"],
                ),
                **_strip_serialized_for_suggestion(s["message"]),
            }
            if "conversion" in s:
                entry["conversion"] = s["conversion"]
            top_3.append(entry)
        best_match = top_3[0] if top_3 else None

        fuzzy = _count_fuzzy_candidates(missing, candidate_dicts)
        gmail = _gmail_status_for_bill_line(
            gmail_client, vendor_name, bill_date,
            html_only_patterns=html_only_patterns,
        )

        # Match Health 2.0 — utökad kandidat- + gmail-data + verdict
        extended_candidates = _find_extended_candidates(
            db, missing,
            vendor_name=vendor_name,
            serialize_message=serialize_message,
        )
        processed_receipts = _score_candidates_for_diagnostic(
            missing, extended_candidates,
            rate_provider=rate_provider,
            strong_threshold=STRONG_MATCH_THRESHOLD,
        )
        gmail_messages = _enrich_gmail_messages(
            gmail_client, db, vendor_name, bill_date,
            html_only_patterns=html_only_patterns,
        )
        verdict, diagnostic_summary = _classify_diagnostic_v2(
            scored_candidates=processed_receipts,
            gmail_messages=gmail_messages,
            gmail_status=gmail,
            vendor_name=vendor_name,
            strong_threshold=STRONG_MATCH_THRESHOLD,
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
            # Match Health 2.0 — nya fält
            "processed_receipts": processed_receipts,
            "gmail_messages": gmail_messages,
            "diagnostic_summary": diagnostic_summary,
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
