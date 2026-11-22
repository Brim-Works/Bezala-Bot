"""C53 Del 0b — rätt kvitto föreslås (e-ticket före bokningsbekräftelse).

Fall i prod (Finnair 8NLAI4, 134,48 EUR): e-ticket-kvittot 749 är kopplat
till återbetalningsraden (-134,48, Bezala 5575527). För köpraden 2513379
föreslogs då bara dubbletten 760 (bokningsbekräftelsen). Regler:

1. Ett kvitto med `duplicate_of` föreslås aldrig — bara originalet.
2. Samma `booking_ref`: e-ticket-kvittot går före bokningsbekräftelsen
   (alla flygbolag).
3. Är originalet redan kopplat: visa "redan kopplat till <tx>" i stället för
   att falla tillbaka på dubbletten.
4. Ett kvitto kopplat till en återbetalningsrad får föreslås för köpraden
   med samma belopp och samma `booking_ref` — bara som förslag. Bezala Bot
   laddar inte upp det en gång till (raden kan bara bära en Bezala-koppling):
   Mikko laddar upp PDF:en själv från Drive-länken.

Endast läsning. Inget skrivs till Bezala.
"""

from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy.orm import Session

from app.models import CardLine, ProcessedMessage

_ETICKET_SUBJECT = ("eticket", "e-ticket", "e ticket", "electronic ticket", "e-lippu",
                    "sähköinen lippu", "elektronisk biljett", "ticket receipt")
_ETICKET_SENDER = ("eticket@", "e-ticket@", "ticket@amadeus", "@amadeus.com")
_CONFIRMATION = ("bokningsbekräftelse", "booking confirmation", "varausvahvistus",
                 "bestillingsbekreftelse", "bookingbekræftelse", "reservation confirmation",
                 "your booking", "din bokning")

SHARED_REFUND_NOTE = "same receipt is also used for the refund {date}"
SHARED_REFUND_NOTE_SV = "samma kvitto används även för återbetalningen {date}"
ALREADY_COUPLED_SV = "redan kopplat till {tx}"
ALREADY_COUPLED_EN = "already coupled to {tx}"


def _g(m, key):
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def is_eticket(m) -> bool:
    subject = (_g(m, "subject") or "").lower()
    sender = (_g(m, "sender") or "").lower()
    if any(w in subject for w in _ETICKET_SUBJECT) or any(w in sender for w in _ETICKET_SENDER):
        return True
    return bool(_g(m, "ticket_number"))


def is_booking_confirmation(m) -> bool:
    if is_eticket(m):
        return False
    subject = (_g(m, "subject") or "").lower()
    return any(w in subject for w in _CONFIRMATION)


def booking_ref_rank(m) -> int:
    """Lägre = bättre bland kvitton med samma booking_ref."""
    if is_eticket(m):
        return 0
    if is_booking_confirmation(m):
        return 2
    return 1


def _eticket_refs(db: Session) -> dict[str, ProcessedMessage]:
    """booking_ref → e-ticket-kvittot (oavsett om det är kopplat)."""
    out: dict[str, ProcessedMessage] = {}
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.booking_ref.isnot(None))
            .filter(ProcessedMessage.duplicate_of.is_(None))
            .all())
    for r in rows:
        if r.booking_ref and is_eticket(r):
            out.setdefault(r.booking_ref.upper(), r)
    return out


def filter_candidates(db: Session, candidates: list[dict]) -> list[dict]:
    """Ta bort dubbletter och bokningsbekräftelser som har ett e-ticket-kvitto
    med samma booking_ref (kopplat eller inte)."""
    etickets = _eticket_refs(db)
    out = []
    for c in candidates:
        if c.get("duplicate_of"):
            continue
        ref = (c.get("booking_ref") or "").upper()
        best = etickets.get(ref) if ref else None
        if best is not None and best.message_id != c.get("message_id") and not is_eticket(c):
            continue
        out.append(c)
    # Samma booking_ref bland kandidaterna: bara den bäst rankade.
    by_ref: dict[str, list[dict]] = defaultdict(list)
    for c in out:
        if c.get("booking_ref"):
            by_ref[c["booking_ref"].upper()].append(c)
    drop = set()
    for group in by_ref.values():
        if len(group) > 1:
            group.sort(key=booking_ref_rank)
            drop.update(id(c) for c in group[1:] if booking_ref_rank(c) > booking_ref_rank(group[0]))
    return [c for c in out if id(c) not in drop]


def original_of(db: Session, m) -> ProcessedMessage | None:
    """Originalet för en dubblett/bokningsbekräftelse (duplicate_of eller
    e-ticket med samma booking_ref)."""
    dup = _g(m, "duplicate_of")
    if dup:
        return (db.query(ProcessedMessage)
                .filter(ProcessedMessage.message_id == dup).first())
    ref = (_g(m, "booking_ref") or "").upper()
    if ref and not is_eticket(m):
        e = _eticket_refs(db).get(ref)
        if e is not None and e.message_id != _g(m, "message_id"):
            return e
    return None


def already_coupled_note(db: Session, m) -> dict | None:
    """Om kvittot är en dubblett vars original redan är kopplat."""
    orig = original_of(db, m)
    if orig is None or not orig.bezala_transaction_id:
        return None
    tx = orig.bezala_transaction_id
    return {
        "message_id": orig.message_id,
        "bezala_transaction_id": tx,
        "text": ALREADY_COUPLED_SV.format(tx=tx),
        "text_en": ALREADY_COUPLED_EN.format(tx=tx),
        "instead_of": _g(m, "message_id"),
    }


_NEG_RE = re.compile(r"(?:^|\s)-\s?\d")


def _coupled_to_refund(m: ProcessedMessage) -> bool:
    if m.bezala_payment_amount is not None and m.bezala_payment_amount < 0:
        return True
    return bool(_NEG_RE.search(m.bezala_payment_merchant or ""))


def _same_amount(a, b) -> bool:
    return a is not None and b is not None and abs(abs(float(a)) - abs(float(b))) < 0.01


def shared_refund_receipt(db: Session, line: CardLine) -> dict | None:
    """Köprad (+belopp) utan kvitto ↔ e-ticket som redan är kopplat till en
    återbetalningsrad med samma belopp. Kräver booking_ref och att en
    bokningsbekräftelse/dubblett med samma booking_ref och belopp finns, eller
    att kvittot självt har booking_ref (flygkvitto)."""
    if line.amount is None or line.amount <= 0:
        return None
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.duplicate_of.is_(None))
            .filter(ProcessedMessage.booking_ref.isnot(None))
            .filter(ProcessedMessage.bezala_transaction_id.isnot(None))
            .all())
    for m in rows:
        if not _coupled_to_refund(m):
            continue
        if (m.currency or "EUR").upper() != (line.currency or "EUR").upper():
            continue
        if not _same_amount(m.amount, line.amount):
            continue
        # Samma booking_ref på köpraden? Kortraden har ingen booking_ref —
        # vi kräver att leverantören syns i kortradens text och att köpet
        # ligger före återbetalningen.
        vendor = (m.vendor or "").lower().split(" ")[0]
        if vendor and vendor not in (line.description or line.vendor or "").lower():
            continue
        refund_date = m.bezala_payment_date
        if line.txn_date and refund_date and line.txn_date.isoformat() > str(refund_date)[:10]:
            continue
        return {
            "message_id": m.message_id, "id": m.id, "vendor": m.vendor,
            "amount": m.amount, "currency": m.currency, "date": m.receipt_date,
            "kind": m.kind, "score": 80, "match": "refund_shared_receipt",
            "booking_ref": m.booking_ref,
            "note": SHARED_REFUND_NOTE.format(date=refund_date or "?"),
            "note_sv": SHARED_REFUND_NOTE_SV.format(date=refund_date or "?"),
            "also_coupled_to": m.bezala_transaction_id,
            "drive_link": m.drive_link,
            "manual_upload": True,
            "how": ("Bezala Bot does not upload it again (the receipt row holds one Bezala "
                    "coupling). Open the Drive link, download the PDF and add it to the "
                    "purchase line in Bezala yourself."),
        }
    return None


def confirmation_dry_run(db: Session) -> dict:
    """Del 0b punkt 4 — bokningsbekräftelser/dubbletter som FÖRE C53 kunde
    föreslås (okopplade, sparade). Endast läsning."""
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.status == "saved")
            .filter(ProcessedMessage.bezala_transaction_id.is_(None))
            .all())
    etickets = _eticket_refs(db)
    by_vendor: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.bezala_upload_status == "success":
            continue
        ref = (r.booking_ref or "").upper()
        loses = bool(ref) and ref in etickets and etickets[ref].message_id != r.message_id \
            and not is_eticket(r)
        if not (r.duplicate_of or loses):
            continue
        orig = original_of(db, r)
        by_vendor[r.vendor or "?"].append({
            "id": r.id, "message_id": r.message_id, "subject": r.subject,
            "amount": r.amount, "currency": r.currency, "receipt_date": r.receipt_date,
            "booking_ref": r.booking_ref, "duplicate_of": r.duplicate_of,
            "booking_confirmation": is_booking_confirmation(r),
            "original": orig.message_id if orig else None,
            "original_coupled_to": orig.bezala_transaction_id if orig else None,
        })
    vendors = {v: {"count": len(items), "receipts": items}
               for v, items in sorted(by_vendor.items())}
    return {
        "dry_run": True,
        "count": sum(v["count"] for v in vendors.values()),
        "by_vendor": vendors,
        "note": "Read-only. These were suggestable before C53; now only the original is.",
    }
