"""C50 — vilka Bezala-transaktioner har boten skrivit till? (endast läsning)

Källor:
* `bezala_write_log` (C50 och framåt — exakt vad som skrevs, status före/efter).
* `processed_messages` med `bezala_transaction_id` (kopplingar/uppladdningar
  före C50). Vad som skrevs rekonstrueras: POST /attachments (kvitto) och —
  om leverantören hade ett konto — PUT /transactions med description, konto,
  moms, date, bill_id/bill_line_id/connected_to_bill_line och
  state="unapproved" (det gamla flödet).
* `card_lines.bezala_transaction_id` (C43 privata utkast).

Status i Bezala läses live med GET /transactions/{id}. Inget skrivs, inget
dras tillbaka — Mikko avgör själv.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import BezalaWriteLog, CardLine, ProcessedMessage
from app.services import bezala_write_guard as guard

logger = logging.getLogger(__name__)

# C53 Del 0 — när C51-skyddet (endast state "draft", `state` skickas aldrig)
# gick live. Merge av C51 (#102, a08ebef) 2026-11-22 12:04:19 Helsingfors =
# 10:04:19 UTC; Railway deployar efter merge, så merge-tiden är den tidigaste
# möjliga tidpunkten (konservativt: allt efter den räknas som "efter").
# written_at i rapporten är UTC (datetime.utcnow).
C51_GUARD_LIVE_AT_UTC_DEFAULT = "2026-11-22T10:04:19"


def guard_live_at() -> datetime:
    import os

    raw = (os.environ.get("C51_GUARD_LIVE_AT_UTC") or "").strip()
    try:
        return datetime.fromisoformat((raw or C51_GUARD_LIVE_AT_UTC_DEFAULT).replace("Z", ""))
    except ValueError:
        return datetime.fromisoformat(C51_GUARD_LIVE_AT_UTC_DEFAULT)


def written_after_guard(written_at: str | None) -> bool | None:
    if not written_at:
        return None
    try:
        when = datetime.fromisoformat(str(written_at).replace("Z", ""))
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    return when >= guard_live_at()


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def _old_flow_put(row: ProcessedMessage, meta: dict | None, mappings: list) -> dict | None:
    """Rekonstruera om det gamla flödet gjorde en PUT (krävde konto)."""
    if meta is None:
        return None
    from app.services.bezala_field_mapper import build_receipt_params

    try:
        params = build_receipt_params(
            file_name=row.file_name, sender=row.sender, vendor=row.vendor,
            category=row.category,
            amount=row.bezala_payment_amount if row.bezala_payment_amount is not None else row.amount,
            currency=row.bezala_payment_currency or row.currency or "EUR",
            receipt_date=row.bezala_payment_date or row.receipt_date,
            subject=row.subject, accounts=meta.get("accounts") or [],
            cost_centers=meta.get("cost_centers") or [],
            vat_rates=meta.get("vat_rates") or [],
            description_override=row.ai_description_en or row.summary,
            vendor_mappings=mappings,
        )
    except Exception:  # noqa: BLE001
        return None
    if not params.get("date") or params.get("credit_account_id") is None:
        return {"put": False}
    return {
        "put": True,
        "description": params.get("description"),
        "credit_account_id": params.get("credit_account_id"),
        "date": params.get("date"),
        "state": "unapproved",
    }


def _live(client, tx_id: str) -> dict:
    if client is None:
        return {"status": "not read"}
    try:
        tx = client.get_transaction(tx_id)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)[:200]}
    snap = guard.status_snapshot(tx)
    inner = guard._unwrap(tx)
    return {
        "status": snap.get("state") or snap.get("status") or "unknown",
        "status_fields": snap,
        "left_draft": guard.is_definitely_not_draft(snap),
        "description_in_bezala": inner.get("description"),
    }


def list_bot_bezala_writes(db: Session, days: int = 14, *, client=None,
                           read_live: bool = True) -> dict:
    days = max(1, min(int(days or 14), 365))
    cutoff = datetime.utcnow() - timedelta(days=days)
    own_client = False
    if client is None and read_live:
        try:
            from app.services.bezala_client import BezalaClient

            client = BezalaClient()
            own_client = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("list_bot_bezala_writes: Bezala ej tillgänglig: %s", exc)
            client = None

    meta = None
    mappings: list = []
    if client is not None:
        try:
            from app.services.bezala_config import list_mappings
            from app.services.pipeline import fetch_bezala_metadata

            meta = fetch_bezala_metadata(client)
            mappings = list_mappings(db)
        except Exception:  # noqa: BLE001
            meta = None

    items: dict[str, dict] = {}
    try:
        rows = (
            db.query(ProcessedMessage)
            .filter(ProcessedMessage.bezala_transaction_id.isnot(None))
            .filter(or_(ProcessedMessage.matched_at >= cutoff,
                        ProcessedMessage.processed_at >= cutoff,
                        ProcessedMessage.manual_uploaded_at >= cutoff))
            .all()
        )
        for r in rows:
            when = r.matched_at or r.manual_uploaded_at or r.processed_at
            written = ["POST /attachments (receipt PDF, draft=1, state=unapproved"
                       + (", bill_line" if r.matched_at else "") + ")"]
            put = _old_flow_put(r, meta, mappings)
            if put and put.get("put"):
                written.append(
                    "PUT /transactions: description={!r}, credit_account_id={}, date={}, "
                    "state='unapproved', bill_id/bill_line_id/connected_to_bill_line".format(
                        put["description"], put["credit_account_id"], put["date"]))
            elif put is None:
                written.append("PUT /transactions (description/account/state) if the "
                               "vendor had an account - could not be reconstructed")
            items[str(r.bezala_transaction_id)] = {
                "transaction_id": str(r.bezala_transaction_id),
                "vendor": r.vendor or r.bezala_payment_merchant,
                "amount": r.bezala_payment_amount if r.bezala_payment_amount is not None else r.amount,
                "currency": r.bezala_payment_currency or r.currency,
                "written_at": _iso(when),
                "source": "coupling" if r.matched_at else (r.upload_source or "upload"),
                "message_id": r.message_id,
                "what_was_written": written,
            }
        for c in (db.query(CardLine)
                  .filter(CardLine.bezala_transaction_id.isnot(None))
                  .filter(CardLine.updated_at >= cutoff).all()):
            items.setdefault(str(c.bezala_transaction_id), {
                "transaction_id": str(c.bezala_transaction_id),
                "vendor": c.vendor or c.description,
                "amount": c.amount, "currency": c.currency,
                "written_at": _iso(c.updated_at),
                "source": "private_expense_draft",
                "what_was_written": ["POST /attachments (private expense note PDF, draft=1)"],
            })
        for log in (db.query(BezalaWriteLog)
                    .filter(BezalaWriteLog.created_at >= cutoff)
                    .order_by(BezalaWriteLog.id).all()):
            key = str(log.transaction_id or f"log-{log.id}")
            it = items.setdefault(key, {
                "transaction_id": log.transaction_id, "vendor": None, "amount": None,
                "currency": None, "written_at": _iso(log.created_at), "source": "write_log",
                "what_was_written": [],
            })
            it.setdefault("write_log", []).append({
                "at": _iso(log.created_at), "operation": log.operation,
                "fields": sorted((log.fields or {}).keys()), "outcome": log.outcome,
                "state_before": log.state_before, "state_after": log.state_after,
                "detail": log.detail,
            })

        out = sorted(items.values(), key=lambda i: i.get("written_at") or "", reverse=True)
        for it in out:
            tx = it.get("transaction_id")
            it["written_after_guard"] = written_after_guard(it.get("written_at"))
            it["bezala_now"] = _live(client, tx) if (tx and read_live) else {"status": "not read"}
    finally:
        if own_client and client is not None:
            client.close()
    return {
        "days": days,
        "count": len(out),
        "left_draft": [i["transaction_id"] for i in out if i["bezala_now"].get("left_draft")],
        "kill_switch": guard.kill_switch_status(db),
        "guard_live_at_utc": guard_live_at().isoformat(),
        "written_after_guard": [i["transaction_id"] for i in out if i.get("written_after_guard")],
        "note": "Read-only. Bezala Bot never withdraws anything - Mikko decides.",
        "items": out,
    }
