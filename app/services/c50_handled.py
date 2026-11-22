"""C50 Del 2 + Del 3 — Apple-köp på företagskortet och kvitton som redan är
hanterade i Bezala.

Del 2: ChatGPT m.fl. köps via Apple och dras från företagskortet. Kortraden
heter "APPLE.COM/BILL", "ITUNES.COM" e.d. — inte appnamnet. Apple-kvitton
matchas därför mot Apple-kortrader på belopp + datum (±3 dagar), både öppna
och redan hanterade (stängda). Kvittots appnamn blir beskrivningen.

Del 3: ett kvitto utan öppen kortrad är sannolikt redan hanterat manuellt
(via boten eller direkt i Bezala):
* träff på en redan hanterad kortrad/transaktion (samma belopp, även
  omräknat, datum ±5 dagar) → `already_handled_in_bezala` (ingen flagga,
  inget nytt utkast),
* ingen träff och äldre än 14 dagar → `probably_handled_elsewhere`
  (granskningslista, inte svep-kön),
* yngre än 14 dagar → väntar som förut.

Inget skrivs till Bezala.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import CardLine, MaintenanceTask, ProcessedMessage, Subscription

logger = logging.getLogger(__name__)

ALREADY_HANDLED = "already_handled_in_bezala"
PROBABLY_HANDLED = "probably_handled_elsewhere"
APPLE_WINDOW_DAYS = 3
HANDLED_WINDOW_DAYS = 5
OLD_DAYS = 14
FX_TOLERANCE = 0.03  # bankens valutapåslag
RERUN_TASK = "c50_rerun_v1"

_APPLE_LINE_RE = re.compile(r"apple\s*\.?\s*com|itunes|apple services|app store", re.I)


# ---------------------------------------------------------------- hjälp


def _day(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def receipt_day(m: ProcessedMessage) -> date | None:
    return _day(m.receipt_date) or _day(m.received_at)


def is_apple_line(c: CardLine) -> bool:
    return bool(_APPLE_LINE_RE.search(f"{c.description or ''} {c.vendor or ''}"))


def is_apple_receipt(m: ProcessedMessage) -> bool:
    from app.services.apple_receipt import is_apple_sender

    return is_apple_sender(m.sender) or (m.vendor or "").lower().startswith("apple")


def amounts_match(amount, currency, other_amount, other_currency, day: date | None,
                  rate=None) -> bool:
    if amount is None or other_amount is None:
        return False
    a, b = abs(float(amount)), abs(float(other_amount))
    cur, ocur = (currency or "EUR").upper(), (other_currency or "EUR").upper()
    if cur == ocur:
        return abs(a - b) <= 0.01
    if rate is None or day is None:
        return False
    try:
        r = rate(day.isoformat(), cur, ocur)
    except Exception:  # noqa: BLE001
        r = None
    if not r:
        return False
    conv = a * float(r)
    return abs(conv - b) <= max(0.02, FX_TOLERANCE * b)


def _rate(db: Session):
    from app.services.currency_converter import make_db_rate_provider

    return make_db_rate_provider(db)


# ---------------------------------------------------------------- Del 2


def apple_line_for(db: Session, m: ProcessedMessage, *, statuses=("missing", "resolved"),
                   rate=None) -> CardLine | None:
    """Apple-kortrad (öppen eller redan hanterad) med samma belopp, ±3 dagar."""
    if not is_apple_receipt(m) or m.amount is None:
        return None
    d = receipt_day(m)
    if d is None:
        return None
    rate = rate or _rate(db)
    best = None
    for c in (db.query(CardLine).filter(CardLine.status.in_(statuses))
              .filter(CardLine.txn_date >= d - timedelta(days=APPLE_WINDOW_DAYS))
              .filter(CardLine.txn_date <= d + timedelta(days=APPLE_WINDOW_DAYS)).all()):
        if not is_apple_line(c) or (c.amount or 0) <= 0:
            continue
        if not amounts_match(m.amount, m.currency, c.amount, c.currency, d, rate):
            continue
        if best is None or abs((c.txn_date - d).days) < abs((best.txn_date - d).days):
            best = c
    return best


def apple_suggestions(db: Session, lines: list[CardLine], used: set[str],
                      exclude_pairs: set[tuple[str, str]]) -> dict[str, ProcessedMessage]:
    """{bill_line_id: Apple-kvitto} för öppna Apple-kortrader utan förslag."""
    apple_lines = [c for c in lines if is_apple_line(c)]
    if not apple_lines:
        return {}
    rate = _rate(db)
    receipts = [
        m for m in (db.query(ProcessedMessage)
                    .filter(ProcessedMessage.deleted_at.is_(None))
                    .filter(ProcessedMessage.status == "saved")
                    .filter(ProcessedMessage.bezala_transaction_id.is_(None))
                    .filter(ProcessedMessage.duplicate_of.is_(None)).all())
        if is_apple_receipt(m) and m.message_id not in used and m.amount is not None
        and m.waiting_status != ALREADY_HANDLED
    ]
    out: dict[str, ProcessedMessage] = {}
    for c in sorted(apple_lines, key=lambda x: x.txn_date or date.min):
        for m in sorted(receipts, key=lambda r: abs(((receipt_day(r) or date.min)
                                                     - (c.txn_date or date.min)).days)):
            d = receipt_day(m)
            if m.message_id in used or (c.bill_line_id, m.message_id) in exclude_pairs:
                continue
            if d is None or c.txn_date is None or abs((d - c.txn_date).days) > APPLE_WINDOW_DAYS:
                continue
            if amounts_match(m.amount, m.currency, c.amount, c.currency, d, rate):
                out[c.bill_line_id] = m
                used.add(m.message_id)
                break
    return out


def app_description(m: ProcessedMessage) -> str | None:
    """Kvittots appnamn → beskrivning (ChatGPT Plus subscription - Nov 2026)."""
    hay = " ".join((x or "").lower() for x in (m.vendor, m.summary, m.subject, m.file_name,
                                               m.ai_description_en))
    d = receipt_day(m)
    if d is None:
        return None
    if "chatgpt" in hay or "openai" in hay:
        return f"ChatGPT Plus subscription - {d.strftime('%b %Y')}"
    return None


# ---------------------------------------------------------------- Del 3


def _bezala_tx_amount(tx: dict):
    for k in ("amount", "total", "sum", "total_amount"):
        if tx.get(k) is not None:
            try:
                return float(tx[k])
            except (TypeError, ValueError):
                return None
    return None


def _bezala_tx_handled(tx: dict) -> bool:
    if tx.get("attachments") or tx.get("attachment_ids") or tx.get("receipt_url") \
            or tx.get("has_attachment"):
        return True
    from app.services.bezala_write_guard import is_definitely_not_draft, status_snapshot

    return is_definitely_not_draft(status_snapshot(tx))


def find_handled(db: Session, m: ProcessedMessage, *, rate=None,
                 bezala_transactions: list[dict] | None = None) -> dict | None:
    """Redan hanterad kortrad/transaktion med samma belopp (+ omräkning),
    datum ±5 dagar. None = ingen träff."""
    if m.amount is None:
        return None
    d = receipt_day(m)
    if d is None:
        return None
    rate = rate or _rate(db)
    lo, hi = d - timedelta(days=HANDLED_WINDOW_DAYS), d + timedelta(days=HANDLED_WINDOW_DAYS)

    # 1) kortrad som fått kvitto i Bezala (försvann ur "saknar kvitto").
    for c in (db.query(CardLine).filter(CardLine.status == "resolved")
              .filter(CardLine.txn_date >= lo).filter(CardLine.txn_date <= hi).all()):
        if amounts_match(m.amount, m.currency, c.amount, c.currency, d, rate):
            return {"source": "card_line", "bill_line_id": c.bill_line_id,
                    "bezala_transaction_id": c.bezala_transaction_id,
                    "description": c.description, "amount": c.amount, "currency": c.currency,
                    "date": c.txn_date.isoformat() if c.txn_date else None}

    # 2) annat kvitto som redan är kopplat/uppladdat (via boten).
    for o in (db.query(ProcessedMessage)
              .filter(ProcessedMessage.id != m.id)
              .filter(ProcessedMessage.deleted_at.is_(None))
              .filter(ProcessedMessage.bezala_transaction_id.isnot(None)).all()):
        od = receipt_day(o) or _day(o.bezala_payment_date)
        if od is None or not (lo <= od <= hi):
            continue
        if amounts_match(m.amount, m.currency, o.amount, o.currency, d, rate) or \
                amounts_match(m.amount, m.currency, o.bezala_payment_amount,
                              o.bezala_payment_currency, d, rate):
            return {"source": "coupled_receipt", "message_id": o.message_id,
                    "bezala_transaction_id": o.bezala_transaction_id,
                    "vendor": o.vendor, "amount": o.amount, "currency": o.currency,
                    "date": o.receipt_date}

    # 3) utlägg i Bezala med bilaga eller som redan skickats (läst cache).
    if bezala_transactions is None:
        from app.services.c50_accounts import reference

        bezala_transactions = reference()[1]
    for tx in bezala_transactions or []:
        td = _day(tx.get("date"))
        if td is None or not (lo <= td <= hi) or not _bezala_tx_handled(tx):
            continue
        if amounts_match(m.amount, m.currency, _bezala_tx_amount(tx), tx.get("currency"),
                         d, rate):
            return {"source": "bezala_transaction", "bezala_transaction_id": tx.get("id"),
                    "description": tx.get("description"), "date": td.isoformat()}
    return None


def classify(db: Session, m: ProcessedMessage, *, today: date | None = None,
             rate=None, has_open_line: bool = False) -> tuple[str | None, dict | None]:
    """Ny waiting_status för ett okopplat kvitto utan säker öppen kortrad,
    eller (None, None) = låt det vänta."""
    if has_open_line:
        return None, None
    hit = find_handled(db, m, rate=rate)
    if hit is not None:
        return ALREADY_HANDLED, hit
    today = today or datetime.utcnow().date()
    d = receipt_day(m)
    since = d or _day(m.waiting_since) or _day(m.processed_at)
    if since is not None and (today - since).days > OLD_DAYS:
        return PROBABLY_HANDLED, None
    return None, None


def list_probably_handled(db: Session) -> list[dict]:
    """Granskningslista (inte svep-kön)."""
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.waiting_status == PROBABLY_HANDLED)
            .order_by(ProcessedMessage.receipt_date.desc()).all())
    return [{"message_id": m.message_id, "vendor": m.vendor, "amount": m.amount,
             "currency": m.currency, "date": m.receipt_date, "source": m.upload_source,
             "source_account": m.source_account, "drive_link": m.drive_link}
            for m in rows]


# ---------------------------------------------------------------- omkörning


def _counts(db: Session) -> dict:
    from sqlalchemy import func

    rows = (db.query(ProcessedMessage.waiting_status, func.count(ProcessedMessage.id))
            .filter(ProcessedMessage.deleted_at.is_(None))
            .group_by(ProcessedMessage.waiting_status).all())
    return {str(k): n for k, n in rows if k}


def _open_missing_pair(db: Session, m: ProcessedMessage, lines: list[CardLine], rate) -> bool:
    from app.services.waiting_receipts import score_pairs

    if apple_line_for(db, m, statuses=("missing",), rate=rate) is not None:
        return True
    return bool(score_pairs(db, [m], lines, rate=rate))


def ensure_chatgpt_subscription(db: Session) -> dict | None:
    """Del 2.4 — ChatGPT via Apple blir ett prenumerationsförslag (aktiveras
    först när Mikko satt konto)."""
    subs = [s for s in db.query(Subscription).all()
            if any(w in (s.vendor_pattern or "").lower() for w in ("chatgpt", "openai"))]
    note = "C50: paid via Apple on the company card"
    for s in subs:
        if s.review_status in ("confirmed", "private"):
            return {"id": s.id, "name": s.name, "status": s.review_status}
    if subs:
        s = subs[0]
        s.review_status = "suggested"
        s.active = False
        s.note = note
        s.vendor_pattern = "chatgpt|openai"
        s.description_template = s.description_template or "ChatGPT Plus subscription - {month}"
    else:
        s = Subscription(name="ChatGPT Plus (Apple)", vendor_pattern="chatgpt|openai",
                         sender="email.apple.com", origin="detected",
                         review_status="suggested", active=False, note=note,
                         description_template="ChatGPT Plus subscription - {month}")
        db.add(s)
    db.commit()
    return {"id": s.id, "name": s.name, "status": s.review_status}


def rerun(db: Session, *, force: bool = False, today: date | None = None) -> dict | None:
    """Kör om ignored_private_source, no_transaction och väntande kvitton med
    C50-reglerna. Idempotent; körs en gång vid start (force=True = igen)."""
    from app.services.cleanup_log import log
    from app.services.waiting_receipts import (
        IGNORED_PRIVATE_SOURCE, NO_TRANSACTION, WAITING, _missing_lines,
    )

    if not force:
        if db.query(MaintenanceTask).filter(MaintenanceTask.name == RERUN_TASK).first():
            return None
        db.add(MaintenanceTask(name=RERUN_TASK))
        db.commit()
    before = _counts(db)
    rate = _rate(db)
    lines = _missing_lines(db)
    moved: dict[str, list[dict]] = {"back_to_waiting": [], ALREADY_HANDLED: [],
                                    PROBABLY_HANDLED: []}
    chatgpt = 0
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.bezala_transaction_id.is_(None))
            .filter(ProcessedMessage.duplicate_of.is_(None))
            .filter(ProcessedMessage.waiting_status.in_(
                (IGNORED_PRIVATE_SOURCE, NO_TRANSACTION, WAITING))).all())
    for m in rows:
        old = m.waiting_status
        desc = app_description(m) if is_apple_receipt(m) else None
        if desc and desc != m.ai_description_en:
            m.ai_description_en = desc
            chatgpt += 1
        brief = {"message_id": m.message_id, "vendor": m.vendor, "amount": m.amount,
                 "currency": m.currency, "date": m.receipt_date, "before": old}
        if _open_missing_pair(db, m, lines, rate):
            if old != WAITING:
                m.waiting_status = WAITING
                moved["back_to_waiting"].append(brief)
                log(db, "c50", "back_to_waiting", m.message_id, before={"waiting_status": old},
                    after={"waiting_status": WAITING}, reason="open company card line found")
            continue
        apple_closed = apple_line_for(db, m, statuses=("resolved",), rate=rate)
        if old == IGNORED_PRIVATE_SOURCE and apple_closed is None:
            continue  # privat kvitto utan Apple-kortrad: fortfarande privat
        if apple_closed is not None:
            new, hit = ALREADY_HANDLED, {"source": "apple_card_line",
                                         "bill_line_id": apple_closed.bill_line_id}
        else:
            new, hit = classify(db, m, today=today, rate=rate)
        if new is None and old == NO_TRANSACTION:
            new = WAITING
        if new is None or new == old:
            continue
        m.waiting_status = new
        moved.setdefault(new, []).append({**brief, "match": hit})
        log(db, "c50", new, m.message_id, before={"waiting_status": old},
            after={"waiting_status": new, "match": hit},
            reason="C50 rules: Apple card lines / already handled in Bezala")
    db.commit()
    sub = ensure_chatgpt_subscription(db) if any(
        "chatgpt" in (m.ai_description_en or "").lower() for m in rows) else None
    result = {"before": before, "after": _counts(db),
              "moved": {k: v for k, v in moved.items()},
              "counts": {k: len(v) for k, v in moved.items()},
              "chatgpt_descriptions": chatgpt, "chatgpt_subscription": sub}
    logger.info("[c50-rerun] %s", {"before": before, "after": result["after"],
                                   "counts": result["counts"]})
    return result
