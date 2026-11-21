"""C46 Del 4 — läs om jobb-Gmail för 2026 (engångsjobb).

Varför: den vanliga sökningen har `after:2026/03/21`, så kvitton från
januari-mars nådde aldrig boten (t.ex. Clarion 13 jan, Riverside
Ängelholm 20 jan, Story Hotel Studio Malmö 10 mar, Moovy 13 jan / 5 mar /
10-11 mar).

* Samma sökningar som skanningen (standard + html-only), men från
  2026-01-01 och utan Bezala-Klar-filtret.
* Plus Mikkos egna vidarebefordringar till Bezalas kvittoadress
  (`to:app.bezala.com`), så kvitton från avsändare utanför listan hittas.
* ALLA meddelanden i varje träffad tråd bedöms (inte bara det första),
  utom Mikkos egna (SENT) när ett original finns.
* Originalmailet används. Finns bara vidarebefordran (Fwd) används den och
  raden markeras `source_note='forwarded'`.
* Allt som redan finns i ProcessedMessage hoppas över.
* Torrläge först (bara metadata: avsändare/ämne/datum), sedan riktig körning.
* Bezala: INGA utkast skapas av jobbet (auto_upload av) — flera av kvittona
  har Mikko redan skickat till Bezala själv. Inget skickas för attest.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter

from app.models import MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

TASK_NAME = "c46_rescan_work_2026_v1"
AFTER = "2026/01/01"
MAX_RESULTS = 2000
FORWARD_QUERY = (
    f"in:sent to:app.bezala.com subject:(Fwd OR Fw OR VS OR WG OR Vb) after:{AFTER}"
)
_FWD_RE = re.compile(r"^\s*((fwd?|vs|wg|vb|tr)\s*:\s*)+", re.IGNORECASE)

# De sex exemplen i uppdraget (ämnesfragment, gemener).
EXAMPLES: dict[str, tuple[str, ...]] = {
    "Clarion 13 jan": ("se131b0074992",),
    "Riverside Ängelholm 20 jan": ("kvitto kontantfaktura",),
    "Story Hotel Studio Malmö 10 mar": ("invoice for your stay from 10-03-26",),
    "Moovy 13 jan": ("32 973 737",),
    "Moovy 5 mar": ("34 793 894",),
    "Moovy 10-11 mar": ("34 998 659",),
}


def is_forward(subject: str | None) -> bool:
    return bool(_FWD_RE.match(subject or ""))


def rescan_queries(settings_row, html_only_patterns, done_label: str) -> list[str]:
    from app.services.settings_service import (
        build_gmail_query,
        build_gmail_query_html_only,
    )

    out = []
    for q in (
        build_gmail_query(settings_row, done_label=done_label,
                          html_only_patterns=html_only_patterns),
        build_gmail_query_html_only(settings_row, html_only_patterns,
                                    done_label=done_label),
    ):
        if not q:
            continue
        q = q.replace(f'-label:"{done_label}" ', "").replace(f'-label:"{done_label}"', "")
        q = re.sub(r"after:\d{4}/\d{2}/\d{2}", f"after:{AFTER}", q)
        out.append(q.strip())
    out.append(FORWARD_QUERY)
    return out


def pick_messages(thread_msgs: list[dict], own_address: str) -> list[dict]:
    """Välj vad som ska bedömas i en tråd: alla meddelanden som inte är
    Mikkos egna. Finns inga sådana (bara vidarebefordran kvar) → de egna
    vidarebefordringarna, markerade forwarded=True."""
    from app.services.private_gmail import sender_address

    own = (own_address or "").lower()
    originals, forwards = [], []
    for m in thread_msgs:
        labels = set(m.get("labels") or [])
        if "DRAFT" in labels:
            continue
        addr = sender_address(m.get("sender"))
        mine = "SENT" in labels or (own and addr == own)
        if mine:
            if is_forward(m.get("subject")):
                forwards.append({**m, "forwarded": True})
            continue
        originals.append({**m, "forwarded": False})
    return originals or forwards


def _known_ids(ids: list[str]) -> set[str]:
    from app import db as _db

    if not ids:
        return set()
    with _db.session_scope() as db:
        return {
            r.message_id for r in db.query(ProcessedMessage.message_id)
            .filter(ProcessedMessage.message_id.in_(ids)).all()
        }


def collect_candidates(gmail, queries: list[str]) -> list[dict]:
    """Alla meddelanden att bedöma (metadata), utan dubbletter och utan
    sådana som redan finns i ProcessedMessage."""
    own = ""
    try:
        own = gmail.get_profile_email()
    except Exception:  # noqa: BLE001
        logger.warning("[c46-rescan] kunde inte läsa egen adress")
    threads: list[str] = []
    for q in queries:
        try:
            pairs = gmail.list_candidates_with_threads(q, max_results=MAX_RESULTS)
        except Exception:  # noqa: BLE001
            logger.exception("[c46-rescan] sökning misslyckades: %s", q[:200])
            continue
        logger.info("[c46-rescan] sökning gav %d träffar: %s", len(pairs), q[:300])
        for _mid, tid in pairs:
            if tid and tid not in threads:
                threads.append(tid)
    out: list[dict] = []
    seen: set[str] = set()
    for tid in threads:
        try:
            msgs = gmail.list_thread_messages(tid)
        except Exception:  # noqa: BLE001
            logger.exception("[c46-rescan] kunde inte läsa tråd %s", tid)
            continue
        picked = [m for m in pick_messages(msgs, own) if m["message_id"] not in seen]
        known = _known_ids([m["message_id"] for m in picked])
        for m in picked:
            seen.add(m["message_id"])
            if m["message_id"] in known:
                continue
            out.append({**m, "thread_id": tid})
        time.sleep(0.02)
    return out


def report(candidates: list[dict]) -> dict:
    from app.services.private_gmail import sender_address

    per_sender = Counter(sender_address(c.get("sender")) or "?" for c in candidates)
    examples = {}
    for name, frags in EXAMPLES.items():
        hit = next(
            (c for c in candidates
             if any(f in (c.get("subject") or "").lower() for f in frags)),
            None,
        )
        examples[name] = (
            {"found": True, "message_id": hit["message_id"],
             "forwarded": hit.get("forwarded", False), "date": hit.get("date")}
            if hit else {"found": False}
        )
    return {
        "candidates": len(candidates),
        "forwarded_only": sum(1 for c in candidates if c.get("forwarded")),
        "per_sender": dict(per_sender.most_common()),
        "examples": examples,
    }


def run(dry_run: bool = True, gmail=None) -> dict:
    from app import db as _db
    from app.services import pipeline as pl
    from app.services.c45_migrations import _scan_deps
    from app.services.gmail_client import DONE_LABEL
    from app.services.html_only_senders import list_active_patterns
    from app.services.settings_service import load_settings

    with _db.session_scope() as db:
        st = load_settings(db)
        queries = rescan_queries(st, list_active_patterns(db), DONE_LABEL)
    gmail = gmail or pl.GmailClient()
    cands = collect_candidates(gmail, queries)
    rep = report(cands)
    logger.info("[c46-rescan] %s: %s", "TORRLÄGE" if dry_run else "KANDIDATER", rep)
    if dry_run:
        return {"dry_run": True, **rep}

    deps = _scan_deps()
    kwargs = dict(deps["kwargs"])
    kwargs["auto_upload"] = False  # inga Bezala-utkast från omläsningen
    kwargs.pop("bezala_metadata", None)
    drive, namer, analyzer, _bezala = deps["clients"]
    result = pl.ScanResult()
    saved: list[dict] = []
    for c in cands:
        before = result.processed
        try:
            pl._process_one_message(
                c["message_id"], gmail, drive, namer, analyzer, None, result, **kwargs,
            )
        except Exception:  # noqa: BLE001
            logger.exception("[c46-rescan] fel för %s", c["message_id"])
            result.errors += 1
            continue
        if result.processed > before:
            with _db.session_scope() as db:
                row = db.query(ProcessedMessage).filter_by(message_id=c["message_id"]).first()
                if row is not None and c.get("forwarded"):
                    row.source_note = "forwarded"
                info = {
                    "message_id": c["message_id"], "sender": c.get("sender"),
                    "subject": c.get("subject"), "forwarded": c.get("forwarded", False),
                    "vendor": row.vendor if row else None,
                    "amount": row.amount if row else None,
                    "currency": row.currency if row else None,
                    "receipt_date": row.receipt_date if row else None,
                }
            saved.append(info)
            logger.info("[c46-rescan] NYTT KVITTO: %s", info)
    out = {
        "dry_run": False, **rep, "saved": len(saved), "skipped": result.skipped,
        "errors": result.errors, "saved_receipts": saved,
    }
    logger.info("[c46-rescan] KLAR: %s", {k: v for k, v in out.items() if k != "saved_receipts"})
    return out


def run_once() -> dict:
    """Torrläge, sedan riktig körning. En gång (MaintenanceTask)."""
    from app import db as _db

    with _db.session_scope() as db:
        if db.query(MaintenanceTask).filter_by(name=TASK_NAME).first():
            return {"skipped": "already_done"}
    dry = run(dry_run=True)
    real = run(dry_run=False)
    with _db.session_scope() as db:
        db.add(MaintenanceTask(name=TASK_NAME))
    return {"dry_run": dry, "real": real}


def start_background() -> None:
    def _run():
        try:
            run_once()
        except Exception:  # noqa: BLE001
            logger.exception("[c46-rescan] avbröts — körs igen vid nästa start")

    threading.Thread(target=_run, name="c46-rescan", daemon=True).start()
