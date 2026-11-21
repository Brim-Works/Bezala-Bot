"""C43 — privat utlägg på kortrader + gemensam normalisering.

Bygger på C42:s `card_lines` (spegel av Bezalas kortrader utan kvitto).
C43 lägger till statusen 'private_expense': privat köp på jobbkortet (t.ex.
HEL-parkering 8,49 EUR 2026-08-01). Rutinen enligt Finance (Riikka):
kommentar i Bezala "Private expense – to be deducted from salary." så drar
lönen av beloppet. Räknas som hanterad i list_missing (inget larm).

Inget skickas någonsin för attest automatiskt — ett utkast skapas bara när
Mikko ber om det (create_draft), med state=unapproved.

Parkeringsrader (HEL/Moovy/Europark/Finavia) som INTE ligger inom en
kalenderresa flaggas 'possible_private' så Mikko får frågan direkt.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import CalendarTrip, CalendarTripDay, CardLine

logger = logging.getLogger(__name__)

STATUS_PRIVATE_EXPENSE = "private_expense"

PRIVATE_EXPENSE_COMMENT = "Private expense – to be deducted from salary."

FLAG_POSSIBLE_PRIVATE = "possible_private"

# Parkeringsleverantörer på kortrader. MOOVY-rader heter alltid
# "MOOVY, TAMPERE" (bolagets säte) men används för HEL-flygplatsparkering.
_PARKING_TOKENS = (
    "MOOVY", "EUROPARK", "FINAVIA", "AIRPARK", "ALIPARK", "APCOA",
    "PARKING", "PARKERING", "PYSAKOINTI", "PYSÄKÖINTI", "P-HELSINKI",
    "EASYPARK", "Q-PARK",
)

# Kortraden kan debiteras flera dagar efter parkeringen. Resefönstret
# utökas därför: 1 dag före resan, 3 dagar efter hemkomst.
TRIP_MARGIN_BEFORE_DAYS = 1
TRIP_MARGIN_AFTER_DAYS = 3


class BillLineStatusError(ValueError):
    """Ogiltig indata (okänd kortrad, fel status osv.)."""


# ---------- Normalisering av Bezalas missing_receipts ----------

_MISSING_AMOUNT_RE = re.compile(r"(-?\d+(?:[.,]\d{1,2})?)\s+([A-Z]{3})\s*$")


def parse_amount_from_description(desc: str | None) -> tuple[float | None, str | None]:
    """'NAMN: VENDOR, PLATS, LAND 28.54 EUR' → (28.54, 'EUR')."""
    if not desc:
        return None, None
    match = _MISSING_AMOUNT_RE.search(desc.strip())
    if not match:
        return None, None
    try:
        return float(match.group(1).replace(",", ".")), match.group(2)
    except ValueError:
        return None, None


def normalize_missing_receipt(raw: dict) -> dict:
    """Bezalas missing_receipt-rad → vår shape.

    amount/currency: strukturerade fält om Bezala skickar dem (kan vara
    originalvalutan, t.ex. 750 SEK), annars beloppet sist i beskrivningen
    (debiterat EUR-belopp). C43: `amount_eur` = EUR-beloppet ur beskrivningen
    när det skiljer sig från originalvalutan, så matchningen kan jämföra
    EUR-kvitton direkt."""
    bill_line_id = raw.get("bill_line_id") or raw.get("id")
    description = (
        raw.get("description") or raw.get("merchant") or raw.get("name") or ""
    )
    amount = raw.get("amount")
    if amount is None:
        amount = raw.get("sum")
    currency = raw.get("currency")
    parsed_amount, parsed_currency = parse_amount_from_description(description)
    if amount is None:
        amount = parsed_amount
    if not currency:
        currency = parsed_currency
    currency = currency or "EUR"
    amount_eur = None
    if parsed_currency == "EUR" and parsed_amount is not None:
        if currency.upper() != "EUR":
            amount_eur = parsed_amount
    try:
        amount = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount = None
    out = {
        "id": bill_line_id,
        "description": description,
        "amount": amount,
        "currency": currency,
        "date": (
            raw.get("date") or raw.get("transaction_date") or raw.get("purchase_date")
        ),
    }
    if amount_eur is not None:
        out["amount_eur"] = amount_eur
    return out


# ---------- Status på card_lines (C42-tabellen) ----------


def get_card_line(db: Session, bill_line_id: Any) -> CardLine | None:
    bid = str(bill_line_id or "").strip()
    if not bid:
        return None
    return db.query(CardLine).filter(CardLine.bill_line_id == bid).first()


def status_map(db: Session) -> dict[str, str]:
    """bill_line_id → status för rader Mikko hanterat."""
    return {
        c.bill_line_id: c.status
        for c in db.query(CardLine)
        .filter(CardLine.status.in_(("private_expense", "receipt_not_available")))
        .all()
    }


def mark_private_expense(
    db: Session, bill_line_id: Any, note: str | None = None, *, sync=None,
) -> CardLine:
    """Sätt status 'private_expense' på kortraden. Finns raden inte i
    card_lines än (ny i Bezala) synkas listan först. Committar."""
    from app.services.calendar_trip_edit import TripEditError
    from app.services.trip_completeness import (
        set_card_line_status,
        sync_card_lines_from_bezala,
    )

    if get_card_line(db, bill_line_id) is None:
        (sync or sync_card_lines_from_bezala)(db)
    try:
        set_card_line_status(db, str(bill_line_id), STATUS_PRIVATE_EXPENSE, note=note)
    except TripEditError as exc:
        raise BillLineStatusError(str(exc)) from exc
    row = get_card_line(db, bill_line_id)
    logger.info("C43 kortrad %s → private_expense (note=%r)", bill_line_id, note)
    return row


def private_expense_description(note: str | None = None, base: str | None = None) -> str:
    """Engelsk Bezala-kommentar för privat utlägg.

    'Private expense – to be deducted from salary.' + ev. Mikkos notis och
    ev. kvittots egen beskrivning (när ett kvitto kopplas till raden)."""
    parts = [PRIVATE_EXPENSE_COMMENT]
    for extra in (base, note):
        extra = (extra or "").strip()
        if extra and extra not in parts:
            parts.append(extra)
    return " ".join(parts)


# ---------- Kalenderresor ----------


def _parse_day(raw: Any) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def trip_for_date(db: Session, raw_date: Any) -> CalendarTrip | None:
    """Kalenderresa som täcker datumet (med marginal för sena debiteringar)."""
    d = _parse_day(raw_date)
    if d is None:
        return None
    lo = d - timedelta(days=TRIP_MARGIN_AFTER_DAYS)
    hi = d + timedelta(days=TRIP_MARGIN_BEFORE_DAYS)
    return (
        db.query(CalendarTrip)
        .filter(CalendarTrip.start_date <= hi)
        .filter(CalendarTrip.end_date >= lo)
        .order_by(CalendarTrip.start_date.asc())
        .first()
    )


def trip_context(db: Session, raw_date: Any) -> dict | None:
    """{'trip_id', 'countries', 'cities'} för matchningens plats-signal."""
    trip = trip_for_date(db, raw_date)
    if trip is None:
        return None
    countries: list[str] = []
    cities: list[str] = []

    def _add(seq: list[str], val: str | None) -> None:
        for part in str(val or "").split(","):
            p = part.strip()
            if p and p not in seq:
                seq.append(p)

    _add(countries, trip.country)
    if trip.destination:
        cities.append(trip.destination.strip())
    for day in db.query(CalendarTripDay).filter(CalendarTripDay.trip_id == trip.id):
        _add(countries, day.country)
        if day.destination and day.destination.strip() not in cities:
            cities.append(day.destination.strip())
    return {"trip_id": trip.id, "countries": countries, "cities": cities}


# ---------- list_missing ----------


def is_parking_line(description: str | None) -> bool:
    text = (description or "").upper()
    return any(tok in text for tok in _PARKING_TOKENS)


def possible_private_flags(db: Session, card_line: CardLine) -> list[str]:
    """['possible_private'] för parkering utanför kalenderresa, annars []."""
    if card_line.status != "missing":
        return []
    if not is_parking_line(card_line.description):
        return []
    if trip_for_date(db, card_line.txn_date) is not None:
        return []
    return [FLAG_POSSIBLE_PRIVATE]


# ---------- Bezala-utkast för privat utlägg ----------


def _note_pdf(snapshot: dict, note: str | None) -> bytes:
    from html import escape

    from app.services.html_pdf_converter import html_to_pdf

    desc = escape(str(snapshot.get("description") or ""))
    amount = snapshot.get("amount")
    cur = escape(str(snapshot.get("currency") or ""))
    d = escape(str(snapshot.get("date") or ""))
    note_html = f"<p>Note: {escape(note)}</p>" if note else ""
    html = (
        "<html><body style='font-family: sans-serif'>"
        "<h2>Private expense</h2>"
        f"<p>{escape(PRIVATE_EXPENSE_COMMENT)}</p>"
        f"<p>Card transaction: {desc}<br>Amount: {amount} {cur}<br>Date: {d}</p>"
        f"{note_html}"
        "<p>Paid by mistake with the company card.</p>"
        "</body></html>"
    )
    return html_to_pdf(html)


def create_private_expense_draft(db: Session, bezala, bill_line_id: Any) -> str:
    """Skapa ett Bezala-UTKAST (state=unapproved, skickas inte för attest)
    kopplat till kortraden, med engelsk kommentar. Kräver att raden är
    markerad private_expense. Idempotent. Returnerar transaction-id."""
    row = get_card_line(db, bill_line_id)
    if row is None or row.status != STATUS_PRIVATE_EXPENSE:
        raise BillLineStatusError("Raden är inte markerad som private_expense")
    if row.bezala_transaction_id:
        return row.bezala_transaction_id
    snap = {
        "description": row.description, "amount": row.amount,
        "currency": row.currency,
        "date": row.txn_date.isoformat() if row.txn_date else None,
    }
    pdf = _note_pdf(snap, row.note)
    result = bezala.attach_file(
        row.bill_line_id,
        f"private-expense-{row.bill_line_id}.pdf",
        pdf,
        description=private_expense_description(row.note),
    )
    row.bezala_transaction_id = str(result.attachment_id)
    db.commit()
    logger.info(
        "C43 private_expense-utkast skapat: bill_line=%s tx=%s (ej skickat för attest)",
        row.bill_line_id, row.bezala_transaction_id,
    )
    return row.bezala_transaction_id
