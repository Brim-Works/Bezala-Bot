"""C47 Del 5 — återbetalningar och dubbletter (lätt version).

* expect_refund(bill_line_id, note): markera att en kortrad ska
  återbetalas (t.ex. avbokad resa).
* Kortrad med negativt belopp matchas mot väntad återbetalning (samma
  leverantör, samma absolutbelopp ±1 %) och kopplas ihop (bara i Bezala
  Bots egen databas).
* Väntad återbetalning äldre än 30 dagar → list_missing + svep-kön.
* Dubbletter: flaggas bara när samma leverantör + samma belopp samma dag
  har FÄRRE kvitton än kortrader (två köp + två kvitton = ingen flagga).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models import CardLine, ExpectedRefund, MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

AMOUNT_TOLERANCE = 0.01
OVERDUE_DAYS = 30
REFUND_MATCHED = "refund_matched"
SEED_TASK = "c47_seed_finnair_refund"

from app.services.calendar_trip_edit import TripEditError  # noqa: E402


class RefundError(TripEditError):
    """Fel som visas för Mikko (engelska)."""


def serialize(r: ExpectedRefund, today: date | None = None) -> dict:
    today = today or date.today()
    since = r.created_at.date() if r.created_at else None
    return {
        "id": r.id, "bill_line_id": r.bill_line_id, "vendor": r.vendor,
        "amount": r.amount, "currency": r.currency,
        "date": r.txn_date.isoformat() if r.txn_date else None,
        "note": r.note, "status": r.status,
        "refund_bill_line_id": r.refund_bill_line_id,
        "matched_at": r.matched_at.isoformat() if r.matched_at else None,
        "age_days": (today - since).days if since else None,
    }


def _from_card_line(db: Session, bid: str) -> dict:
    """Leverantör/belopp från Bezala Bots spegel av kortraden (om den finns)."""
    line = db.query(CardLine).filter(CardLine.bill_line_id == bid).first()
    if line is None:
        return {}
    return {"vendor": line.vendor or line.description, "amount": line.amount,
            "currency": line.currency, "txn_date": line.txn_date}


def expect_refund(db: Session, bill_line_id: str, note: str | None = None, *,
                  vendor: str | None = None, amount: float | None = None,
                  currency: str | None = None, txn_date: date | None = None) -> dict:
    from app.services.calendar_trip_edit import clean_text

    bid = str(bill_line_id or "").strip()
    if not bid.isdigit():
        raise RefundError("bill_line_id must be the numeric Bezala card line id.")
    info = _from_card_line(db, bid)
    vendor = vendor or info.get("vendor")
    amount = amount if amount is not None else info.get("amount")
    if amount is None:
        raise RefundError(
            f"Card line {bid} is not in Bezala Bot's card line mirror - give vendor and amount."
        )
    row = (db.query(ExpectedRefund)
           .filter(ExpectedRefund.bill_line_id == bid)
           .filter(ExpectedRefund.status == "open").first())
    if row is None:
        row = ExpectedRefund(bill_line_id=bid, status="open")
        db.add(row)
    row.vendor = vendor
    row.amount = abs(float(amount))
    row.currency = (currency or info.get("currency") or "EUR").upper()
    row.txn_date = txn_date or info.get("txn_date")
    note = clean_text(note, "note")
    if note is not None:
        row.note = note
    db.commit()
    logger.info("[c47-refund] väntar återbetalning: %s %s %s (%s)", bid, row.vendor,
                row.amount, row.note)
    return serialize(row)


def seed_finnair_refund(db: Session) -> dict | None:
    """C47: Finnair 134,48 € (bill_line 2513379, avbokad resa 26 nov)."""
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == SEED_TASK).first():
        return None
    out = expect_refund(db, "2513379", "Cancelled trip 26 Nov 2026 - refund expected",
                        vendor="Finnair", amount=134.48, currency="EUR")
    db.add(MaintenanceTask(name=SEED_TASK))
    db.commit()
    return out


def _same_vendor(refund: ExpectedRefund, line: CardLine) -> bool:
    from app.services.receipt_matcher import vendor_similarity
    from app.services.trip_completeness import _vendor_words

    words = _vendor_words(refund.vendor)
    hay = f"{line.description or ''} {line.vendor or ''}".lower()
    if words and any(w in hay for w in words):
        return True
    return vendor_similarity(line.description, refund.vendor, None) >= 0.6


def match_refunds(db: Session) -> list[dict]:
    """Negativa kortrader ↔ väntade återbetalningar. Committar."""
    open_refunds = db.query(ExpectedRefund).filter(ExpectedRefund.status == "open").all()
    if not open_refunds:
        return []
    lines = (db.query(CardLine)
             .filter(CardLine.amount < 0)
             .filter(CardLine.status == "missing").all())
    out = []
    for line in sorted(lines, key=lambda c: c.txn_date or date.min):
        for r in open_refunds:
            if r.status != "open":
                continue
            if r.currency and line.currency and r.currency.upper() != line.currency.upper():
                continue
            if abs(abs(float(line.amount)) - r.amount) > AMOUNT_TOLERANCE * r.amount:
                continue
            if not _same_vendor(r, line):
                continue
            r.status = "matched"
            r.refund_bill_line_id = line.bill_line_id
            r.matched_at = datetime.utcnow()
            line.status = REFUND_MATCHED
            line.set_by = "auto"
            line.note = f"Refund of bill line {r.bill_line_id}"
            out.append({"refund_id": r.id, "bill_line_id": r.bill_line_id,
                        "refund_bill_line_id": line.bill_line_id, "amount": line.amount})
            logger.info("[c47-refund] återbetalning %s kopplad till %s", line.bill_line_id,
                        r.bill_line_id)
            break
    db.commit()
    return out


def list_refunds(db: Session, status: str | None = None) -> list[dict]:
    q = db.query(ExpectedRefund)
    if status:
        q = q.filter(ExpectedRefund.status == status)
    return [serialize(r) for r in q.order_by(ExpectedRefund.id.desc()).all()]


def overdue_refunds(db: Session, today: date | None = None) -> list[dict]:
    today = today or date.today()
    return [r for r in list_refunds(db, "open")
            if r["age_days"] is not None and r["age_days"] > OVERDUE_DAYS]


# ---------- dubbletter ----------


DUP_WINDOW_DAYS = 5
DUP_AMOUNT_TOLERANCE = 0.10  # samma som valutaomräkningen i matchningen


def _receipt_amount_matches(m: ProcessedMessage, line: CardLine, rate) -> bool:
    """Kvittots belopp ≈ kortradens (samma valuta ±2 %, annars via ECB-kurs
    ±10 %, eller kortradens EUR-belopp). Kopplad snapshot räknas också."""
    from app.services.receipt_matcher import _amount_matches_via_conversion

    amount = float(line.amount)
    if (m.bezala_payment_amount is not None
            and abs(float(m.bezala_payment_amount) - amount) < 0.02):
        return True
    if m.amount is None:
        return False
    cur = (line.currency or "").upper()
    mcur = (m.currency or "").upper()
    if not cur or not mcur or cur == mcur:
        return abs(float(m.amount) - amount) <= 0.02 * max(amount, 0.01)
    ok, _, _ = _amount_matches_via_conversion(
        amount, cur, float(m.amount), mcur,
        line.txn_date.isoformat() if line.txn_date else None, rate)
    return bool(ok)


def _receipt_days(m: ProcessedMessage) -> list[date]:
    from app.services.trip_completeness import _parse_day

    return [d for d in (_parse_day(m.bezala_payment_date), _parse_day(m.receipt_date),
                        _parse_day(m.received_at)) if d]


def matching_receipts(receipts: list[ProcessedMessage], line: CardLine, rate) -> list:
    """Alla kvitton (kopplade eller inte) för kortraden: leverantör +
    belopp (med valutaomräkning) + datum ±5 dagar."""
    from app.services.receipt_matcher import vendor_similarity

    out = []
    for m in receipts:
        days = _receipt_days(m)
        if not days or min(abs((d - line.txn_date).days) for d in days) > DUP_WINDOW_DAYS:
            continue
        if not _receipt_amount_matches(m, line, rate):
            continue
        text = line.description or line.vendor or ""
        if (vendor_similarity(text, m.vendor, m.sender) < 0.6
                and vendor_similarity(text, m.bezala_payment_merchant, None) < 0.6):
            continue
        out.append(m)
    return out


def duplicate_charges(db: Session) -> list[dict]:
    """C48: samma leverantör + belopp + dag med FLER kortrader än kvitton.
    Alla kvitton räknas (kopplade eller inte, valutaomräknade, ±5 dagar);
    papperskorg och dubblett-markerade räknas inte. Grupper där ingen
    kortrad längre saknar kvitto i Bezala flaggas aldrig."""
    from app.services.currency_converter import make_db_rate_provider

    groups: dict[tuple, list[CardLine]] = defaultdict(list)
    for c in db.query(CardLine).all():
        if c.txn_date is None or not c.amount or c.amount <= 0:
            continue
        key = ((c.vendor or c.description or "").strip().lower(), c.txn_date,
               round(float(c.amount), 2), (c.currency or "").upper())
        groups[key].append(c)
    multi = {k: v for k, v in groups.items() if len(v) >= 2}
    if not multi:
        return []
    receipts = (db.query(ProcessedMessage)
                .filter(ProcessedMessage.deleted_at.is_(None))
                .filter(ProcessedMessage.status == "saved")
                .filter(ProcessedMessage.duplicate_of.is_(None)).all())
    rate = make_db_rate_provider(db)
    keys = sorted(multi, key=lambda k: (k[1], k[0]))
    # Varje kvitto räknas för högst en grupp (närmaste dag först) och en
    # grupp tar högst lika många kvitton som den har kortrader.
    pairs = []
    for gi, key in enumerate(keys):
        line = multi[key][0]
        for m in matching_receipts(receipts, line, rate):
            dist = min(abs((d - line.txn_date).days) for d in _receipt_days(m))
            pairs.append((dist, gi, m.id, m))
    pairs.sort(key=lambda p: (p[0], p[1], p[2]))
    found: dict[int, list] = defaultdict(list)
    used: set[int] = set()
    for _, gi, mid, m in pairs:
        if mid in used or len(found[gi]) >= len(multi[keys[gi]]):
            continue
        used.add(mid)
        found[gi].append(m)
    out = []
    for gi, (vendor, d, amount, cur) in enumerate(keys):
        lines = multi[keys[gi]]
        if not any(c.status == "missing" for c in lines):
            continue
        got = found[gi]
        if len(lines) > len(got):
            out.append({
                "id": f"dup:{d.isoformat()}:{amount:.2f}:{vendor[:40]}",
                "vendor": lines[0].vendor or lines[0].description,
                "date": d.isoformat(), "amount": amount, "currency": cur,
                "card_lines": len(lines), "receipts": len(got),
                "bill_line_ids": sorted(c.bill_line_id for c in lines),
                "charges": [{"bill_line_id": c.bill_line_id, "status": c.status,
                             "description": c.description} for c in
                            sorted(lines, key=lambda c: c.bill_line_id)],
                "receipt_list": [{"id": m.id, "message_id": m.message_id,
                                  "vendor": m.vendor, "amount": m.amount,
                                  "currency": m.currency, "date": m.receipt_date,
                                  "coupled": bool(m.bezala_transaction_id)}
                                 for m in got],
            })
    return out
