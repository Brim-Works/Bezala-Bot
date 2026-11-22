"""C52 — Bolt-testet, momsrapport och "redan hanterat i Bezala".

Del 1: ETT hårt låst test (Mikko godkände 2026-11-22 kl 12:30):
  PUT /transactions/5579365 {"transaction": {"description": "..."}}
  via skyddsfunktionen (läsning före/efter, kill switch). Inget `state`,
  inga andra fält, ingen annan transaktion. Körs högst en gång.

Del 3: läsrapport över momsläsningen (C51, 90 dagar) - vilka kvitton som
  fick moms, vilka som inte gjorde det och varför. Kan köra om läsningen för
  kvitton utan moms (ändrar bara botens egen `vat_info`).

Del 4: kvitton med status `saved`, utan `waiting_status` och utan
  `bezala_transaction_id` syns i ingen lista. Torrkörning: matcha mot
  Bezalas utlägg (datum ±1 dag, belopp även NOK/SEK mot EUR med tolerans,
  leverantör). `apply` sätter bara botens egen status
  `already_handled_in_bezala` för de kvitton Mikko sagt ja till. Inget
  skrivs till Bezala.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- Del 1

BOLT_TX_ID = "5579365"
BOLT_DESCRIPTION = "Bolt.new Pro subscription, 16 November 2026"
BOLT_TASK = "c52_bolt_description_put_v1"


def _bolt_fields(tx: dict) -> dict:
    t = tx.get("transaction") if isinstance(tx.get("transaction"), dict) else tx
    keys = ("id", "state", "description", "approvers", "current_approvers_ids",
            "transaction_approvers", "approved_by", "approved_by_ids", "updated_at")
    return {k: t.get(k) for k in keys if k in t}


def run_bolt_test(db: Session, client=None) -> dict:
    """Del 1 — exakt en PUT med bara description mot 5579365. Körs en gång."""
    from app.services import bezala_write_guard as guard

    if db.query(MaintenanceTask).filter(MaintenanceTask.name == BOLT_TASK).first():
        return {"ran": False, "reason": "already run once - not repeated"}
    db.add(MaintenanceTask(name=BOLT_TASK))
    db.commit()

    close = False
    if client is None:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
        close = True
    out: dict[str, Any] = {"ran": True, "transaction_id": BOLT_TX_ID,
                           "sent": {"transaction": {"description": BOLT_DESCRIPTION}}}
    try:
        out["before"] = _bolt_fields(client.get_transaction(BOLT_TX_ID))
        try:
            guard.guarded_put(client, BOLT_TX_ID, {"description": BOLT_DESCRIPTION}, db=db)
            out["put"] = "ok"
        except guard.BezalaWriteBlocked as exc:
            out["put"] = "blocked"
            out["error"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            out["put"] = "failed"
            out["error"] = str(exc)[:500]
        out["after"] = _bolt_fields(client.get_transaction(BOLT_TX_ID))
        out["kill_switch"] = {"writes_halted": guard.writes_halted(db)}
        out["still_draft"] = out["after"].get("state") == "draft" and not (
            out["after"].get("current_approvers_ids") or out["after"].get("approvers")
            or out["after"].get("transaction_approvers"))
        logger.info("C52 Bolt-test: %s", out)
        return out
    finally:
        if close:
            client.close()


# ---------------------------------------------------------------- Del 3


def vat_report(db: Session, message_ids: list[str] | None = None,
               now: datetime | None = None) -> dict:
    """Läsrapport över momsläsningen (C51-omläsningen, 90 dagar)."""
    from app.services import c51_migrations as c51

    now = now or datetime.utcnow()
    since = (now - timedelta(days=90)).date().isoformat()
    task = db.query(MaintenanceTask).filter(MaintenanceTask.name == c51.TASK).first()
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.receipt_date >= since).all())
    picked = {r.id for r in c51.candidates(db, now=now)}

    def state(r: ProcessedMessage) -> str:
        vi = r.vat_info
        if vi is None:
            return "not_read"
        if isinstance(vi, dict) and vi.get("unread"):
            return "read_no_vat_found"
        return "read"

    counts: dict[str, int] = {}
    not_read: list[dict] = []
    for r in rows:
        s = state(r)
        counts[s] = counts.get(s, 0) + 1
        if s == "not_read":
            not_read.append(_vat_row(r, picked))
    out = {
        "c51_backfill_ran_at": task.ran_at.isoformat() if task and task.ran_at else None,
        "max_rows_per_run": c51.MAX_ROWS,
        "receipts_last_90_days": len(rows),
        "counts": counts,
        "not_read": sorted(not_read, key=lambda x: x["date"] or "", reverse=True),
        "log": _vat_log(db),
    }
    if message_ids:
        want = {str(x) for x in message_ids}
        out["selected"] = [
            {**_vat_row(r, picked), "vat_state": state(r), "vat_info": r.vat_info}
            for r in db.query(ProcessedMessage)
            .filter(ProcessedMessage.message_id.in_(want)).all()
        ]
    return out


def _vat_row(r: ProcessedMessage, picked: set[int]) -> dict:
    reasons = []
    if not r.drive_file_id:
        reasons.append("no Drive file")
    if r.duplicate_of:
        reasons.append("marked as duplicate")
    if r.waiting_status in ("ignored_private_source", "private"):
        reasons.append(f"waiting_status {r.waiting_status}")
    if not reasons and r.id not in picked:
        reasons.append("not picked (over the per-run limit)")
    if not reasons:
        reasons.append("picked - read failed or not run yet")
    return {"message_id": r.message_id, "vendor": r.vendor, "amount": r.amount,
            "currency": r.currency, "date": r.receipt_date, "file_name": r.file_name,
            "why_not_read": reasons}


def _vat_log(db: Session) -> dict:
    from app.models import CleanupLog

    rows = db.query(CleanupLog).filter(CleanupLog.run == "c52_vat").all()
    by: dict[str, int] = {}
    errors = []
    for r in rows:
        by[r.rule] = by.get(r.rule, 0) + 1
        if r.rule == "vat_read_failed":
            errors.append({"message_id": r.message_id, "error": r.reason})
    return {"counts": by, "errors": errors[-20:]}


def reread_vat(db: Session, *, limit: int = 200) -> dict:
    """Läs om momsen för kvitton (90 dagar) som saknar den. Ändrar bara
    `vat_info` på botens egna rader och loggar varje utfall i cleanup_log."""
    from app.services import c51_migrations as c51
    from app.services.cleanup_log import log
    from app.services.drive_client import DriveClient
    from app.services.receipt_analyzer import ReceiptAnalyzer

    analyzer = ReceiptAnalyzer()
    if not analyzer.enabled:
        return {"error": "receipt analyzer is not enabled"}
    old = c51.MAX_ROWS
    try:
        c51.MAX_ROWS = limit
        rows = c51.candidates(db)
    finally:
        c51.MAX_ROWS = old
    drive = DriveClient()
    res = {"tried": len(rows), "read": 0, "no_vat_found": 0, "failed": 0}
    for r in rows:
        mid = r.message_id
        try:
            ok = c51.read_vat(db, r, drive, analyzer)
            res["read" if ok else "no_vat_found"] += 1
            log(db, "c52_vat", "vat_read" if ok else "vat_not_on_receipt", mid,
                after={"vat_info": r.vat_info}, commit=True)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            res["failed"] += 1
            log(db, "c52_vat", "vat_read_failed", mid, reason=f"{type(exc).__name__}: {exc}",
                commit=True)
    return res


# ---------------------------------------------------------------- Del 4

DATE_WINDOW_DAYS = 1
FX_TOLERANCE = 0.05  # bankens valutapåslag + kursdag


def blind_spot_receipts(db: Session) -> list[ProcessedMessage]:
    return (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.status == "saved")
            .filter(ProcessedMessage.waiting_status.is_(None))
            .filter(ProcessedMessage.bezala_transaction_id.is_(None))
            .filter(ProcessedMessage.duplicate_of.is_(None))
            .order_by(ProcessedMessage.receipt_date.desc()).all())


def _day(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _f(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def tx_amounts(tx: dict) -> list[tuple[float, str]]:
    """Alla belopp ett Bezala-utlägg har: debiterat (EUR) och ursprungligt."""
    out = []
    for amt, cur in (("billed_amount", "billed_amount_currency"),
                     ("original_amount", "original_amount_currency"),
                     ("amount", "currency"), ("total", "currency")):
        a = _f(tx.get(amt))
        if a is not None:
            out.append((abs(a), str(tx.get(cur) or "EUR").upper()))
    return out


def amount_hit(amount, currency, tx: dict, day: date, rate) -> str | None:
    if amount is None:
        return None
    a, cur = abs(float(amount)), (currency or "EUR").upper()
    for b, bcur in tx_amounts(tx):
        if cur == bcur and abs(a - b) <= 0.01:
            return f"{b:.2f} {bcur}"
    for b, bcur in tx_amounts(tx):
        if cur == bcur or rate is None:
            continue
        try:
            r = rate(day.isoformat(), cur, bcur)
        except Exception:  # noqa: BLE001
            r = None
        if r and abs(a * float(r) - b) <= max(0.05, FX_TOLERANCE * b):
            return f"{b:.2f} {bcur} (~{a * float(r):.2f} {bcur} converted)"
    return None


def vendor_hit(m: ProcessedMessage, tx: dict) -> bool:
    import re

    from app.services.c50_accounts import _norm, vendor_key

    keys = {k for k in (vendor_key(m.vendor), vendor_key(m.bezala_payment_merchant)) if k}
    hay = _norm(" ".join(str(tx.get(k) or "") for k in (
        "description", "vendor_name", "merchant", "bill_line_description")))
    v = tx.get("vendor")
    hay += " " + _norm(v.get("name") if isinstance(v, dict) else str(v or ""))
    if any(re.search(r"\b" + re.escape(k) + r"\b", hay) for k in keys):
        return True
    # ChatGPT köps via Apple: kvittots leverantör kan vara "Apple".
    blob = _norm(" ".join(x or "" for x in (m.vendor, m.subject, m.ai_description_en,
                                            m.file_name)))
    if ("chatgpt" in blob or "openai" in blob) and ("chatgpt" in hay or "openai" in hay):
        return True
    return False


def find_matches(db: Session, transactions: list[dict] | None = None, rate=None) -> dict:
    """Torrkörning — listar bara, ändrar inget."""
    if transactions is None:
        from app.services.bezala_client import BezalaClient

        c = BezalaClient()
        try:
            transactions = c.list_transactions() or []
        finally:
            c.close()
    if rate is None:
        from app.services.currency_converter import make_db_rate_provider

        rate = make_db_rate_provider(db)
    by_day: dict[date, list[dict]] = {}
    for tx in transactions:
        d = _day(tx.get("date"))
        if d is not None:
            by_day.setdefault(d, []).append(tx)

    matches, amount_only, no_match = [], [], []
    for m in blind_spot_receipts(db):
        d = _day(m.receipt_date) or _day(m.received_at)
        brief = {"message_id": m.message_id, "vendor": m.vendor, "amount": m.amount,
                 "currency": m.currency, "date": m.receipt_date, "file_name": m.file_name,
                 "drive_link": m.drive_link}
        if d is None or m.amount is None:
            no_match.append({**brief, "why": "no date or amount"})
            continue
        found, weak = None, None
        for off in (0, -1, 1):
            for tx in by_day.get(d + timedelta(days=off), []):
                hit = amount_hit(m.amount, m.currency, tx, d, rate)
                if not hit:
                    continue
                info = {"bezala_transaction_id": tx.get("id"), "date": tx.get("date"),
                        "description": tx.get("description"), "state": tx.get("state"),
                        "amount": hit}
                if vendor_hit(m, tx):
                    found = info
                    break
                weak = weak or info
            if found:
                break
        if found:
            matches.append({**brief, "bezala": found})
        elif weak:
            amount_only.append({**brief, "bezala": weak,
                                "note": "same amount and date, vendor not in the description"})
        else:
            no_match.append({**brief, "why": "no Bezala expense with this date and amount"})
    return {"dry_run": True, "blind_spot_receipts": len(matches) + len(amount_only)
            + len(no_match), "bezala_transactions_read": len(transactions),
            "matches": matches, "amount_and_date_only": amount_only, "no_match": no_match}


def apply_already_handled(db: Session, message_ids: list[str],
                          transactions: list[dict] | None = None, rate=None) -> dict:
    """Sätt `already_handled_in_bezala` BARA för de kvitton Mikko sagt ja till
    och som fortfarande finns i torrkörningens träfflista. Botens egen status -
    inget skrivs till Bezala."""
    from app.services.c50_handled import ALREADY_HANDLED
    from app.services.cleanup_log import log

    want = {str(x) for x in message_ids or []}
    if not want:
        return {"applied": [], "skipped": [], "note": "no message_ids given - nothing changed"}
    res = find_matches(db, transactions=transactions, rate=rate)
    allowed = {x["message_id"]: x for x in res["matches"] + res["amount_and_date_only"]}
    applied, skipped = [], []
    for mid in sorted(want):
        hit = allowed.get(mid)
        m = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == mid).first()
        if hit is None or m is None or m.waiting_status is not None \
                or m.bezala_transaction_id is not None:
            skipped.append({"message_id": mid, "why": "not in the current match list"})
            continue
        m.waiting_status = ALREADY_HANDLED
        log(db, "c52", ALREADY_HANDLED, mid, before={"waiting_status": None},
            after={"waiting_status": ALREADY_HANDLED, "match": hit["bezala"]},
            reason="Mikko confirmed: already entered by hand in Bezala")
        applied.append({"message_id": mid, "vendor": m.vendor, "bezala": hit["bezala"]})
    db.commit()
    return {"applied": applied, "skipped": skipped,
            "note": "Only Bezala Bot's own status changed. Nothing was written to Bezala."}
