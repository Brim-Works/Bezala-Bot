"""C47 — API för mobilsidan (/m), svep-kön och desktopens Travel Tinder.

Samma inloggning (session-cookie) som resten av appen. Inget skickas för
attest; koppling = Bezala-UTKAST. Inga skrivningar till Netvisor.
"""

from __future__ import annotations

import logging
from collections import OrderedDict

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CalendarTrip, CardLine, ProcessedMessage
from app.services.calendar_trip_edit import TripEditError

logger = logging.getLogger(__name__)


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


router = APIRouter(dependencies=[Depends(require_auth)])


def _err(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _row(db: Session, message_id: str) -> ProcessedMessage:
    m = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == message_id).first()
    if m is None or m.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    return m


def _trip_for(db: Session, day) -> dict | None:
    from app.services.trip_completeness import _trip_for_date

    if day is None:
        return None
    trips = db.query(CalendarTrip).filter(CalendarTrip.status != "rejected").all()
    t = _trip_for_date(trips, day)
    if t is None:
        return None
    return {"id": t.id, "destination": t.destination, "status": t.status,
            "dates": f"{t.start_date} – {t.end_date}"}


def _receipt_view(db: Session, m: ProcessedMessage) -> dict:
    from app.services.waiting_receipts import receipt_brief, receipt_day, suggest_for_receipt

    sugg = None
    if not m.bezala_transaction_id and m.waiting_status in (
            "waiting_for_transaction", "no_transaction"):
        sugg = suggest_for_receipt(db, m)
    if m.bezala_transaction_id:
        status = "coupled"
    elif sugg:
        status = "suggested"
    elif m.waiting_status == "private":
        status = "private"
    else:
        status = "waiting"
    return {"receipt": receipt_brief(m), "suggestion": sugg, "status": status,
            "trip": _trip_for(db, receipt_day(m))}


# ---------- Del 1: mobil uppladdning ----------


@router.post("/api/m/receipts")
async def upload_mobile_receipt(
    file: UploadFile = File(...),
    comment: str | None = Form(None),
    db: Session = Depends(get_db),
):
    from app.services.receipt_inbox import SOURCE_MOBILE, InboxError, ingest_receipt_file

    data = await file.read()
    try:
        res = ingest_receipt_file(
            db, data, filename=file.filename or "kvitto.jpg",
            declared_mime=file.content_type or "", source=SOURCE_MOBILE,
            sender="mobile@bezala-bot", subject=comment or None,
        )
    except InboxError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("[c47-mobile] uppladdning misslyckades")
        raise HTTPException(status_code=502, detail=f"Upload failed: {exc}") from exc
    return _receipt_view(db, res.row)


@router.get("/api/m/receipts/{message_id}")
def get_mobile_receipt(message_id: str, db: Session = Depends(get_db)):
    return _receipt_view(db, _row(db, message_id))


@router.get("/api/m/card-lines")
def open_card_lines(message_id: str | None = None, db: Session = Depends(get_db)):
    """Öppna kortrader (utan kvitto), närmast kvittots datum först."""
    from app.services.waiting_receipts import receipt_day

    lines = db.query(CardLine).filter(CardLine.status == "missing").all()
    day = receipt_day(_row(db, message_id)) if message_id else None

    def key(c: CardLine):
        if day and c.txn_date:
            return (abs((c.txn_date - day).days), c.bill_line_id)
        return (10**6 - (c.txn_date.toordinal() if c.txn_date else 0), c.bill_line_id)

    return {"card_lines": [{
        "bill_line_id": c.bill_line_id, "description": c.description, "vendor": c.vendor,
        "amount": c.amount, "currency": c.currency,
        "date": c.txn_date.isoformat() if c.txn_date else None,
    } for c in sorted(lines, key=key)]}


class CouplePayload(BaseModel):
    bill_line_id: str


@router.post("/api/m/receipts/{message_id}/couple")
def couple_mobile_receipt(message_id: str, payload: CouplePayload,
                          db: Session = Depends(get_db)):
    from app.services.decision_queue import decide

    _row(db, message_id)
    try:
        decide(db, f"receipt:{payload.bill_line_id}:{message_id}", "yes", source="mobile")
    except TripEditError as exc:
        raise _err(exc) from exc
    return _receipt_view(db, _row(db, message_id))


class OptionalLinePayload(BaseModel):
    bill_line_id: str | None = None
    note: str | None = None


@router.post("/api/m/receipts/{message_id}/private")
def private_mobile_receipt(message_id: str, payload: OptionalLinePayload,
                           db: Session = Depends(get_db)):
    """Privat: kortraden (om föreslagen) markeras som privat utlägg; kvittot
    slutar vänta på transaktion."""
    from app.services import bill_line_status as bls

    m = _row(db, message_id)
    if payload.bill_line_id:
        try:
            bls.mark_private_expense(db, payload.bill_line_id,
                                     payload.note or f"{m.vendor or 'Receipt'} - private")
        except bls.BillLineStatusError as exc:
            raise _err(exc) from exc
    m.waiting_status = "private"
    db.commit()
    return _receipt_view(db, m)


@router.post("/api/m/receipts/{message_id}/keep")
def keep_mobile_receipt(message_id: str, payload: OptionalLinePayload,
                        db: Session = Depends(get_db)):
    """Spara utan koppling: förslaget (om något) avvisas, kvittot väntar kvar."""
    from app.services.decision_queue import decide

    m = _row(db, message_id)
    if payload.bill_line_id:
        decide(db, f"receipt:{payload.bill_line_id}:{message_id}", "no", source="mobile")
    if m.waiting_status not in ("waiting_for_transaction", "no_transaction"):
        m.waiting_status = "waiting_for_transaction"
        db.commit()
    return _receipt_view(db, m)


# ---------- kvittofil (miniatyr) ----------

_FILE_CACHE: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
_FILE_CACHE_MAX = 40


@router.get("/api/receipts/{message_id}/file")
def receipt_file(message_id: str, db: Session = Depends(get_db)):
    from app.services.drive_client import DriveClient
    from app.services.receipt_inbox import sniff_mime

    m = _row(db, message_id)
    if not m.drive_file_id:
        raise HTTPException(status_code=404, detail="No file")
    cached = _FILE_CACHE.get(m.drive_file_id)
    if cached is None:
        try:
            data = DriveClient().download_pdf(m.drive_file_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Drive: {exc}") from exc
        cached = (data, sniff_mime(data, "application/octet-stream") or
                  "application/octet-stream")
        _FILE_CACHE[m.drive_file_id] = cached
        while len(_FILE_CACHE) > _FILE_CACHE_MAX:
            _FILE_CACHE.popitem(last=False)
    data, mime = cached
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "private, max-age=3600"})


# ---------- Del 3: svep-kön ----------


@router.get("/api/queue")
def get_queue(db: Session = Depends(get_db)):
    from app.services.decision_queue import last_decision, list_pending

    out = list_pending(db)
    out["last_decision"] = last_decision(db)
    return out


class DecidePayload(BaseModel):
    item_id: str
    decision: str


@router.post("/api/queue/decide")
def post_decide(payload: DecidePayload, db: Session = Depends(get_db)):
    from app.services.decision_queue import decide

    try:
        return decide(db, payload.item_id, payload.decision, source="ui")
    except TripEditError as exc:
        db.rollback()
        raise _err(exc) from exc


@router.post("/api/queue/undo")
def post_undo(db: Session = Depends(get_db)):
    from app.services.decision_queue import undo_last

    try:
        return undo_last(db)
    except TripEditError as exc:
        db.rollback()
        raise _err(exc) from exc


@router.get("/api/queue/waiting")
def get_waiting(db: Session = Depends(get_db)):
    from app.services.waiting_receipts import list_waiting

    rows = list_waiting(db)
    for r in rows:
        r["trip"] = None
    return {"count": len(rows), "receipts": rows}


@router.get("/api/queue/matched")
def get_matched(month: str | None = None, trip_id: int | None = None,
                db: Session = Depends(get_db)):
    """Redan kopplade kvitto ↔ kortrad + godkända resor, nyast först.
    Återanvänder desktopens /api/bezala/matched-pairs."""
    from app.main import get_matched_pairs
    from app.services.calendar_trip_edit import summary
    from app.services.trip_completeness import _parse_day, _trip_for_date

    pairs = get_matched_pairs(period="all", search=None, db=db, _=None)["pairs"]
    trips = db.query(CalendarTrip).filter(CalendarTrip.status != "rejected").all()
    out = []
    for p in pairs:
        day = _parse_day(p["payment"].get("date")) or _parse_day(p["receipt"].get("receipt_date"))
        t = _trip_for_date(trips, day)
        p["date"] = day.isoformat() if day else None
        p["trip"] = ({"id": t.id, "destination": t.destination,
                      "dates": f"{t.start_date} – {t.end_date}"} if t else None)
        if month and (not p["date"] or not p["date"].startswith(month)):
            continue
        if trip_id and (not t or t.id != trip_id):
            continue
        out.append(p)
    approved = []
    for t in sorted((t for t in trips if t.status == "approved"),
                    key=lambda t: t.start_date, reverse=True):
        if month and not (t.start_date.isoformat()[:7] <= month <= t.end_date.isoformat()[:7]):
            continue
        if trip_id and t.id != trip_id:
            continue
        approved.append(summary(t))
    months = sorted({p["date"][:7] for p in out if p.get("date")}, reverse=True)
    return {"pairs": out, "approved_trips": approved, "months": months,
            "trips": [{"id": t.id, "destination": t.destination,
                       "dates": f"{t.start_date} – {t.end_date}"}
                      for t in sorted(trips, key=lambda t: t.start_date, reverse=True)]}


@router.post("/api/queue/unmatch/{message_id}")
def post_unmatch(message_id: str, db: Session = Depends(get_db)):
    """Koppla loss: ångra matchningen och lägg tillbaka i kön."""
    from app.services.decision_queue import unmatch_receipt

    try:
        return unmatch_receipt(db, message_id)
    except TripEditError as exc:
        db.rollback()
        raise _err(exc) from exc


@router.post("/api/queue/trips/{trip_id}/unapprove")
def post_unapprove(trip_id: int, db: Session = Depends(get_db)):
    from app.services import calendar_trip_edit as edit

    try:
        return edit.unapprove_trip(db, trip_id)
    except TripEditError as exc:
        raise _err(exc) from exc


# ---------- Del 4 + 5 ----------


@router.get("/api/subscriptions")
def get_subscriptions(db: Session = Depends(get_db)):
    from app.services.subscriptions import list_subscriptions

    return {"subscriptions": list_subscriptions(db)}


class RefundPayload(BaseModel):
    bill_line_id: str
    note: str | None = None


@router.get("/api/refunds")
def get_refunds(db: Session = Depends(get_db)):
    from app.services.refunds import list_refunds

    return {"refunds": list_refunds(db)}


@router.post("/api/refunds")
def post_refund(payload: RefundPayload, db: Session = Depends(get_db)):
    from app.services.refunds import expect_refund

    try:
        return expect_refund(db, payload.bill_line_id, payload.note)
    except TripEditError as exc:
        raise _err(exc) from exc


# ---------- översikt (Del 6) ----------


@router.get("/api/overview")
def get_overview(db: Session = Depends(get_db)):
    from app.models import ScanRun
    from app.services.decision_queue import list_pending
    from app.services.waiting_receipts import list_waiting

    last = db.query(ScanRun).order_by(ScanRun.id.desc()).first()
    waiting = [r for r in list_waiting(db) if r["waiting_status"] == "waiting_for_transaction"]
    return {
        "to_decide": list_pending(db)["count"],
        "card_lines_without_receipt": db.query(CardLine)
        .filter(CardLine.status == "missing").count(),
        "receipts_waiting_for_transaction": len(waiting),
        "last_scan": ({
            "started_at": last.started_at.isoformat() if last.started_at else None,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "status": last.status, "found": last.messages_found,
            "processed": last.messages_processed, "errors": last.errors,
        } if last else None),
    }


# ---------- C48 ----------


class AccountPayload(BaseModel):
    account: int | None = None


@router.post("/api/subscriptions/{subscription_id}/account")
def post_subscription_account(subscription_id: int, payload: AccountPayload,
                              db: Session = Depends(get_db)):
    """Mikkos val av Bezala-konto för en prenumeration (gissas aldrig)."""
    from app.services.subscriptions import bezala_accounts, set_account

    try:
        accounts = (bezala_accounts(db).get("accounts")
                    if payload.account is not None else None)
        return set_account(db, subscription_id, payload.account, accounts=accounts)
    except TripEditError as exc:
        db.rollback()
        raise _err(exc) from exc


@router.get("/api/receipt-inbox")
def get_receipt_inbox(db: Session = Depends(get_db)):
    """C48 Del 5: +kvitto-adressen, godkända avsändare, senaste mail."""
    from app.services.kvitto_mail import inbox_info

    return inbox_info(db)


@router.get("/api/cleanup-report")
def get_cleanup_report(rule: str | None = None, limit: int = 50,
                       db: Session = Depends(get_db)):
    from app.services.cleanup_log import report

    return report(db, rule, limit)
