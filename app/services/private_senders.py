"""C46 — avsändare för privat Gmail (tabellen `private_gmail_senders`).

* Tabellen är sanningen. `PRIVATE_GMAIL_SENDER_ALLOWLIST` lägger bara in
  saknade adresser (added_via='env') och tar aldrig bort något.
* Lägg till via UI (Inställningar → Anslutningar → Privat Gmail) eller MCP
  (`add_private_sender`). Samma validering överallt (`validate_sender`):
  exakt adress eller '@domän.se'; publika e-postdomäner och det privata
  kontots egen adress blockeras.
* Ta bort = active=False. Raden finns kvar så env inte lägger tillbaka den.
* Ny avsändare → engångsskanning från 2026-01-01 för just den avsändaren i
  bakgrunden. Idempotent: redan kända meddelanden (ProcessedMessage) hoppas
  över av pipelinen.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import db as _db
from app.models import PrivateGmailSender, ProcessedMessage
from app.services.private_gmail import (
    ACCOUNT_PRIVATE,
    InvalidSender,
    PRIVATE_SCAN_AFTER,
    env_allowlist,
    sender_address,
    sender_matches,
    validate_sender,
)

logger = logging.getLogger(__name__)

VIA_UI, VIA_MCP, VIA_ENV = "ui", "mcp", "env"
BACKFILL_MAX_RESULTS = 500


def _own_private_address() -> list[str]:
    """Det anslutna privata kontots adress (blockeras alltid)."""
    try:
        from app.services.oauth_token_store import get_token_data

        email = (get_token_data("gmail_private") or {}).get("email")
        return [email] if email else []
    except Exception:  # noqa: BLE001
        return []


def seed_from_env(db: Session) -> list[str]:
    """Lägg in env-adresser som saknas i tabellen. Tar aldrig bort och
    återaktiverar aldrig en borttagen rad. Committar. → tillagda."""
    existing = {r.address for r in db.query(PrivateGmailSender.address).all()}
    added: list[str] = []
    for addr in env_allowlist():
        if addr in existing:
            continue
        kind = "domain" if addr.startswith("@") else "email"
        db.add(PrivateGmailSender(address=addr, kind=kind, active=True, added_via=VIA_ENV))
        added.append(addr)
    if added:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return []
        logger.info("C46: private_gmail_senders — från env: %s", added)
    return added


def active_addresses() -> list[str]:
    with _db.session_scope() as db:
        seed_from_env(db)
        return [
            r.address for r in db.query(PrivateGmailSender)
            .filter(PrivateGmailSender.active.is_(True))
            .order_by(PrivateGmailSender.id).all()
        ]


def _stats(db: Session, rows: list[PrivateGmailSender]) -> dict[int, dict]:
    msgs = (
        db.query(ProcessedMessage.sender, ProcessedMessage.status)
        .filter(ProcessedMessage.source_account == ACCOUNT_PRIVATE)
        .all()
    )
    out = {r.id: {"fetched": 0, "saved": 0, "rejected": 0} for r in rows}
    for sender, status in msgs:
        addr = sender_address(sender)
        for r in rows:
            if sender_matches(addr, r.address):
                st = out[r.id]
                st["fetched"] += 1
                if status == "saved":
                    st["saved"] += 1
                else:
                    st["rejected"] += 1
                break
    return out


def _row_dict(r: PrivateGmailSender, stats: dict | None = None) -> dict:
    return {
        "address": r.address,
        "kind": r.kind,
        "active": bool(r.active),
        "added_at": r.added_at.isoformat() if r.added_at else None,
        "added_via": r.added_via,
        "note": r.note,
        "last_scan_at": r.last_scan_at.isoformat() if r.last_scan_at else None,
        "backfill_status": r.backfill_status,
        **(stats or {}),
    }


def last_private_scan(db: Session) -> dict | None:
    from app.models import ScanRun

    run = (
        db.query(ScanRun)
        .filter(ScanRun.private_found.isnot(None))
        .order_by(ScanRun.id.desc()).first()
    )
    if run is None:
        return None
    return {
        "at": (run.finished_at or run.started_at).isoformat()
        if (run.finished_at or run.started_at) else None,
        "found": run.private_found,
        # Mail från avsändare som inte finns i listan: bara antal, inget innehåll.
        "rejected_unknown_sender": run.private_rejected or 0,
    }


def list_senders(db: Session, *, include_inactive: bool = True) -> dict:
    seed_from_env(db)
    q = db.query(PrivateGmailSender).order_by(PrivateGmailSender.id)
    if not include_inactive:
        q = q.filter(PrivateGmailSender.active.is_(True))
    rows = q.all()
    stats = _stats(db, rows)
    return {
        "senders": [_row_dict(r, stats.get(r.id)) for r in rows],
        "last_scan": last_private_scan(db),
    }


def add_sender(db: Session, address: str, note: str | None = None, *,
               via: str = VIA_UI, start_scan: bool = True) -> dict:
    """Validera + lägg till (eller återaktivera). Startar engångsskanningen
    för avsändaren. Kastar InvalidSender."""
    addr, kind = validate_sender(address, blocked_addresses=_own_private_address())
    seed_from_env(db)
    row = db.query(PrivateGmailSender).filter_by(address=addr).first()
    status = "added"
    if row is None:
        row = PrivateGmailSender(
            address=addr, kind=kind, active=True, added_via=via,
            note=(note or None),
        )
        db.add(row)
    elif row.active:
        status = "already_active"
        if note:
            row.note = note
    else:
        status = "reactivated"
        row.active = True
        if note:
            row.note = note
    db.commit()
    scan_started = False
    if status != "already_active" and start_scan:
        scan_started = start_backfill(addr)
    logger.info("C46: privat avsändare %s %s (via %s, skanning=%s)",
                addr, status, via, scan_started)
    return {
        "status": status,
        "sender": _row_dict(row),
        "backfill_started": scan_started,
        "backfill_from": PRIVATE_SCAN_AFTER.replace("/", "-"),
    }


def remove_sender(db: Session, address: str) -> dict:
    addr = (address or "").strip().lower()
    row = db.query(PrivateGmailSender).filter_by(address=addr).first()
    if row is None:
        raise InvalidSender(f"{addr or address} finns inte i listan")
    was_active = bool(row.active)
    row.active = False
    db.commit()
    logger.info("C46: privat avsändare %s borttagen (active=False)", addr)
    return {"status": "removed" if was_active else "already_removed",
            "sender": _row_dict(row)}


def mark_scanned(addresses: list[str], when: datetime | None = None) -> None:
    if not addresses:
        return
    with _db.session_scope() as db:
        for r in (
            db.query(PrivateGmailSender)
            .filter(PrivateGmailSender.address.in_(addresses)).all()
        ):
            r.last_scan_at = when or datetime.utcnow()


def _set_backfill(addr: str, status: str) -> None:
    with _db.session_scope() as db:
        r = db.query(PrivateGmailSender).filter_by(address=addr).first()
        if r is not None:
            r.backfill_status = status
            r.backfill_at = datetime.utcnow()


def run_backfill(addr: str) -> dict:
    """Engångsskanning från 2026-01-01 för EN avsändare. Bara läsning i
    privata Gmail; varje meddelande kontrolleras mot hela den aktiva
    listan innan innehåll hämtas."""
    from app.services import pipeline as pl
    from app.services.c45_migrations import _scan_deps
    from app.services.oauth_token_store import get_refresh_token

    if not get_refresh_token("gmail_private"):
        _set_backfill(addr, "not_connected")
        return {"status": "not_connected"}
    _set_backfill(addr, "running")
    deps = _scan_deps()
    drive, namer, analyzer, bezala = deps["clients"]
    kwargs = dict(deps["kwargs"])
    kwargs.pop("link_fetch_senders", None)
    result = pl.ScanResult()
    try:
        pl.run_private_pass(
            drive, namer, analyzer, bezala, result,
            max_results=BACKFILL_MAX_RESULTS, after=PRIVATE_SCAN_AFTER,
            senders=[addr], **kwargs,
        )
    finally:
        if bezala is not None:
            try:
                bezala.close()
            except Exception:  # noqa: BLE001
                pass
    out = {
        "status": "done", "found": result.private_found,
        "saved": result.processed, "skipped": result.skipped,
        "rejected_unknown_sender": result.private_rejected, "errors": result.errors,
    }
    _set_backfill(addr, "done" if not result.errors else "done_with_errors")
    logger.info("[c46-sender-backfill] %s: %s", addr, out)
    return out


def start_backfill(addr: str) -> bool:
    def _run():
        try:
            run_backfill(addr)
        except Exception:  # noqa: BLE001
            logger.exception("[c46-sender-backfill] %s avbröts", addr)
            try:
                _set_backfill(addr, "error")
            except Exception:  # noqa: BLE001
                pass

    try:
        threading.Thread(target=_run, name=f"c46-backfill-{addr}", daemon=True).start()
        return True
    except Exception:  # noqa: BLE001
        logger.exception("[c46-sender-backfill] kunde inte starta %s", addr)
        return False


__all__ = [
    "InvalidSender", "add_sender", "remove_sender", "list_senders",
    "active_addresses", "seed_from_env", "run_backfill", "start_backfill",
]
