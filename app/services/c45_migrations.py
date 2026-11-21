"""C45 — engångsjobb vid startup (idempotenta via MaintenanceTask).

1. `support@airportrecovery.com` som html-only-avsändare (kvittot kommer
   som text i ett svar i tråden).
2. Engångsskanning av 2026 i Visma-Gmail: bedöm VARJE meddelande i trådar
   där boten tidigare sett ett meddelande, plus allt från avsändare med
   trådsvar-regel. Varje nytt kvitto loggas med prefixet `[c45-thread-scan]`.
   Körs i en bakgrundstråd så appen startar direkt.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import HtmlOnlySender, MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

SEED_SENDERS_TASK = "seed_html_only_senders_c45"
THREAD_SCAN_TASK = "c45_thread_reply_scan_2026_v1"
SCAN_AFTER = "2026/01/01"


def _task_done(db: Session, name: str) -> bool:
    return (
        db.query(MaintenanceTask).filter(MaintenanceTask.name == name).first()
        is not None
    )


def seed_c45_html_only_senders(db: Session) -> int:
    from app.services.reply_receipts import REPLY_RECEIPT_SENDERS

    if _task_done(db, SEED_SENDERS_TASK):
        return 0
    existing = {
        (s.sender_pattern or "").lower() for s in db.query(HtmlOnlySender).all()
    }
    added = 0
    for pattern in REPLY_RECEIPT_SENDERS:
        if pattern in existing:
            continue
        db.add(HtmlOnlySender(
            sender_pattern=pattern,
            description="Airport Recovery Services — kvitto som svar i tråd",
            is_active=True,
        ))
        added += 1
    db.add(MaintenanceTask(name=SEED_SENDERS_TASK))
    db.commit()
    logger.info("C45: html_only_senders — %d nya rader", added)
    return added


def thread_scan_2026() -> dict:
    """Bedöm alla meddelanden i 2026-trådar som saknar ProcessedMessage-rad
    och kommer från samma avsändare som trådens kända leverantörsmail."""
    from app.db import session_scope
    from app.services import pipeline as pl
    from app.services.private_gmail import sender_address
    from app.services.reply_receipts import REPLY_RECEIPT_SENDERS

    with session_scope() as db:
        if _task_done(db, THREAD_SCAN_TASK):
            return {"skipped": "already_done"}
        rows = (
            db.query(ProcessedMessage.thread_id, ProcessedMessage.sender)
            .filter(ProcessedMessage.thread_id.isnot(None))
            .filter(ProcessedMessage.received_at >= datetime(2026, 1, 1))
            .all()
        )
        threads: dict[str, str] = {}
        for tid, sender in rows:
            if tid and sender and (sender_address(sender) or "").find("@visma.com") < 0:
                threads.setdefault(tid, sender_address(sender))

    gmail = pl.GmailClient()
    # Trådar från avsändare med trådsvar-regel (även om boten aldrig sparat
    # något därifrån — Airport Recovery-fallet).
    q = " OR ".join(f"from:{s}" for s in REPLY_RECEIPT_SENDERS)
    for mid, tid in gmail.list_candidates_with_threads(
        f"({q}) after:{SCAN_AFTER} -in:spam -in:trash", max_results=200,
    ):
        if tid:
            meta = gmail.fetch_message_metadata(mid)
            threads.setdefault(tid, sender_address(meta.get("sender")))

    deps = _scan_deps()
    result = pl.ScanResult()
    found: list[dict] = []
    for tid, sender in threads.items():
        if not sender:
            continue
        try:
            msgs = gmail.list_thread_reply_ids(tid)
        except Exception:  # noqa: BLE001
            logger.exception("[c45-thread-scan] kunde inte läsa tråd %s", tid)
            continue
        if len(msgs) < 2:
            continue
        ids = [m["message_id"] for m in msgs if m.get("message_id")]
        with session_scope() as db:
            known = {
                r.message_id for r in db.query(ProcessedMessage.message_id)
                .filter(ProcessedMessage.message_id.in_(ids)).all()
            }
        # Bezala-Klar ignoreras medvetet: AI:n kan tidigare ha avfärdat
        # svaret (inget DB-spår), och det är just de vi letar efter.
        for mid in pl.thread_sibling_candidates(
            msgs, sender=sender, done_label_id=None,
            known_ids=known | result.seen_ids,
        ):
            before = result.processed
            try:
                pl._process_one_message(mid, gmail, *deps["clients"], result, **deps["kwargs"])
            except Exception as exc:  # noqa: BLE001
                logger.exception("[c45-thread-scan] fel för %s", mid)
                result.errors += 1
                continue
            if result.processed > before:
                with session_scope() as db:
                    row = (
                        db.query(ProcessedMessage)
                        .filter(ProcessedMessage.message_id == mid).first()
                    )
                    info = {
                        "message_id": mid, "thread_id": tid,
                        "sender": row.sender if row else sender,
                        "subject": row.subject if row else None,
                        "received_at": (
                            row.received_at.isoformat()
                            if row and row.received_at else None
                        ),
                        "amount": row.amount if row else None,
                        "currency": row.currency if row else None,
                        "description_en": row.ai_description_en if row else None,
                    }
                found.append(info)
                logger.info("[c45-thread-scan] NYTT KVITTO i trådsvar: %s", info)
        time.sleep(0.05)

    with session_scope() as db:
        db.add(MaintenanceTask(name=THREAD_SCAN_TASK))
    summary = {
        "threads": len(threads), "new_receipts": len(found),
        "skipped": result.skipped, "errors": result.errors,
    }
    logger.info("[c45-thread-scan] KLAR: %s", summary)
    return {**summary, "receipts": found}


def _scan_deps() -> dict:
    from app.db import session_scope
    from app.services import pipeline as pl
    from app.services.settings_service import load_settings

    with session_scope() as db:
        st = load_settings(db)
        raw = getattr(st, "html_to_pdf_enabled", None)
        kwargs = {
            "excluded_subjects": list(st.exclude_subjects or []),
            "auto_upload": bool(st.auto_upload_enabled),
            "confidence_threshold": int(st.confidence_threshold or 0),
            "ai_min_confidence": int(st.ai_min_confidence_to_save or 0),
            "link_fetch_senders": list(st.link_fetch_senders or []),
            "html_to_pdf_enabled": True if raw is None else bool(raw),
        }
        ai_enabled = bool(st.ai_naming_enabled)
    analyzer = pl.ReceiptAnalyzer()
    kwargs["use_ai"] = ai_enabled and analyzer.enabled
    bezala = None
    if kwargs["auto_upload"]:
        try:
            bezala = pl.BezalaClient()
            meta = pl.fetch_bezala_metadata(bezala)
            meta["vendor_mappings"] = pl._load_vendor_mappings()
            kwargs["bezala_metadata"] = meta
        except Exception:  # noqa: BLE001
            logger.exception("[c45-thread-scan] Bezala-init misslyckades")
            bezala = None
    return {
        "clients": (pl.DriveClient(), pl.FileNamer(), analyzer, bezala),
        "kwargs": kwargs,
    }


def start_thread_scan_in_background() -> None:
    def _run():
        try:
            thread_scan_2026()
        except Exception:  # noqa: BLE001
            logger.exception("[c45-thread-scan] avbröts — körs igen vid nästa start")

    threading.Thread(target=_run, name="c45-thread-scan", daemon=True).start()


def run_c45_migrations(db: Session) -> None:
    try:
        seed_c45_html_only_senders(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("C45: seed html_only_senders misslyckades")
