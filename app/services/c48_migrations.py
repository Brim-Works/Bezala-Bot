"""C48 — engångsjobb vid start (idempotenta) + läsverktyg.

* Del 6: cleanup_log fylls bakåt (vad C47-städningen gjort / skulle göra).
* Del 2: kvitton 2026 utan belopp listas och läses om (utan auto-upload).
* Del 3/3b: prenumerationsförslag städas (ihopslagning, tyst avfärdande
  utan kortrader, troligen privata, belopp), privata Gmail-kvitton utan
  kortrad efter 14 dagar → ignored_private_source.
* Del 4: syftet på suggested-resor normaliseras; edited/approved med
  upprepat eller icke-engelskt syfte rapporteras (ändras inte).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.models import CalendarTrip, CalendarTripDay, CardLine, MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

PURPOSE_TASK = "c48_purpose_v1"
RUN = "c48_startup"


def _once(db: Session, name: str) -> bool:
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == name).first():
        return False
    db.add(MaintenanceTask(name=name))
    db.commit()
    return True


def _trip_cities(db: Session, trip: CalendarTrip) -> list[str]:
    cities = [c.strip() for c in (trip.destination or "").split("→") if c.strip()]
    for d in db.query(CalendarTripDay).filter(CalendarTripDay.trip_id == trip.id).all():
        if d.destination:
            cities += [c.strip() for c in d.destination.split("→") if c.strip()]
    return cities


def fix_purposes(db: Session, *, force: bool = False) -> dict | None:
    """suggested: normalisera syftet. edited/approved: bara rapport."""
    from app.services.cleanup_log import log
    from app.services.trip_english import non_english_warnings, normalize_purpose

    if not force and not _once(db, PURPOSE_TASK):
        return None
    fixed, report = [], []
    for t in db.query(CalendarTrip).order_by(CalendarTrip.id).all():
        if not t.purpose:
            continue
        cities = _trip_cities(db, t)
        new = normalize_purpose(t.purpose, cities)
        repeated = new != t.purpose.strip()
        if t.status == "suggested":
            if repeated:
                log(db, RUN, "purpose_fixed", None, before={"trip_id": t.id, "purpose": t.purpose},
                    after={"purpose": new}, reason="repeated entry removed")
                fixed.append({"trip_id": t.id, "before": t.purpose, "after": new})
                t.purpose = new
            continue
        if t.status in ("edited", "approved"):
            warn = non_english_warnings("purpose", t.purpose)
            if repeated or warn:
                why = []
                if repeated:
                    why.append(f"repeated (suggested: '{new}')")
                why += warn
                log(db, RUN, "purpose_report", None,
                    before={"trip_id": t.id, "status": t.status, "purpose": t.purpose,
                            "start_date": t.start_date},
                    reason="; ".join(why))
                report.append({"trip_id": t.id, "status": t.status, "purpose": t.purpose,
                               "reason": "; ".join(why)})
    db.commit()
    logger.info("[c48-purpose] rättade %s, rapport %s", fixed, report)
    return {"fixed": fixed, "report": report}


def run_all(db: Session, *, background: bool = True) -> dict:
    from app.services.amount_missing import report_and_reread
    from app.services.c47_cleanup import backfill_cleanup_log
    from app.services.subscriptions import apply_private, tidy_suggestions
    from app.services.waiting_receipts import ignore_private_source

    out: dict = {}

    def _ignore():
        n = ignore_private_source(db)
        db.commit()
        return n

    for name, fn in (
        ("cleanup_backfill", lambda: backfill_cleanup_log(db)),
        ("purposes", lambda: fix_purposes(db)),
        ("subscriptions", lambda: tidy_suggestions(db, RUN)),
        ("private_subscriptions", lambda: apply_private(db)),
        ("ignored_private_source", _ignore),
        ("amount_missing", lambda: report_and_reread(db, background=background)),
    ):
        try:
            out[name] = fn()
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("C48: %s misslyckades", name)
    return out


# ---------- läsverktyg (MCP investigate_vendor) ----------


def investigate_vendor(db: Session, query: str, limit: int = 50) -> dict:
    """Read-only: varifrån kommer en leverantörs rader? Kortrader på
    företagskortet (alla statusar) + kvitton med källa (jobb/privat Gmail,
    mobil, +kvitto, manuell), avsändare och väntestatus."""
    words = [w for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower())]
    if not words:
        return {"error": "Give a vendor name, e.g. 'skyshowtime'."}

    def hit(*texts) -> bool:
        hay = " ".join((t or "").lower() for t in texts)
        return any(w in hay for w in words)

    lines = [c for c in db.query(CardLine).order_by(CardLine.txn_date).all()
             if hit(c.description, c.vendor)]
    receipts = [m for m in db.query(ProcessedMessage).order_by(ProcessedMessage.id).all()
                if hit(m.vendor, m.file_name, m.subject, m.bezala_payment_merchant)]
    from collections import Counter

    snap = [m for m in receipts if m.bezala_payment_amount is not None]
    return {
        "query": query,
        "company_card_lines": [{
            "bill_line_id": c.bill_line_id, "date": c.txn_date.isoformat() if c.txn_date else None,
            "description": c.description, "amount": c.amount, "currency": c.currency,
            "status": c.status, "note": c.note} for c in lines][:limit],
        "coupled_card_charges": [{
            "message_id": m.message_id, "merchant": m.bezala_payment_merchant,
            "amount": m.bezala_payment_amount, "date": m.bezala_payment_date} for m in snap][:limit],
        "receipts": [{
            "id": m.id, "message_id": m.message_id, "vendor": m.vendor, "amount": m.amount,
            "currency": m.currency, "date": m.receipt_date, "sender": m.sender,
            "source": m.upload_source, "account": m.source_account or "visma",
            "source_note": m.source_note, "status": m.status,
            "waiting_status": m.waiting_status, "duplicate_of": m.duplicate_of,
            "deleted": m.deleted_at is not None,
            "bezala_transaction_id": m.bezala_transaction_id} for m in receipts][:limit],
        "summary": {
            "card_lines": len(lines),
            "coupled_card_charges": len(snap),
            "receipts": len(receipts),
            "receipts_by_account": dict(Counter((m.source_account or "visma") for m in receipts)),
            "receipts_by_sender": dict(Counter(m.sender or "?" for m in receipts).most_common(5)),
        },
    }
