"""FAS 11.1 — gruppera kvitton till resor.

Tre steg:
  1) Hitta flygbiljetter (anchors) bland kandidat-meddelanden.
  2) Bygg ett resefönster runt varje flygbiljett (±dagar) och samla
     ihop relaterade kvitton.
  3) Skicka förslagen till Claude för att finslipa grupperingen,
     generera titel/beskrivning och bedöma confidence.

Pure-funktionsstil där det går — inga sidoeffekter förrän
schedulerade jobbet eller endpoint:en sparar i DB.

Defensiva mot Claude-fel: vid API-misslyckande används en lokal
fallback för titel/beskrivning så användaren ändå får ett förslag.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ProcessedMessage, Trip, TripFeedback, TripMessage

logger = logging.getLogger(__name__)


# Domänlista för flygbolag som vi känner igen via vendor- eller sender-
# fältet. Andra flygbolag fångas också om kategori är 'Flyg'.
FLIGHT_VENDOR_TOKENS: tuple[str, ...] = (
    "finnair",
    "sas",
    "lufthansa",
    "klm",
    "norwegian",
    "ryanair",
    "british airways",
    "airfrance",
    "air france",
    "easyjet",
    "wizz air",
    "wizzair",
    "delta",
    "united airlines",
    "swiss",
)

# FAS 11.1.1 — Resefönstret bygger primärt på outbound→inbound-flyg.
# Fall-back när vi inte hittar ett returflyg: outbound + 7 dagar.
# 2 dagar före outbound tas också med (taxi till flygplats).
WINDOW_DAYS_BEFORE = 2
WINDOW_DAYS_AFTER_FALLBACK = 7
RETURN_FLIGHT_LOOKAHEAD_DAYS = 14

# Filter: kvitton dyrare än så här ingår sällan i en resa
# (datorinköp, kontorsmöbler etc). Gäller alla valutor — vi gör inte
# valutakonvertering i candidate-filtret.
LARGE_RECEIPT_THRESHOLD = 5000.0

# Minst så här många kvitton (inkl. flygbiljett) krävs för att ett
# förslag ska skapas. < 2 = bara flygbiljett → vi skapar ändå men
# markerar det med lägre confidence i refine-steget.
MIN_RECEIPTS_FOR_TRIP = 2


# FAS 11.1.1 — endast kvitton vars kategori innehåller någon av dessa
# tokens räknas som potentiella resekostnader. Listan är medvetet bred
# (substring-match, case-insensitive) och täcker både svenska, engelska
# och finska kategori-strängar.
TRAVEL_CATEGORIES: tuple[str, ...] = (
    "hotell", "boende", "logi", "hotel",
    "taxi", "tåg", "tag", "buss", "tunnelbana", "train", "bus",
    "flyg", "flight",
    "parkering", "parking",
    "restaurang", "café", "cafe", "lunch", "middag",
    "biluthyrning", "hyrbil", "rental",
    "kollektivtrafik", "transit",
    "mat",
)


def _is_travel_category(category: str | None) -> bool:
    if not category:
        return False
    needle = category.lower()
    return any(token in needle for token in TRAVEL_CATEGORIES)


@dataclass(frozen=True)
class TripProposal:
    """Mellansteg innan Claude finslipar förslaget."""
    anchor_message_id: str
    message_ids: list[str]
    start_date: date
    end_date: date


# ---------- Steg 1 + 2: anchors + relaterade kvitton ----------


def _msg_date(msg: ProcessedMessage) -> date | None:
    """Plocka det mest specifika datumet vi har för ett kvitto.

    Föredrar receipt_date (extraherat från PDF) framför received_at
    (Gmail-mottagningstid), eftersom receipt_date speglar resans datum.
    """
    if msg.receipt_date:
        try:
            return datetime.strptime(msg.receipt_date[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            pass
    if msg.received_at:
        try:
            return msg.received_at.date()
        except Exception:  # noqa: BLE001
            return None
    return None


def _is_flight(msg: ProcessedMessage) -> bool:
    if msg.category and "flyg" in msg.category.lower():
        return True
    haystack = " ".join([
        (msg.vendor or "").lower(),
        (msg.sender or "").lower(),
        (msg.subject or "").lower(),
    ])
    return any(token in haystack for token in FLIGHT_VENDOR_TOKENS)


def find_flight_anchors(
    messages: Sequence[ProcessedMessage],
) -> list[ProcessedMessage]:
    """Hitta kvitton som ser ut som flygbiljetter — baseras på
    kategori eller vendor/subject-mönster."""
    return [m for m in messages if _is_flight(m)]


def _find_return_flight_date(
    outbound: ProcessedMessage,
    all_messages: Sequence[ProcessedMessage],
) -> date | None:
    """Hitta hemresan: tidigaste flygkvitto efter outbound, inom
    RETURN_FLIGHT_LOOKAHEAD_DAYS-fönstret. Returnerar None om inget
    sådant hittas — kallaren använder fallback-fönster."""
    outbound_d = _msg_date(outbound)
    if not outbound_d:
        return None
    upper = outbound_d + timedelta(days=RETURN_FLIGHT_LOOKAHEAD_DAYS)
    candidates: list[date] = []
    for m in all_messages:
        if m.message_id == outbound.message_id:
            continue
        if not _is_flight(m):
            continue
        d = _msg_date(m)
        if not d:
            continue
        if outbound_d < d <= upper:
            candidates.append(d)
    return min(candidates) if candidates else None


def find_related_receipts(
    anchor: ProcessedMessage,
    all_messages: Sequence[ProcessedMessage],
    *,
    excluded_patterns: Sequence[str] = (),
) -> list[ProcessedMessage]:
    """Hitta kvitton som troligen hör till samma resa som flygbiljetten.

    FAS 11.1.1 — striktare heuristik:
      1. Datum-fönstret är outbound→inbound (inte ±14d). När returflyg
         saknas faller vi tillbaka till outbound + 7d.
      2. Endast kvitton vars kategori liknar en resekategori
         (TRAVEL_CATEGORIES). Anchor-flyget är alltid med.
      3. Vendors i excluded_patterns (SaaS/prenumerationer) tas bort.
      4. Skälig summa (<5000 EUR).
      5. Anchor-kvittot är alltid inkluderat.
    """
    anchor_d = _msg_date(anchor)
    if not anchor_d:
        return [anchor]

    return_d = _find_return_flight_date(anchor, all_messages)
    if return_d is None:
        return_d = anchor_d + timedelta(days=WINDOW_DAYS_AFTER_FALLBACK)

    window_start = anchor_d - timedelta(days=WINDOW_DAYS_BEFORE)
    # En dags marginal efter sista flyget för t.ex. taxi-hem-kvitto
    # som kommer dagen efter återresan.
    window_end = return_d + timedelta(days=1)

    excluded = tuple(p.lower() for p in excluded_patterns if p)

    related: list[ProcessedMessage] = [anchor]
    for msg in all_messages:
        if msg.message_id == anchor.message_id:
            continue

        d = _msg_date(msg)
        if not d or not (window_start <= d <= window_end):
            continue

        if not _is_travel_category(msg.category):
            continue

        if msg.amount and msg.amount > LARGE_RECEIPT_THRESHOLD:
            continue

        if msg.vendor and excluded:
            haystack = msg.vendor.lower()
            if any(p in haystack for p in excluded):
                continue

        related.append(msg)
    return related


def _merge_overlapping_proposals(
    proposals: list[TripProposal],
) -> list[TripProposal]:
    """Slå samman förslag vars datumfönster överlappar — t.ex. tur-
    och returflyg samma resa. Behåll det första anchor_message_id som
    representativt.

    Algoritm: sortera på start_date, slå sedan ihop angränsande
    förslag där (a.end_date >= b.start_date - 1 day). I varje merge
    union:as message_ids."""
    if not proposals:
        return []
    ordered = sorted(proposals, key=lambda p: p.start_date)
    merged: list[TripProposal] = []
    current = ordered[0]
    current_ids = list(dict.fromkeys(current.message_ids))
    current_anchor = current.anchor_message_id

    for nxt in ordered[1:]:
        # Fönstren räknas som överlappande om de delar minst en dag
        # eller är direkt angränsande (en dag mellan).
        if nxt.start_date <= current.end_date + timedelta(days=1):
            current_ids.extend(
                m for m in nxt.message_ids if m not in current_ids
            )
            current = TripProposal(
                anchor_message_id=current_anchor,
                message_ids=current_ids,
                start_date=current.start_date,
                end_date=max(current.end_date, nxt.end_date),
            )
        else:
            merged.append(current)
            current = nxt
            current_ids = list(dict.fromkeys(current.message_ids))
            current_anchor = current.anchor_message_id
    merged.append(current)
    return merged


# ---------- Steg 3: Claude refine + lokal fallback ----------


def _format_iso(d: date) -> str:
    return d.isoformat()


def _local_fallback_title(start: date, end: date, msgs: Sequence[ProcessedMessage]) -> str:
    """Genererar en best-effort titel utan AI: 'Resa 30 apr - 2 maj 2026'
    eller 'Stockholm 30 apr - 2 maj 2026' om vi kan gissa destination
    från en hotell-vendor."""
    destination = _guess_destination_local(msgs)
    sv_months = {
        1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "maj", 6: "jun",
        7: "jul", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "dec",
    }
    s = f"{start.day} {sv_months[start.month]}"
    e = f"{end.day} {sv_months[end.month]} {end.year}"
    prefix = destination if destination else "Resa"
    return f"{prefix} {s} - {e}"[:200]


def _guess_destination_local(msgs: Sequence[ProcessedMessage]) -> str | None:
    """Lokal heuristik för destination utan AI — letar efter ord i
    subject/vendor som liknar en stadsidentifierare. Mycket grov.
    Returnerar None om vi inte kan gissa."""
    candidates = [
        "Stockholm", "Göteborg", "Malmö", "Helsinki", "Helsingfors",
        "Köpenhamn", "Copenhagen", "Oslo", "London", "Paris", "Berlin",
        "Amsterdam", "New York", "Zurich", "Munich", "München", "Madrid",
    ]
    text = " ".join(filter(None, [
        " ".join((m.subject or "") for m in msgs),
        " ".join((m.summary or "") for m in msgs),
        " ".join((m.vendor or "") for m in msgs),
    ]))
    for c in candidates:
        if re.search(rf"\b{re.escape(c)}\b", text, re.IGNORECASE):
            return c
    return None


def _summarize_messages_for_prompt(messages: Sequence[ProcessedMessage]) -> str:
    parts: list[str] = []
    for m in messages:
        parts.append(
            "\n".join([
                f"Message {m.message_id}:",
                f"- Vendor: {m.vendor or '?'}",
                f"- Receipt date: {m.receipt_date or '?'}",
                f"- Received at: {m.received_at.isoformat() if m.received_at else '?'}",
                f"- Amount: {m.amount} {m.currency or ''}".rstrip(),
                f"- Category: {m.category or '?'}",
                f"- Subject: {(m.subject or '')[:200]}",
                f"- Summary: {(m.summary or '')[:300]}",
            ])
        )
    return "\n\n".join(parts)


def _format_feedback_for_prompt(feedback_rows: Sequence[TripFeedback]) -> str:
    if not feedback_rows:
        return ""
    lines = ["", "## Tidigare feedback från användaren:"]
    for fb in feedback_rows:
        try:
            details_json = json.dumps(fb.details or {}, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            details_json = "{}"
        lines.append(f"- {fb.feedback_type}: {details_json}")
    return "\n".join(lines)


def _build_refine_prompt(
    messages: Sequence[ProcessedMessage],
    feedback_rows: Sequence[TripFeedback],
) -> str:
    summaries = _summarize_messages_for_prompt(messages)
    feedback = _format_feedback_for_prompt(feedback_rows)
    return f"""Du är en assistent som grupperar kvitton till resor.

## Kvitton att analysera:
{summaries}
{feedback}

## Uppgift:
1. Bedöm om dessa kvitton hör till samma resa.
2. Generera en kort titel (max 50 tecken): "Destination DATUM - DATUM ÅR"
3. Identifiera destination (stad, land om olika från användarens hemland).
4. Skriv en kort beskrivning (1-2 meningar) som "Affärsresa till X för Y".
5. Bekräfta start_date och end_date (kan vara olika från första/sista
   kvitto om något inte hör hit).
6. Ange din confidence 0-100. Var konservativ — om datum eller geografi
   inte passar, sänk confidence.

Returnera ENDAST giltig JSON i detta schema (ingen markdown):
{{
  "title": "...",
  "destination": "...",
  "description": "...",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "confidence": 85,
  "message_ids": ["..."],
  "rejected_message_ids": ["..."],
  "rejection_reasons": {{"msg_id": "anledning"}}
}}"""


def _parse_iso_date(raw: str | None, fallback: date) -> date:
    if not raw:
        return fallback
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return fallback


def _strip_codefences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    return s.strip()


def call_claude_for_trip(
    messages: Sequence[ProcessedMessage],
    feedback_history: Sequence[TripFeedback],
    *,
    client=None,
) -> dict | None:
    """Anropa Claude för att finslipa ett trip-förslag. Returnerar dict
    med titeln/beskrivning/confidence/etc., eller None vid API-fel."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    if client is None:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=settings.anthropic_api_key)
        except Exception:  # noqa: BLE001
            logger.exception("Kunde inte skapa Anthropic-client för trip-refine")
            return None

    prompt = _build_refine_prompt(messages, feedback_history)
    try:
        resp = client.messages.create(
            model=settings.claude_model,
            max_tokens=600,
            system=(
                "Du svarar ENDAST med giltig JSON. Inga kommentarer, "
                "ingen markdown."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Claude trip-refine API-fel")
        return None

    raw = "".join(
        block.text for block in resp.content
        if getattr(block, "type", "") == "text"
    )
    try:
        return json.loads(_strip_codefences(raw))
    except (ValueError, json.JSONDecodeError):
        logger.warning("Kunde inte parsa Claude-JSON: %r", raw[:300])
        return None


def _refine_proposal(
    proposal: TripProposal,
    msg_objects: Sequence[ProcessedMessage],
    feedback_history: Sequence[TripFeedback],
) -> dict:
    """Returnerar ett dict-objekt redo att sparas som Trip:
        {title, destination, description, start_date, end_date,
         confidence, message_ids}.

    Vid Claude-fel använder vi en best-effort lokal fallback (lägre
    confidence, lokal-genererad titel)."""
    claude_result = call_claude_for_trip(msg_objects, feedback_history)
    if claude_result and claude_result.get("title"):
        try:
            included_ids = list(claude_result.get("message_ids") or proposal.message_ids)
            # Filtrera bort id:n som inte fanns i original-proposal
            valid_ids = {m.message_id for m in msg_objects}
            included_ids = [mid for mid in included_ids if mid in valid_ids]
            if not included_ids:
                included_ids = list(proposal.message_ids)
            start = _parse_iso_date(claude_result.get("start_date"), proposal.start_date)
            end = _parse_iso_date(claude_result.get("end_date"), proposal.end_date)
            confidence_raw = claude_result.get("confidence", 50)
            try:
                confidence = max(0, min(100, int(confidence_raw)))
            except (TypeError, ValueError):
                confidence = 50
            return {
                "title": str(claude_result.get("title"))[:200],
                "destination": (
                    str(claude_result["destination"])[:100]
                    if claude_result.get("destination") else None
                ),
                "description": (
                    str(claude_result["description"])[:2000]
                    if claude_result.get("description") else None
                ),
                "start_date": start,
                "end_date": end,
                "confidence": confidence,
                "message_ids": included_ids,
                "anchor_message_id": proposal.anchor_message_id,
            }
        except Exception:  # noqa: BLE001
            logger.exception("Misslyckades plocka data från Claude-svar")

    # Fallback — ingen AI eller dåligt svar
    return {
        "title": _local_fallback_title(
            proposal.start_date, proposal.end_date, msg_objects,
        ),
        "destination": _guess_destination_local(msg_objects),
        "description": None,
        "start_date": proposal.start_date,
        "end_date": proposal.end_date,
        "confidence": 40,
        "message_ids": list(proposal.message_ids),
        "anchor_message_id": proposal.anchor_message_id,
    }


# ---------- Publik API ----------


def _candidate_messages(db: Session, lookback_days: int) -> list[ProcessedMessage]:
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    # Subquery: alla message_id:n som redan tillhör en aktiv eller
    # föreslagen resa (oavsett removed_at). Vi lägger inte ett kvitto
    # två gånger.
    already_in_trip = (
        db.query(TripMessage.message_id)
        .join(Trip, Trip.id == TripMessage.trip_id)
        .filter(Trip.status.in_(["active", "suggested"]))
        .filter(TripMessage.removed_at.is_(None))
    )
    return (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.processed_at >= cutoff)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(~ProcessedMessage.message_id.in_(already_in_trip))
        .all()
    )


def suggest_trips(db: Session, lookback_days: int = 90) -> list[dict]:
    """Analysera oprocessade kvitton och föreslå resor. Returnerar
    en lista av dict (samma format som `_refine_proposal`).
    Sparar INTE i DB — kallaren ansvarar för persistens."""
    candidates = _candidate_messages(db, lookback_days)
    if not candidates:
        return []

    anchors = find_flight_anchors(candidates)
    if not anchors:
        return []

    # Hämta SaaS-listan en gång per analys. Defensivt — om tabellen
    # saknas eller fel uppstår faller vi tillbaka till tom lista.
    try:
        from app.services.excluded_vendors import (
            list_excluded_vendor_patterns,
        )
        excluded_patterns = list_excluded_vendor_patterns(db)
    except Exception:  # noqa: BLE001
        logger.exception("Kunde inte läsa excluded_vendors — fortsätter utan filter")
        excluded_patterns = []

    proposals: list[TripProposal] = []
    for anchor in anchors:
        related = find_related_receipts(
            anchor, candidates, excluded_patterns=excluded_patterns,
        )
        if len(related) < MIN_RECEIPTS_FOR_TRIP:
            continue
        anchor_d = _msg_date(anchor) or datetime.utcnow().date()
        related_dates = [d for d in (_msg_date(m) for m in related) if d]
        start = min(related_dates) if related_dates else anchor_d
        end = max(related_dates) if related_dates else anchor_d
        proposals.append(TripProposal(
            anchor_message_id=anchor.message_id,
            message_ids=[m.message_id for m in related],
            start_date=start,
            end_date=end,
        ))

    if not proposals:
        return []

    proposals = _merge_overlapping_proposals(proposals)

    # Hämta feedback-historik en gång — samma history för alla refine
    feedback_history = (
        db.query(TripFeedback)
        .order_by(TripFeedback.created_at.desc())
        .limit(10)
        .all()
    )

    by_id = {m.message_id: m for m in candidates}
    refined: list[dict] = []
    for proposal in proposals:
        msg_objects = [
            by_id[mid] for mid in proposal.message_ids if mid in by_id
        ]
        if not msg_objects:
            continue
        refined.append(_refine_proposal(proposal, msg_objects, feedback_history))
    return refined


def find_overlapping_trip(
    db: Session, start: date, end: date, message_ids: Iterable[str],
) -> Trip | None:
    """Returnera en redan existerande resa (suggested eller active)
    som överlappar i datum OCH delar minst ett message_id med det
    nya förslaget. Används för att undvika duplicerade förslag."""
    msg_id_list = list(message_ids)
    if not msg_id_list:
        return None
    candidates = (
        db.query(Trip)
        .filter(Trip.status.in_(["suggested", "active"]))
        .filter(Trip.start_date <= end)
        .filter(Trip.end_date >= start)
        .all()
    )
    for trip in candidates:
        existing_ids = {
            r.message_id for r in
            db.query(TripMessage).filter(
                TripMessage.trip_id == trip.id,
                TripMessage.removed_at.is_(None),
            ).all()
        }
        if existing_ids & set(msg_id_list):
            return trip
    return None


def persist_suggestions(
    db: Session, suggestions: list[dict],
) -> list[Trip]:
    """Spara refined suggestions i DB. Skipar förslag som överlappar
    med en befintlig resa. Returnerar listan med Trip-objekt som
    faktiskt sparades."""
    saved: list[Trip] = []
    for s in suggestions:
        existing = find_overlapping_trip(
            db, s["start_date"], s["end_date"], s.get("message_ids", []),
        )
        if existing is not None:
            continue
        trip = Trip(
            title=s["title"],
            destination=s.get("destination"),
            start_date=s["start_date"],
            end_date=s["end_date"],
            description=s.get("description"),
            ai_confidence=s.get("confidence"),
            status="suggested",
            base_currency="EUR",
        )
        db.add(trip)
        db.flush()
        for msg_id in s.get("message_ids", []):
            db.add(TripMessage(
                trip_id=trip.id,
                message_id=msg_id,
                added_by="ai_suggestion",
            ))
        recalculate_trip_total(db, trip)
        saved.append(trip)
    if saved:
        db.commit()
    return saved


# ---------- Total-summan + valutakonvertering ----------


def _convert(
    db: Session,
    amount: float | None,
    from_currency: str | None,
    to_currency: str,
    on_date: date | None,
) -> Decimal | None:
    """Konvertera amount till to_currency. Använder cached ECB-kurs.
    Returnerar None om någon parameter saknas eller om kursen inte
    går att hämta."""
    if amount is None or not from_currency or not to_currency:
        return None
    if from_currency.upper() == to_currency.upper():
        try:
            return Decimal(str(amount))
        except (InvalidOperation, ValueError):
            return None
    if not on_date:
        return None
    try:
        from app.services.currency_converter import get_rate
        rate = get_rate(
            on_date.isoformat(), from_currency, to_currency, db=db,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Currency-rate-fetch misslyckades vid trip-total")
        return None
    if rate is None:
        return None
    try:
        return Decimal(str(amount)) * Decimal(str(rate))
    except (InvalidOperation, ValueError):
        return None


def recalculate_trip_total(db: Session, trip: Trip) -> None:
    """Summera alla aktiva trip_messages omräknat till trip.base_currency.
    Sätter trip.total_amount. Kvitton utan amount/currency hoppas över
    tyst — totalen är best-effort, ej revisorisk."""
    rows = (
        db.query(ProcessedMessage)
        .join(TripMessage, TripMessage.message_id == ProcessedMessage.message_id)
        .filter(TripMessage.trip_id == trip.id)
        .filter(TripMessage.removed_at.is_(None))
        .all()
    )
    total = Decimal("0")
    for msg in rows:
        d = _msg_date(msg)
        converted = _convert(
            db, msg.amount, msg.currency, trip.base_currency or "EUR", d,
        )
        if converted is not None:
            total += converted
    # Avrunda till 2 decimaler för persistens.
    trip.total_amount = total.quantize(Decimal("0.01"))


# ---------- Trip CRUD-helpers ----------


def serialize_trip(db: Session, trip: Trip) -> dict:
    """Returnera Trip + dess kvitton som ett dict redo för JSON-svar."""
    rows = (
        db.query(TripMessage, ProcessedMessage)
        .join(
            ProcessedMessage,
            ProcessedMessage.message_id == TripMessage.message_id,
        )
        .filter(TripMessage.trip_id == trip.id)
        .filter(TripMessage.removed_at.is_(None))
        .order_by(ProcessedMessage.received_at.asc().nullsfirst())
        .all()
    )
    messages = []
    for tm, msg in rows:
        messages.append({
            "id": msg.id,
            "message_id": msg.message_id,
            "vendor": msg.vendor,
            "amount": msg.amount,
            "currency": msg.currency,
            "receipt_date": msg.receipt_date,
            "received_at": (
                msg.received_at.isoformat() if msg.received_at else None
            ),
            "category": msg.category,
            "subject": msg.subject,
            "summary": msg.summary,
            "added_by": tm.added_by,
        })

    return {
        "id": trip.id,
        "title": trip.title,
        "destination": trip.destination,
        "start_date": trip.start_date.isoformat() if trip.start_date else None,
        "end_date": trip.end_date.isoformat() if trip.end_date else None,
        "total_amount": (
            float(trip.total_amount) if trip.total_amount is not None else None
        ),
        "base_currency": trip.base_currency,
        "status": trip.status,
        "created_at": trip.created_at.isoformat() if trip.created_at else None,
        "user_decision_at": (
            trip.user_decision_at.isoformat() if trip.user_decision_at else None
        ),
        "ai_confidence": trip.ai_confidence,
        "description": trip.description,
        "user_edited": bool(trip.user_edited),
        "netvisor_trip_id": trip.netvisor_trip_id,
        "netvisor_synced_at": (
            trip.netvisor_synced_at.isoformat()
            if trip.netvisor_synced_at else None
        ),
        # FAS 11.5.1 — per diem
        "destination_country": trip.destination_country,
        "departure_home_at": (
            trip.departure_home_at.isoformat()
            if trip.departure_home_at else None
        ),
        "return_home_at": (
            trip.return_home_at.isoformat()
            if trip.return_home_at else None
        ),
        "trip_route": trip.trip_route,
        "per_diem_amount": (
            float(trip.per_diem_amount)
            if trip.per_diem_amount is not None else None
        ),
        "per_diem_currency": trip.per_diem_currency,
        "per_diem_calculation": trip.per_diem_calculation,
        "messages": messages,
    }


def accept_trip(db: Session, trip: Trip) -> dict:
    if trip.status != "suggested":
        return serialize_trip(db, trip)
    trip.status = "active"
    trip.user_decision_at = datetime.utcnow()
    db.add(TripFeedback(
        trip_id=trip.id, feedback_type="accepted", details={},
    ))
    db.commit()
    return serialize_trip(db, trip)


def reject_trip(db: Session, trip: Trip) -> dict:
    if trip.status != "suggested":
        return serialize_trip(db, trip)
    trip.status = "rejected"
    trip.user_decision_at = datetime.utcnow()
    db.add(TripFeedback(
        trip_id=trip.id, feedback_type="rejected", details={},
    ))
    db.commit()
    return serialize_trip(db, trip)


def archive_trip(db: Session, trip: Trip) -> dict:
    trip.status = "archived"
    db.commit()
    return serialize_trip(db, trip)


def edit_trip(
    db: Session,
    trip: Trip,
    *,
    title: str | None = None,
    destination: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    description: str | None = None,
    add_message_ids: Sequence[str] | None = None,
    remove_message_ids: Sequence[str] | None = None,
) -> dict:
    """Användaren redigerar en resa. Loggar ändringar i trip_feedback."""
    changes: dict = {}

    if title is not None and title != trip.title:
        changes["title"] = {"from": trip.title, "to": title}
        trip.title = title[:200]

    if destination is not None and destination != trip.destination:
        changes["destination"] = {"from": trip.destination, "to": destination}
        trip.destination = (destination or None)
        if trip.destination:
            trip.destination = trip.destination[:100]

    if start_date is not None and start_date != trip.start_date:
        changes["start_date"] = {
            "from": trip.start_date.isoformat() if trip.start_date else None,
            "to": start_date.isoformat(),
        }
        trip.start_date = start_date

    if end_date is not None and end_date != trip.end_date:
        changes["end_date"] = {
            "from": trip.end_date.isoformat() if trip.end_date else None,
            "to": end_date.isoformat(),
        }
        trip.end_date = end_date

    if description is not None and description != trip.description:
        changes["description"] = {
            "from": trip.description, "to": description,
        }
        trip.description = description

    added: list[str] = []
    removed: list[str] = []

    for msg_id in add_message_ids or []:
        existing = (
            db.query(TripMessage)
            .filter(TripMessage.trip_id == trip.id)
            .filter(TripMessage.message_id == msg_id)
            .first()
        )
        if existing is not None:
            if existing.removed_at is not None:
                existing.removed_at = None
                added.append(msg_id)
        else:
            db.add(TripMessage(
                trip_id=trip.id,
                message_id=msg_id,
                added_by="manual",
            ))
            added.append(msg_id)

    for msg_id in remove_message_ids or []:
        existing = (
            db.query(TripMessage)
            .filter(TripMessage.trip_id == trip.id)
            .filter(TripMessage.message_id == msg_id)
            .filter(TripMessage.removed_at.is_(None))
            .first()
        )
        if existing is not None:
            existing.removed_at = datetime.utcnow()
            removed.append(msg_id)

    if added or removed:
        changes["messages"] = {"added": added, "removed": removed}

    if changes:
        trip.user_edited = True
        db.add(TripFeedback(
            trip_id=trip.id, feedback_type="edited", details=changes,
        ))
        # Flush så recalculate ser nyligen uppdaterade trip_messages
        # (sessionen kör med autoflush=False).
        db.flush()
        # Räkna om totalsumman när kvitton ändras
        recalculate_trip_total(db, trip)
        db.commit()

    return serialize_trip(db, trip)


def save_trip_feedback(
    db: Session,
    trip: Trip,
    feedback_type: str,
    details: dict | None = None,
) -> TripFeedback:
    """Logga frittextfeedback (wrong_grouping, missing_receipts, ...)."""
    fb = TripFeedback(
        trip_id=trip.id,
        feedback_type=feedback_type[:50],
        details=details or {},
    )
    db.add(fb)
    db.commit()
    return fb


def trip_stats(db: Session) -> dict:
    """Aggregat för Resor-vyn."""
    from sqlalchemy import func as sql_func

    active = (
        db.query(sql_func.count(Trip.id))
        .filter(Trip.status == "active")
        .scalar() or 0
    )
    suggested = (
        db.query(sql_func.count(Trip.id))
        .filter(Trip.status == "suggested")
        .scalar() or 0
    )
    total_amount = Decimal("0")
    rows = (
        db.query(Trip.total_amount)
        .filter(Trip.status == "active")
        .filter(Trip.total_amount.isnot(None))
        .all()
    )
    for (amt,) in rows:
        if amt is not None:
            total_amount += Decimal(str(amt))
    return {
        "active": int(active),
        "suggested": int(suggested),
        "total_amount_eur": float(total_amount.quantize(Decimal("0.01"))),
    }
