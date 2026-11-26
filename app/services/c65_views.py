"""C65 — data för MCP Apps-vyerna (matchningskort). Bara läsning.

`match_details` jämför en kortrad med ett kvitto fält för fält och ger
poängen med uppdelning (belopp, datum, leverantör, plats). Inget skrivs —
varken i Bezala Bot, Bezala eller Netvisor.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CardLine, ProcessedMessage
from app.services.calendar_trip_edit import TripEditError


def _max_points() -> dict:
    from app.services import receipt_matcher as rm

    return {"amount": rm.AMOUNT_BONUS, "date": rm.DATE_BUCKETS[0][1],
            "vendor": rm.VENDOR_BONUS_MAX, "location": rm.LOCATION_BONUS}


def _find_receipt(db: Session, message_id: str) -> ProcessedMessage | None:
    q = db.query(ProcessedMessage).filter(ProcessedMessage.deleted_at.is_(None))
    row = q.filter(ProcessedMessage.message_id == str(message_id)).first()
    if row is None and str(message_id).isdigit():
        row = q.filter(ProcessedMessage.id == int(message_id)).first()
    return row


def _place_text(trip: dict | None) -> str | None:
    if not trip:
        return None
    parts = list(trip.get("cities") or []) + list(trip.get("countries") or [])
    return ", ".join(dict.fromkeys(p for p in parts if p)) or None


def _receipt_place(m: ProcessedMessage) -> str | None:
    from app.services.receipt_matcher import CURRENCY_COUNTRY

    return CURRENCY_COUNTRY.get((m.currency or "").upper().strip())


def _other_uses(db: Session, m: ProcessedMessage, line: CardLine) -> list[dict]:
    """Används kvittot redan på en annan rad? Dubblett? Delat med återbetalning?"""
    from app.services.c53_receipts import _coupled_to_refund

    notes: list[dict] = []
    if m.duplicate_of:
        notes.append({"kind": "duplicate",
                      "text": f"Kvittot är en dubblett av ett annat kvitto ({m.duplicate_of})."})
    tx = m.bezala_transaction_id
    if tx and str(tx) != str(line.bill_line_id) and str(tx) != str(line.bezala_transaction_id or ""):
        if _coupled_to_refund(m):
            notes.append({"kind": "shared_refund",
                          "text": ("Kvittot används redan för återbetalningen "
                                   f"{m.bezala_payment_date or ''} (utlägg {tx}). Samma kvitto "
                                   "kan delas av köp och återbetalning - ladda då upp PDF:en "
                                   "själv från Drive.").replace("  ", " ")})
        else:
            notes.append({"kind": "used_elsewhere",
                          "text": f"Kvittot används redan på en annan rad i Bezala (utlägg {tx})."})
    return notes


def match_details(db: Session, bill_line_id: str, message_id: str | None = None) -> dict:
    from app.services.card_line_coupling import (
        _missing_from_card_line,
        _serialize,
        get_card_line,
        suggest_receipts,
    )
    from app.services.currency_converter import make_db_rate_provider
    from app.services.receipt_matcher import score_match

    bid = str(bill_line_id).strip()
    line = db.query(CardLine).filter(CardLine.bill_line_id == bid).first()
    if line is None:
        raise TripEditError(f"Kortraden '{bid}' finns inte i Bezala Bot.")
    source = "given"
    suggestion = None
    if not message_id:
        info = get_card_line(db, bid, scan_bezala=False)
        if info.get("coupled_receipts"):
            message_id = info["coupled_receipts"][0].get("message_id")
            source = "coupled"
        else:
            suggestion = suggest_receipts(db, [line]).get(bid)
            message_id = (suggestion or {}).get("message_id")
            source = "suggested"
    card = {
        "bill_line_id": bid,
        "vendor": line.vendor or line.description,
        "description": line.description,
        "amount": line.amount,
        "currency": line.currency,
        "date": line.txn_date.isoformat() if line.txn_date else None,
        "status": line.status,
        "bezala_transaction_id": line.bezala_transaction_id,
    }
    missing = _missing_from_card_line(db, line)
    from app.services.receipt_matcher import card_country

    card["place"] = _place_text(missing.get("trip")) or card_country(line.description)
    out: dict = {"bill_line_id": bid, "card_line": card, "receipt": None,
                 "source": source, "max_points": _max_points(),
                 "read_only": True,
                 "note": "Bara läsning. Inget skrivs till Bezala."}
    if not message_id:
        out["warnings"] = [{"kind": "no_receipt", "text": "Inget kvitto hittat för kortraden."}]
        return out
    m = _find_receipt(db, message_id)
    if m is None:
        raise TripEditError(f"Kvittot '{message_id}' finns inte i Bezala Bot.")
    s = score_match(missing, _serialize(m), rate_provider=make_db_rate_provider(db))
    b = s.get("breakdown") or {}
    out["receipt"] = {
        "message_id": m.message_id, "id": m.id, "vendor": m.vendor,
        "amount": m.amount, "currency": m.currency, "date": m.receipt_date,
        "place": _receipt_place(m), "kind": m.kind, "file_name": m.file_name,
        "drive_link": m.drive_link, "subject": m.subject,
        "bezala_transaction_id": m.bezala_transaction_id,
        "duplicate_of": m.duplicate_of,
        "draft_fill_status": m.draft_fill_status,
        "draft_fill_note": m.draft_fill_note,
    }
    mx = _max_points()
    out["score"] = {
        "total": s.get("total"),
        "parts": {
            "amount": b.get("amount", 0),
            "date": b.get("date", 0),
            "vendor": b.get("vendor", 0),
            "location": b.get("location", 0),
        },
        "date_days_off": b.get("date_days_off"),
        "location_reason": b.get("location_reason"),
        "rejected_reason": s.get("rejected_reason"),
        "conversion": s.get("conversion"),
    }
    out["fields_ok"] = {
        "amount": (b.get("amount") or 0) >= mx["amount"] * 0.5,
        "date": (b.get("date") or 0) >= 25,
        "vendor": (b.get("vendor") or 0) >= mx["vendor"] * 0.5,
        # Plats: None = okänt (kvittot eller kortraden säger inget om platsen).
        "location": True if b.get("location") else (
            False if card["place"] and out["receipt"]["place"] else None),
    }
    warnings = _other_uses(db, m, line)
    if suggestion and suggestion.get("match") == "refund_shared_receipt" and \
            not any(w["kind"] == "shared_refund" for w in warnings):
        warnings.append({"kind": "shared_refund", "text": suggestion.get("note_sv") or
                         "Samma kvitto används även för återbetalningen."})
    if m.amount is None:
        warnings.append({"kind": "amount_missing", "text": "Kvittot saknar belopp - öppna kvittot."})
    out["warnings"] = warnings
    return out
