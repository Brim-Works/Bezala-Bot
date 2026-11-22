"""C48 Del 6 — datastädningens logg (tabell cleanup_log).

Railway-loggarna går inte alltid att läsa härifrån, så allt städningen
gör (eller skulle göra i torrläge) sparas i databasen och läses via
MCP get_cleanup_report(rule?, limit).
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy.orm import Session

from app.models import CleanupLog

logger = logging.getLogger(__name__)

RULE_INFO = {
    "empty_error": "Error rows without sender/subject/file (mail could not be fetched)",
    "pre_2026": "Receipts dated before 2026 moved to the trash",
    "date_corrected": "Receipt dates corrected (AI year outside a sane window) and flagged",
    "finnair_duplicate": "Finnair booking confirmations marked as duplicates of the e-ticket",
    "hotel_duplicate": "Hotel invoice/payment confirmation pairs marked as duplicates",
    "duplicate": "Other duplicates (same ticket/booking number)",
    "amount_missing": "Processed 2026 receipts without amount (re-read when possible)",
    "ignored_private_source": "Private Gmail receipts without a company card charge after 14 days",
    "purpose_fixed": "Suggested trips whose purpose was de-duplicated",
    "purpose_report": "Edited/approved trips with a repeated or non-English purpose (not changed)",
    "subscription_dismissed": "Subscription suggestions dismissed silently (no company card lines)",
    "subscription_merged": "Subscription suggestions merged (same charges)",
}


def _jsonable(value):
    import json

    if value is None:
        return None
    try:
        return json.loads(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001
        return {"repr": repr(value)[:500]}


def log(db: Session, run: str, rule: str, message_id: str | None = None, *,
        before=None, after=None, reason: str | None = None,
        dry_run: bool = False, commit: bool = False) -> CleanupLog:
    row = CleanupLog(run=run[:64], rule=rule[:40],
                     message_id=(str(message_id)[:255] if message_id else None),
                     before=_jsonable(before), after=_jsonable(after),
                     reason=(reason or None) and str(reason)[:2000], dry_run=dry_run)
    db.add(row)
    if commit:
        db.commit()
    return row


def already_logged(db: Session, run: str) -> bool:
    return db.query(CleanupLog.id).filter(CleanupLog.run == run).first() is not None


def _row(r: CleanupLog) -> dict:
    return {
        "id": r.id, "run": r.run, "rule": r.rule, "message_id": r.message_id,
        "before": r.before, "after": r.after, "reason": r.reason,
        "dry_run": r.dry_run, "at": r.created_at.isoformat() if r.created_at else None,
    }


def report(db: Session, rule: str | None = None, limit: int = 50) -> dict:
    limit = max(1, min(int(limit or 50), 500))
    all_rows = db.query(CleanupLog).all()
    counts = Counter(r.rule for r in all_rows)
    q = db.query(CleanupLog)
    if rule:
        q = q.filter(CleanupLog.rule == rule)
    rows = q.order_by(CleanupLog.id.desc()).limit(limit).all()
    out: dict = {
        "counts": dict(sorted(counts.items())),
        "rules": {k: RULE_INFO.get(k, "") for k in sorted(counts)},
        "rows": [_row(r) for r in rows],
    }
    if rule == "empty_error" or rule is None:
        causes = Counter((r.reason or "(no error message)")[:120]
                         for r in all_rows if r.rule == "empty_error")
        out["empty_error_causes"] = causes.most_common(15)
    return out
