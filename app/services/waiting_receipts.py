"""C47 Del 1.6 — kvitton som väntar på korttransaktion.

Vanligaste fallet: kvittot fotas (t.ex. i taxin) innan kortraden finns i
Bezala — den kommer ofta 1–5 dagar senare. Kvittot sparas då med
waiting_status='waiting_for_transaction'. Vid varje Bezala-synk:

* matcha alla väntande kvitton mot kortrader utan kvitto (leverantör,
  belopp med valutaomräkning, datum ±5 dagar)
* säker träff (samma nivå som auto-matchningen i Match Health: ≥80 poäng
  med tydlig leverantörssignal, eller exakt belopp + leverantör ≥95 %)
  OCH entydig (ingen annan kortrad/inget annat kvitto konkurrerar) →
  koppla automatiskt (Bezala-UTKAST, aldrig attest)
* osäker träff → syns i svep-kön (kvitto ↔ kortrad)
* ingen träff efter 14 dagar → 'no_transaction' (list_missing + svep-kön)

Gäller kvitton från mobilen, +kvitto-mail och Gmail (Gmail-kvitton som
sparats efter C47 tas in automatiskt).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import CardLine, MaintenanceTask, ProcessedMessage

logger = logging.getLogger(__name__)

WAITING = "waiting_for_transaction"
NO_TRANSACTION = "no_transaction"
PRIVATE = "private"
CLOSED = "closed"
COUPLED = "coupled"
# C48 Del 3b — kvitto från PRIVATA Gmail utan kortrad på företagskortet
# efter 14 dagar: privat köp (Apple skickar kvitton för alla App Store-
# köp). Ingen notxn-flagga, inget svepkort, inget Bezala-utkast.
IGNORED_PRIVATE_SOURCE = "ignored_private_source"
OPEN_STATUSES = (WAITING, NO_TRANSACTION)

WINDOW_DAYS = 5
NO_TXN_DAYS = 14
SAFE_SCORE = 80
SAFE_VENDOR_POINTS = 18
MIN_SCORE = 50

ADOPT_TASK = "c47_waiting_receipts_cutoff"


def _today() -> date:
    from app.services.trip_completeness import _today as t

    return t()


def _day(value) -> date | None:
    from app.services.trip_completeness import _parse_day

    return _parse_day(value)


def receipt_day(m: ProcessedMessage) -> date | None:
    return _day(m.receipt_date) or _day(m.received_at)


def open_query(db: Session):
    return (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.waiting_status.in_(OPEN_STATUSES))
        .filter(ProcessedMessage.deleted_at.is_(None))
    )


def _serialize(m: ProcessedMessage) -> dict:
    from app.main import _serialize_message

    return _serialize_message(m)


def _missing(db: Session, c: CardLine) -> dict:
    from app.services.card_line_coupling import _missing_from_card_line

    return _missing_from_card_line(db, c)


def is_safe(score: dict) -> bool:
    b = score.get("breakdown") or {}
    if not b.get("amount"):
        return False
    total = score.get("total") or 0
    vendor = b.get("vendor") or 0
    if total >= SAFE_SCORE and vendor >= SAFE_VENDOR_POINTS:
        return True
    # Exakt samma belopp + leverantör ≥95 % (auto_match_confident).
    return b.get("amount") == 50 and vendor >= 29


def _within_window(a: date | None, b: date | None) -> bool:
    return a is not None and b is not None and abs((a - b).days) <= WINDOW_DAYS


def rejected_pairs(db: Session) -> set[tuple[str, str]]:
    from app.services.decision_queue import rejected_receipt_pairs

    return rejected_receipt_pairs(db)


def score_pairs(db: Session, receipts: list[ProcessedMessage],
                lines: list[CardLine], *, rate=None,
                rejected: set[tuple[str, str]] | None = None) -> list[dict]:
    """Alla par (kvitto, kortrad) inom ±5 dagar med poäng ≥ MIN_SCORE."""
    from app.services.currency_converter import make_db_rate_provider
    from app.services.receipt_matcher import score_match

    rate = rate or make_db_rate_provider(db)
    rejected = rejected or set()
    missing = {c.bill_line_id: _missing(db, c) for c in lines}
    out = []
    for m in receipts:
        rd = receipt_day(m)
        ser = _serialize(m)
        for c in lines:
            if (c.amount or 0) <= 0 or not _within_window(rd, c.txn_date):
                continue
            if (c.bill_line_id, m.message_id) in rejected:
                continue
            s = score_match(missing[c.bill_line_id], ser, rate_provider=rate)
            if s["total"] < MIN_SCORE or not s["breakdown"].get("amount"):
                continue
            out.append({"message": m, "line": c, "score": s, "safe": is_safe(s)})
    out.sort(key=lambda p: -p["score"]["total"])
    return out


def pair_brief(p: dict) -> dict:
    c, s = p["line"], p["score"]
    out = {
        "bill_line_id": c.bill_line_id,
        "description": c.description,
        "vendor": c.vendor,
        "amount": c.amount,
        "currency": c.currency,
        "date": c.txn_date.isoformat() if c.txn_date else None,
        "score": s["total"],
        "safe": p["safe"],
    }
    if s.get("conversion"):
        out["conversion"] = s["conversion"]
    return out


def _missing_lines(db: Session) -> list[CardLine]:
    return db.query(CardLine).filter(CardLine.status == "missing").all()


def suggest_for_receipt(db: Session, m: ProcessedMessage) -> dict | None:
    """Bästa kortrad för ett (nytt) kvitto, eller None."""
    pairs = score_pairs(db, [m], _missing_lines(db), rejected=rejected_pairs(db))
    return pair_brief(pairs[0]) if pairs else None


def adopt_gmail_receipts(db: Session) -> int:
    """Gmail-kvitton sparade efter C47:s driftstart tas in i väntekön.
    Äldre kvitton rörs inte (de har redan hanterats i Match/Granska)."""
    task = db.query(MaintenanceTask).filter(MaintenanceTask.name == ADOPT_TASK).first()
    if task is None:
        db.add(MaintenanceTask(name=ADOPT_TASK, ran_at=datetime.utcnow()))
        db.commit()
        return 0
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.waiting_status.is_(None))
        .filter(ProcessedMessage.status == "saved")
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.duplicate_of.is_(None))
        .filter(ProcessedMessage.bezala_transaction_id.is_(None))
        .filter(ProcessedMessage.processed_at >= task.ran_at)
        .all()
    )
    for m in rows:
        if (m.payment_method or "company_card") != "company_card":
            continue
        m.waiting_status = WAITING
        m.waiting_since = m.processed_at or datetime.utcnow()
    db.commit()
    return len(rows)


def _close_coupled(db: Session) -> int:
    rows = (
        open_query(db)
        .filter(ProcessedMessage.bezala_transaction_id.isnot(None))
        .all()
    )
    dup = open_query(db).filter(ProcessedMessage.duplicate_of.isnot(None)).all()
    for m in rows:
        m.waiting_status = COUPLED
    for m in dup:
        m.waiting_status = CLOSED
    db.commit()
    return len(rows)


def match_waiting_receipts(db: Session, *, couple=None) -> dict:
    """Körs efter varje Bezala-synk. couple(db, bill_line_id, message_id)
    kopplar (default card_line_coupling.couple_receipt → Bezala-utkast)."""
    from app.services.bezala_write_guard import auto_couple_enabled

    # C50 — automatisk koppling (Bezala-skrivning) bara med AUTO_COUPLE_ENABLED.
    auto_ok = couple is not None or auto_couple_enabled(db)
    if couple is None:
        from app.services.card_line_coupling import couple_receipt as couple

    adopted = adopt_gmail_receipts(db)
    _close_coupled(db)
    receipts = (
        open_query(db)
        .filter(ProcessedMessage.bezala_transaction_id.is_(None))
        .filter(ProcessedMessage.duplicate_of.is_(None))
        .all()
    )
    lines = _missing_lines(db)
    rejected = rejected_pairs(db)
    pairs = score_pairs(db, receipts, lines, rejected=rejected)

    # Konkurrens: andra okopplade kvitton (även ej väntande) mot samma rad.
    by_line: dict[str, int] = {}
    by_receipt: dict[str, int] = {}
    for p in pairs:
        by_line[p["line"].bill_line_id] = by_line.get(p["line"].bill_line_id, 0) + 1
        by_receipt[p["message"].message_id] = by_receipt.get(p["message"].message_id, 0) + 1
    waiting_ids = {m.message_id for m in receipts}
    others = [
        m for m in (
            db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.status == "saved")
            .filter(ProcessedMessage.bezala_transaction_id.is_(None))
            .filter(ProcessedMessage.duplicate_of.is_(None))
            .all()
        )
        if m.message_id not in waiting_ids and m.bezala_upload_status != "success"
    ]
    safe_lines = {p["line"].bill_line_id for p in pairs if p["safe"]}
    if others and safe_lines:
        for q in score_pairs(db, others, [c for c in lines if c.bill_line_id in safe_lines],
                             rejected=rejected):
            by_line[q["line"].bill_line_id] = by_line.get(q["line"].bill_line_id, 0) + 1

    coupled, uncertain, failed = [], [], []
    used_lines: set[str] = set()
    for p in pairs:
        bid, mid = p["line"].bill_line_id, p["message"].message_id
        if bid in used_lines or p["message"].bezala_transaction_id:
            continue
        if not p["safe"] or by_line.get(bid, 0) > 1 or by_receipt.get(mid, 0) > 1:
            uncertain.append((bid, mid))
            continue
        if not auto_ok:
            # Ligger kvar som förslag i svep-kön — Mikko säger ja själv.
            uncertain.append((bid, mid))
            continue
        try:
            couple(db, bid, mid)
        except Exception as exc:  # noqa: BLE001 — en rad får aldrig fälla synken
            db.rollback()
            logger.warning("[c47-waiting] auto-koppling %s ← %s misslyckades: %s", bid, mid, exc)
            failed.append({"bill_line_id": bid, "message_id": mid, "error": str(exc)[:200]})
            continue
        used_lines.add(bid)
        row = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == mid).first()
        if row is not None:
            row.waiting_status = COUPLED
            db.commit()
        coupled.append({"bill_line_id": bid, "message_id": mid,
                        "score": p["score"]["total"]})
        logger.info("[c47-waiting] auto-kopplat %s ← %s (%s p)", bid, mid, p["score"]["total"])

    # C50 Del 3 — kvitto utan öppen kortrad: redan hanterat i Bezala?
    # Ingen träff och äldre än 14 dagar → probably_handled_elsewhere
    # (granskningslista, inte svep-kön). Yngre väntar som förut.
    from app.services import c50_handled as handled

    paired = {p["message"].message_id for p in pairs}
    ignored = ignore_private_source(db)
    flagged = 0
    already = 0
    for m in open_query(db).filter(ProcessedMessage.waiting_status == WAITING).all():
        if m.bezala_transaction_id or m.duplicate_of or m.message_id in paired:
            continue
        if handled.apple_line_for(db, m, statuses=("missing",)) is not None:
            continue
        new, hit = handled.classify(db, m)
        if new is None:
            continue
        m.waiting_status = new
        if new == handled.ALREADY_HANDLED:
            already += 1
        else:
            flagged += 1
    db.commit()
    result = {"adopted": adopted, "waiting": len(receipts), "auto_coupled": coupled,
              "uncertain": len(uncertain), "failed": failed,
              "probably_handled_elsewhere": flagged, "already_handled_in_bezala": already,
              "ignored_private_source": ignored,
              "auto_couple_enabled": auto_ok}
    logger.info("[c47-waiting] %s", {k: (len(v) if isinstance(v, list) else v)
                                     for k, v in result.items()})
    return result


def is_private_source(m: ProcessedMessage) -> bool:
    from app.services.private_gmail import ACCOUNT_PRIVATE

    return (m.source_account or "visma") == ACCOUNT_PRIVATE


def ignore_private_source(db: Session, now: datetime | None = None) -> int:
    """Privata Gmail-kvitton som väntat ≥14 dagar utan kortrad (även de som
    redan flaggats no_transaction) → ignored_private_source. Loggas i
    cleanup_log (regel ignored_private_source). Committar inte."""
    from app.services.cleanup_log import log

    cutoff = (now or datetime.utcnow()) - timedelta(days=NO_TXN_DAYS)
    n = 0
    for m in open_query(db).filter(ProcessedMessage.bezala_transaction_id.is_(None)).all():
        if not is_private_source(m):
            continue
        # Räkna från kvittots datum (köpet), inte från när boten sparade det:
        # omläsningen 2026 sparade gamla Apple-kvitton nyligen.
        since = m.waiting_since or m.processed_at
        rd = receipt_day(m)
        if rd is not None:
            bought = datetime.combine(rd, datetime.min.time())
            since = min(since, bought) if since else bought
        if m.waiting_status == WAITING and (since is None or since > cutoff):
            continue
        # C50 Del 2 — Apple-köp på företagskortet (APPLE.COM/BILL e.d.).
        from app.services import c50_handled as handled

        if handled.apple_line_for(db, m, statuses=("missing",)) is not None:
            continue  # öppen kortrad finns → förslag i svep-kön
        if handled.apple_line_for(db, m, statuses=("resolved",)) is not None:
            m.waiting_status = handled.ALREADY_HANDLED
            continue
        before = m.waiting_status
        m.waiting_status = IGNORED_PRIVATE_SOURCE
        n += 1
        log(db, "waiting_receipts", "ignored_private_source", m.message_id,
            before={"waiting_status": before, "vendor": m.vendor, "amount": m.amount,
                    "currency": m.currency, "date": m.receipt_date, "sender": m.sender},
            after={"waiting_status": IGNORED_PRIVATE_SOURCE},
            reason="private Gmail receipt without a company card charge after 14 days")
    db.flush()
    return n


def receipt_brief(m: ProcessedMessage, today: date | None = None) -> dict:
    today = today or _today()
    since = m.waiting_since or m.processed_at
    return {
        "id": m.id,
        "message_id": m.message_id,
        "vendor": m.vendor,
        "amount": m.amount,
        "currency": m.currency,
        "date": m.receipt_date,
        "category": m.category,
        "source": m.upload_source,
        "file_name": m.file_name,
        "drive_file_id": m.drive_file_id,
        "drive_link": m.drive_link,
        "waiting_status": m.waiting_status,
        "waiting_since": since.isoformat() if since else None,
        "waiting_days": (today - since.date()).days if since else None,
        "review_flag": m.review_flag,
    }


def list_waiting(db: Session) -> list[dict]:
    rows = (
        open_query(db)
        .filter(ProcessedMessage.bezala_transaction_id.is_(None))
        .order_by(ProcessedMessage.id.desc())
        .all()
    )
    today = _today()
    return [receipt_brief(m, today) for m in rows]


def list_without_transaction(db: Session) -> list[dict]:
    return [r for r in list_waiting(db) if r["waiting_status"] == NO_TRANSACTION]
