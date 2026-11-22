"""C50 — gemensam skyddsfunktion för ALLA Bezala-skrivningar.

Bakgrund (prod 2026-11-22): auto-kopplingen skickade tre utlägg för attest
(Anthropic, Scandic Go, Arlanda Express). Alla tre gick via
`attach_file` → `update_transaction` (PUT /transactions/{id}) med en payload
som innehöll `state: "unapproved"` (+ bill_id, bill_line_id,
connected_to_bill_line, date). Bolt, som kopplades utan PUT, låg kvar som
utkast. Därför:

* `AUTO_COUPLE_ENABLED` (env, default false) styr ALL automatisk koppling och
  alla automatiska Bezala-skrivningar. En databasflagga kan bara stänga av
  (kill switch), aldrig slå på.
* Bara tillåtna fält får skrivas (kvitto/bilaga, description, konto,
  kostnadsställe, moms, kostnadsrader). Status/state/approver/submit nekas.
* Transaktionen läses före och efter varje PUT. Ändras statusfälten (eller
  lämnar en ny transaktion utkastläget) → CRITICAL, kill switch i databasen
  och alla vidare skrivningar stoppas.
* PUT av metadata (description/konto/moms) är AV tills det verifierats att
  Bezala inte skickar för attest av sig självt (`BEZALA_METADATA_PUT_ENABLED`).
  Förslaget sparas i boten i stället så Mikko kan klistra in det själv.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- fält

# Fält som får skrivas via PUT /transactions/{id}. Allt annat nekas.
ALLOWED_TRANSACTION_FIELDS = frozenset({
    "description",
    "credit_account_id", "account_id",
    "cost_center_id",
    "vat_lines_attributes", "vat_id", "vat_rate_id",
    "cost_lines_attributes", "transaction_lines_attributes",
})

# Fält i POST /attachments-formuläret (kvitto/bilaga). C51: `state` skickas
# ALDRIG i någon skrivning (Arlanda m.fl. fick `state=unapproved` i både POST
# och PUT och hamnade i `reviewing`). `description` skickas inte heller.
ALLOWED_ATTACHMENT_FORM_FIELDS = frozenset({"draft", "bill_line_id", "transaction_id"})

# C51 — fält som aldrig får finnas i en skrivning, oavsett lista.
FORBIDDEN_WRITE_FIELDS = frozenset({"state", "status", "workflow_state", "approval_status",
                                    "approval_state", "submit", "submitted"})

# C51 — Bezalas `state` (se docs/bezala_states.md). Läst med GET 2026-11-22
# på alla 421 utlägg i GET /transactions: bara "draft", "reviewing" och
# "accounted" förekommer.
#   "draft"     = Utkast (inte skickat för attest)
#   "reviewing" = Väntar på (andras) attestering
#   "accounted" = Bokfört
# "unapproved" (som gammal kod skickade) förekommer aldrig och räknas INTE
# som utkast. Skrivning tillåts BARA när state är "draft"; saknas
# state-fältet, eller är värdet något annat, nekas skrivningen.
DRAFT_STATES = frozenset({"draft"})

# Statusrelaterade fält vi jämför före/efter.
STATUS_FIELDS = (
    "state", "status", "workflow_state", "approval_status", "approval_state",
    "submitted", "is_submitted", "submitted_at", "sent_for_approval",
    "send_for_approval", "approved", "approved_at", "approver", "approver_id",
    "approvers", "current_approver", "current_approver_id", "draft", "is_draft",
    # C52 — attestanter som Bezala faktiskt returnerar (GET 5579365).
    "current_approvers_ids", "transaction_approvers", "approved_by", "approved_by_ids",
)

# Tillstånd som SÄKERT inte är utkast.
NON_DRAFT_STATES = frozenset({
    "reviewing", "in_review", "review", "pending", "pending_approval",
    "waiting_for_approval", "awaiting_approval", "submitted", "sent",
    "approved", "accepted", "paid", "processing", "processed", "rejected",
    "invoiced", "exported", "accounted", "unapproved",
})

FLAG_AUTO_COUPLE = "auto_couple_enabled"
FLAG_WRITES_HALTED = "bezala_writes_halted"


class BezalaWriteBlocked(RuntimeError):
    """Skrivningen nekades av skyddsfunktionen (fält eller kill switch)."""


# ---------------------------------------------------------------- env


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_auto_couple_enabled() -> bool:
    return _env_bool("AUTO_COUPLE_ENABLED", False)


def metadata_put_enabled() -> bool:
    """PUT av description/konto/moms. Av tills verifierat säkert."""
    return _env_bool("BEZALA_METADATA_PUT_ENABLED", False)


# ---------------------------------------------------------------- db-flaggor


@contextmanager
def _session(db=None) -> Iterator[Any]:
    if db is not None:
        yield db
        return
    from app.db import session_scope

    with session_scope() as s:
        yield s


def _get_flag(db, key: str) -> str | None:
    from app.models import BotFlag

    row = db.query(BotFlag).filter(BotFlag.key == key).first()
    return row.value if row is not None else None


def _set_flag(db, key: str, value: str, reason: str | None) -> None:
    from app.models import BotFlag

    row = db.query(BotFlag).filter(BotFlag.key == key).first()
    if row is None:
        row = BotFlag(key=key, value=value, reason=reason)
        db.add(row)
    else:
        row.value = value
        row.reason = reason
    db.commit()


def _missing_table(exc: Exception) -> bool:
    """bot_flags/bezala_write_log skapas av init_db vid start. Saknas
    tabellen (bara i tester utan init_db) finns heller ingen kill switch."""
    msg = str(exc).lower()
    return "no such table" in msg or ("relation" in msg and "does not exist" in msg)


def writes_halted(db=None) -> bool:
    try:
        with _session(db) as s:
            return (_get_flag(s, FLAG_WRITES_HALTED) or "").lower() == "true"
    except Exception as exc:  # noqa: BLE001 — kan vi inte läsa flaggan: stoppa
        if _missing_table(exc):
            return False
        logger.exception("C50: kunde inte läsa kill switch — behandlar som stoppad")
        return True


def auto_couple_enabled(db=None) -> bool:
    """Automatisk koppling/skrivning tillåten? Kräver env AUTO_COUPLE_ENABLED
    =true OCH att kill switch i databasen inte slagit av den."""
    if not env_auto_couple_enabled():
        return False
    try:
        with _session(db) as s:
            if (_get_flag(s, FLAG_AUTO_COUPLE) or "").lower() == "false":
                return False
            if (_get_flag(s, FLAG_WRITES_HALTED) or "").lower() == "true":
                return False
    except Exception as exc:  # noqa: BLE001
        if _missing_table(exc):
            return True
        logger.exception("C50: kunde inte läsa auto-couple-flaggan — behandlar som av")
        return False
    return True


def trip_kill_switch(reason: str, db=None) -> None:
    """Slå av all automatisk koppling och alla Bezala-skrivningar (beständigt)."""
    logger.critical("C50 KILL SWITCH: %s — AUTO_COUPLE av, Bezala-skrivningar stoppade", reason)
    try:
        with _session(db) as s:
            _set_flag(s, FLAG_AUTO_COUPLE, "false", reason)
            _set_flag(s, FLAG_WRITES_HALTED, "true", reason)
    except Exception:  # noqa: BLE001
        logger.exception("C50: kunde inte spara kill switch i databasen")


def reset_kill_switch(db=None) -> None:
    """Endast på Mikkos uttryckliga begäran. Slår INTE på auto-koppling
    (det styrs av env AUTO_COUPLE_ENABLED) — tar bara bort stoppet."""
    with _session(db) as s:
        _set_flag(s, FLAG_WRITES_HALTED, "false", "reset by Mikko")
        _set_flag(s, FLAG_AUTO_COUPLE, "true", "reset by Mikko")


def kill_switch_status(db=None) -> dict:
    from app.models import BotFlag

    with _session(db) as s:
        rows = {r.key: {"value": r.value, "reason": r.reason,
                        "updated_at": r.updated_at.isoformat() if r.updated_at else None}
                for r in s.query(BotFlag).all()}
    return {
        "env_auto_couple_enabled": env_auto_couple_enabled(),
        "metadata_put_enabled": metadata_put_enabled(),
        "auto_couple_enabled": auto_couple_enabled(),
        "writes_halted": writes_halted(),
        "flags": rows,
    }


# ---------------------------------------------------------------- kontroller


def check_fields(fields: dict[str, Any], allowed=ALLOWED_TRANSACTION_FIELDS) -> None:
    denied = sorted(k for k in (fields or {})
                    if k not in allowed or k in FORBIDDEN_WRITE_FIELDS)
    if denied:
        raise BezalaWriteBlocked(f"Bezala write refused - field(s) not allowed: {denied}")


def ensure_writes_allowed(db=None) -> None:
    if writes_halted(db):
        raise BezalaWriteBlocked(
            "Bezala writes are halted by the kill switch (a transaction left draft state)."
        )


def _unwrap(tx: dict | None) -> dict:
    if not isinstance(tx, dict):
        return {}
    inner = tx.get("transaction")
    return inner if isinstance(inner, dict) else tx


def status_snapshot(tx: dict | None) -> dict:
    t = _unwrap(tx)
    return {k: t.get(k) for k in STATUS_FIELDS if k in t}


def is_definitely_not_draft(snapshot: dict) -> bool:
    for key in ("state", "status", "workflow_state", "approval_status", "approval_state"):
        v = snapshot.get(key)
        if isinstance(v, str) and v.strip().lower() in NON_DRAFT_STATES:
            return True
    for key in ("submitted", "is_submitted", "sent_for_approval", "approved"):
        if snapshot.get(key) is True:
            return True
    for key in ("submitted_at", "approved_at"):
        if snapshot.get(key):
            return True
    return False


def draft_state(snapshot: dict) -> str | None:
    v = (snapshot or {}).get("state")
    return v.strip().lower() if isinstance(v, str) else None


def is_draft(snapshot: dict) -> bool:
    """C51 — tillåtelselista: bara `state` i DRAFT_STATES räknas som utkast.
    Saknas state → inte utkast (skrivningen nekas)."""
    return draft_state(snapshot) in DRAFT_STATES and not is_definitely_not_draft(snapshot)


def status_changed(before: dict, after: dict) -> list[str]:
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


# ---------------------------------------------------------------- logg


def record_write(*, operation: str, transaction_id=None, bill_line_id=None,
                 fields: dict | None = None, before: dict | None = None,
                 after: dict | None = None, outcome: str = "ok",
                 detail: str | None = None, db=None) -> None:
    try:
        from app.models import BezalaWriteLog

        with _session(db) as s:
            s.add(BezalaWriteLog(
                transaction_id=str(transaction_id) if transaction_id else None,
                bill_line_id=str(bill_line_id) if bill_line_id else None,
                operation=operation,
                fields=_loggable(fields),
                state_before=before, state_after=after,
                outcome=outcome, detail=(detail or None) and str(detail)[:2000],
            ))
            s.commit()
    except Exception as exc:  # noqa: BLE001 — loggen får aldrig fälla skrivningen
        if _missing_table(exc):
            logger.warning("C50: bezala_write_log saknas — skrivningen loggas bara i loggen")
            return
        logger.exception("C50: kunde inte logga Bezala-skrivning")


def _loggable(fields: dict | None) -> dict | None:
    if not fields:
        return None
    return {k: (v if isinstance(v, (str, int, float, bool, type(None), list, dict)) else str(v))
            for k, v in fields.items()}


# ---------------------------------------------------------------- skrivningar


def verify_new_draft(client, transaction_id, *, bill_line_id=None,
                     fields: dict | None = None, operation: str = "attach",
                     db=None) -> dict:
    """Efter POST /attachments: läs transaktionen. Är den inte längre ett
    utkast → CRITICAL + kill switch. Returnerar status-snapshot."""
    after: dict = {}
    try:
        after = status_snapshot(client.get_transaction(transaction_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("C50: kunde inte läsa tx %s efter %s: %s", transaction_id, operation, exc)
        record_write(operation=operation, transaction_id=transaction_id,
                     bill_line_id=bill_line_id, fields=fields, outcome="ok",
                     detail=f"post-read failed: {exc}", db=db)
        return {}
    left = is_definitely_not_draft(after) or (
        draft_state(after) is not None and draft_state(after) not in DRAFT_STATES)
    if left:
        record_write(operation=operation, transaction_id=transaction_id,
                     bill_line_id=bill_line_id, fields=fields, after=after,
                     outcome="status_changed", detail="new transaction is not a draft", db=db)
        trip_kill_switch(f"tx {transaction_id} not a draft after {operation}: {after}", db=db)
        raise BezalaWriteBlocked(
            f"Bezala transaction {transaction_id} left draft state after {operation} - "
            "kill switch on, all Bezala writes stopped."
        )
    record_write(operation=operation, transaction_id=transaction_id,
                 bill_line_id=bill_line_id, fields=fields, after=after, db=db)
    return after


def guarded_put(client, transaction_id, fields: dict[str, Any], *, db=None) -> dict:
    """PUT /transactions/{id} med bara tillåtna fält, läsning före och efter.
    Statusändring → CRITICAL + kill switch + BezalaWriteBlocked."""
    check_fields(fields)
    ensure_writes_allowed(db)
    before = status_snapshot(client.get_transaction(transaction_id))
    if not is_draft(before):
        record_write(operation="put", transaction_id=transaction_id, fields=fields,
                     before=before, outcome="blocked",
                     detail=f"state {draft_state(before)!r} is not a draft state - PUT refused",
                     db=db)
        raise BezalaWriteBlocked(
            f"Bezala transaction {transaction_id} is not a draft "
            f"(state={draft_state(before)!r}, draft = {sorted(DRAFT_STATES)}) - not writing to it."
        )
    result = client._put_transaction_unchecked(transaction_id, fields)
    after = status_snapshot(client.get_transaction(transaction_id))
    changed = status_changed(before, after)
    if changed or not is_draft(after):
        record_write(operation="put", transaction_id=transaction_id, fields=fields,
                     before=before, after=after, outcome="status_changed",
                     detail=f"changed: {changed}", db=db)
        trip_kill_switch(
            f"status changed on tx {transaction_id} after PUT {sorted(fields)}: "
            f"{before} -> {after}", db=db,
        )
        raise BezalaWriteBlocked(
            f"Bezala transaction {transaction_id} changed status after the write "
            f"({changed}) - kill switch on, all Bezala writes stopped."
        )
    record_write(operation="put", transaction_id=transaction_id, fields=fields,
                 before=before, after=after, db=db)
    return result
