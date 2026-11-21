"""C43 — status på Bezala-kortrader + `list_missing`.

Mikko kan markera en kortrad som:
  - 'private_expense'       — privat köp på jobbkortet (t.ex. HEL-parkering
                              8,49 EUR 2026-08-01). Rutinen enligt Finance
                              (Riikka): kommentar i Bezala "Private expense –
                              to be deducted from salary." så drar lönen av
                              beloppet.
  - 'receipt_not_available' — leverantören ger inget kvitto (förklaring i
                              note).

Båda räknas som hanterade i list_missing (inget larm). Inget skickas någonsin
för attest automatiskt — ett utkast skapas bara när Mikko ber om det, och då
med state=unapproved (samma väg som vanliga kopplingar).

Dessutom: parkeringsrader (HEL/Moovy/Europark/Finavia) som INTE ligger inom
en kalenderresa flaggas 'possible_private' så Mikko får frågan direkt.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import BillLineStatus, CalendarTrip, CalendarTripDay

logger = logging.getLogger(__name__)

STATUS_PRIVATE_EXPENSE = "private_expense"
STATUS_RECEIPT_NOT_AVAILABLE = "receipt_not_available"
VALID_STATUSES = (STATUS_PRIVATE_EXPENSE, STATUS_RECEIPT_NOT_AVAILABLE)

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
    """Ogiltig indata (okänd status, tom bill_line_id osv.)."""


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


# ---------- Status-CRUD ----------


def _clean_id(bill_line_id: Any) -> str:
    s = str(bill_line_id or "").strip()
    if not s:
        raise BillLineStatusError("bill_line_id saknas")
    return s


def get_status(db: Session, bill_line_id: Any) -> BillLineStatus | None:
    return db.get(BillLineStatus, _clean_id(bill_line_id))


def status_map(db: Session) -> dict[str, BillLineStatus]:
    return {r.bill_line_id: r for r in db.query(BillLineStatus).all()}


def set_status(
    db: Session,
    bill_line_id: Any,
    status: str,
    *,
    note: str | None = None,
    snapshot: dict | None = None,
) -> BillLineStatus:
    if status not in VALID_STATUSES:
        raise BillLineStatusError(
            f"Okänd status {status!r} — tillåtna: {', '.join(VALID_STATUSES)}"
        )
    bid = _clean_id(bill_line_id)
    note = (note or "").strip() or None
    if status == STATUS_RECEIPT_NOT_AVAILABLE and not note:
        raise BillLineStatusError(
            "receipt_not_available kräver en förklaring (note)"
        )
    row = db.get(BillLineStatus, bid)
    if row is None:
        row = BillLineStatus(bill_line_id=bid, status=status)
        db.add(row)
    row.status = status
    row.note = note
    if snapshot:
        row.description = snapshot.get("description") or row.description
        amt = snapshot.get("amount")
        row.amount = float(amt) if amt is not None else row.amount
        row.currency = snapshot.get("currency") or row.currency
        row.line_date = snapshot.get("date") or row.line_date
    db.commit()
    logger.info("C43 bill_line %s → %s (note=%r)", bid, status, note)
    return row


def clear_status(db: Session, bill_line_id: Any) -> bool:
    row = db.get(BillLineStatus, _clean_id(bill_line_id))
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def serialize_status(row: BillLineStatus | None) -> dict | None:
    if row is None:
        return None
    return {
        "bill_line_id": row.bill_line_id,
        "status": row.status,
        "note": row.note,
        "description": row.description,
        "amount": row.amount,
        "currency": row.currency,
        "date": row.line_date,
        "bezala_transaction_id": row.bezala_transaction_id,
        "bezala_comment": (
            private_expense_description(row.note)
            if row.status == STATUS_PRIVATE_EXPENSE else None
        ),
    }


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


def build_missing_list(
    db: Session,
    rows: Iterable[dict],
    *,
    include_handled: bool = False,
) -> dict:
    """Saknade kvitton (normaliserade rader) + status/flaggor.

    Hanterade rader (private_expense / receipt_not_available) räknas inte
    som saknade; de returneras bara med include_handled=True."""
    statuses = status_map(db)
    missing: list[dict] = []
    handled: list[dict] = []
    for row in rows:
        bid = str(row.get("id"))
        st = statuses.get(bid)
        item = dict(row)
        item["status"] = st.status if st else "missing"
        item["status_note"] = st.note if st else None
        flags: list[str] = []
        trip = trip_for_date(db, row.get("date"))
        item["trip_id"] = trip.id if trip else None
        if st is None and is_parking_line(row.get("description")) and trip is None:
            flags.append(FLAG_POSSIBLE_PRIVATE)
            item["question"] = (
                "Parking outside any calendar trip — private expense? "
                "Use mark_private_expense if so."
            )
        item["flags"] = flags
        (handled if st else missing).append(item)
    out: dict = {
        "missing_count": len(missing),
        "possible_private_count": sum(
            1 for m in missing if FLAG_POSSIBLE_PRIVATE in m["flags"]
        ),
        "missing": missing,
    }
    if include_handled:
        out["handled"] = handled
    else:
        out["handled_count"] = len(handled)
    return out


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


def create_private_expense_draft(
    db: Session,
    bezala,
    bill_line_id: Any,
    *,
    snapshot: dict | None = None,
) -> str:
    """Skapa ett Bezala-UTKAST (state=unapproved, skickas inte för attest)
    kopplat till kortraden, med engelsk kommentar. Kräver att raden redan
    är markerad private_expense. Returnerar transaction-id."""
    row = get_status(db, bill_line_id)
    if row is None or row.status != STATUS_PRIVATE_EXPENSE:
        raise BillLineStatusError(
            "Raden är inte markerad som private_expense"
        )
    if row.bezala_transaction_id:
        return row.bezala_transaction_id
    snap = snapshot or {
        "description": row.description, "amount": row.amount,
        "currency": row.currency, "date": row.line_date,
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
