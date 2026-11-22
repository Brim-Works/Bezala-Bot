"""C47 Del 3 — svep-kön ("Travel Tinder").

En kö med allt som väntar på Mikkos beslut, ett kort i taget:

  receipt:<bill_line_id>:<message_id>  kvitto ↔ kortrad (suggested_receipt)
  trip:<id>                            resa med status suggested/edited
  sub:<id>                             prenumerationsförslag (Del 4)
  subpriv:<id>                         C48: troligen privat prenumeration
  subdev:<bill_line_id>                prenumeration med avvikande belopp
  subnr:<bill_line_id>                 prenumeration utan kvitto 5 dagar
  dup:<datum>:<belopp>:<leverantör>    fler kortrader än kvitton (Del 5)
  notxn:<message_id>                   kvitto utan transaktion efter 14 dagar
  refund:<id>                          väntad återbetalning >30 dagar

decide(item_id, yes|no|later). Höger = ja, vänster = nej, upp = senare.
'no' på ett kvitto/kortrad-par = paret föreslås aldrig igen. Varje beslut
loggas i queue_decisions och kan ångras (undo_last).

Samma kö används av mobilen (/m/swipe), desktop och MCP
(list_pending_decisions / decide). Inget skickas för attest; koppling =
Bezala-UTKAST. Inga skrivningar till Netvisor.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import (
    CalendarTrip, CardLine, ExpectedRefund, ProcessedMessage, QueueDecision, Subscription,
)
from app.services.calendar_trip_edit import TripEditError

logger = logging.getLogger(__name__)

DECISIONS = ("yes", "no", "later")
# "Fortsätt vänta"-svar döljer kortet en tid i stället för för alltid.
SNOOZE_NO_DAYS = {"notxn": 14, "refund": 14, "subnr": 7}

KIND_ORDER = {"receipt": 0, "subdev": 1, "subnr": 2, "dup": 3, "refund": 4,
              "notxn": 5, "subpriv": 6, "sub": 6, "trip": 7}


class QueueError(TripEditError):
    """Fel som visas för Mikko (engelska)."""


def _kind(item_id: str) -> str:
    return (item_id or "").split(":", 1)[0]


def _active_decisions(db: Session) -> list[QueueDecision]:
    return (db.query(QueueDecision)
            .filter(QueueDecision.undone_at.is_(None))
            .order_by(QueueDecision.id).all())


def rejected_receipt_pairs(db: Session) -> set[tuple[str, str]]:
    """(bill_line_id, message_id) som Mikko sagt nej till."""
    out = set()
    for d in (db.query(QueueDecision)
              .filter(QueueDecision.undone_at.is_(None))
              .filter(QueueDecision.kind == "receipt")
              .filter(QueueDecision.decision == "no").all()):
        parts = d.item_id.split(":", 2)
        if len(parts) == 3:
            out.add((parts[1], parts[2]))
    return out


def _hidden_and_later(db: Session) -> tuple[set[str], dict[str, datetime]]:
    now = datetime.utcnow()
    hidden: set[str] = set()
    later: dict[str, datetime] = {}
    last: dict[str, QueueDecision] = {}
    for d in _active_decisions(db):
        last[d.item_id] = d
    for item_id, d in last.items():
        kind = _kind(item_id)
        if d.decision == "later":
            later[item_id] = d.created_at or now
        elif kind == "trip":
            continue  # resor följer sin status
        elif d.decision == "no" and kind in SNOOZE_NO_DAYS:
            if (d.created_at or now) > now - timedelta(days=SNOOZE_NO_DAYS[kind]):
                hidden.add(item_id)
        else:
            hidden.add(item_id)
    return hidden, later


# ---------- kortbyggare ----------


def _money(amount, currency) -> str:
    if amount is None:
        return "—"
    return f"{float(amount):.2f} {currency or ''}".strip()


_GROUND_CATEGORIES = ("Taxi", "Tåg", "Kollektivtrafik")


def _ground_km(m: ProcessedMessage) -> dict | None:
    """C53 — tåg/buss/taxi-kvitto: {'mode', 'km'} (km None = saknas), annars None."""
    from app.services.trip_legs import parse_km, receipt_mode

    if m.category not in _GROUND_CATEGORIES and m.category not in (None, "", "Annat"):
        return None
    text = " ".join(str(v) for v in (m.vendor, m.sender, m.subject, m.summary,
                                     m.ai_description_en, m.file_name) if v)
    mode = receipt_mode(m.category, text)
    if mode is None:
        return None
    return {"mode": mode, "km": parse_km(text)}


def _receipt_items(db: Session) -> list[dict]:
    from app.services.card_line_coupling import suggest_receipts
    from app.services.trip_completeness import _today, _trip_for_date

    lines = (db.query(CardLine).filter(CardLine.status == "missing")
             .filter(CardLine.amount > 0).all())
    if not lines:
        return []
    sugg = suggest_receipts(db, lines, exclude_pairs=rejected_receipt_pairs(db))
    trips = db.query(CalendarTrip).filter(CalendarTrip.status != "rejected").all()
    by_id = {c.bill_line_id: c for c in lines}
    out = []
    today = _today()
    for bid, s in sugg.items():
        if not s:
            continue
        c = by_id[bid]
        m = (db.query(ProcessedMessage)
             .filter(ProcessedMessage.message_id == s["message_id"]).first())
        if m is None:
            continue
        trip = _trip_for_date(trips, c.txn_date)
        amount_missing = m.amount is None
        # C50 — kontoförslag + beskrivning att klistra in (skrivs inte via API).
        from app.services.card_line_coupling import (
            _account_suggestion, _bezala_fill, _description_to_paste,
        )

        account = _account_suggestion(m, c)
        fill = _bezala_fill(m, c, account)
        warning = ("Amount missing - open the receipt before coupling"
                   if amount_missing else None)
        if account.get("missing"):
            warning = f"{warning}; Account missing" if warning else "Account missing"
        # C53 Del 0b — samma kvitto som återbetalningen: bara förslag, Mikko
        # laddar upp PDF:en själv (boten laddar inte upp igen).
        shared = s.get("match") == "refund_shared_receipt"
        if shared:
            note = f"{s.get('note_sv')} - ladda upp PDF:en själv från Drive"
            warning = f"{note}; {warning}" if warning else note
        # C53 — km för tåg/buss/taxi: "xx km" sist i beskrivningen när känt,
        # annars flaggan km_missing (gissas aldrig).
        ground = _ground_km(m)
        description = fill["description"] or _description_to_paste(m, c)
        if ground is not None and ground["km"] is not None:
            from app.services.trip_legs import append_km

            description = append_km(description, ground["km"])
            if fill.get("description"):
                fill = {**fill, "description": description}
        elif ground is not None:
            warning = f"{warning}; km missing" if warning else "km missing"
        out.append({
            "id": f"receipt:{bid}:{m.message_id}",
            "kind": "receipt",
            "title": f"{m.vendor or 'Receipt'} ↔ {c.vendor or c.description or 'card line'}",
            "subtitle": (f"Amount missing - open the receipt before coupling "
                         f"(card {_money(c.amount, c.currency)})" if amount_missing else
                         f"{_money(m.amount, m.currency)} ↔ {_money(c.amount, c.currency)}"),
            "amount_missing": amount_missing,
            "warning": warning,
            "account": account,
            "account_missing": bool(account.get("missing")),
            "description_to_paste": description,
            "shared_with_refund": ({"note": s.get("note"), "note_sv": s.get("note_sv"),
                                    "also_coupled_to": s.get("also_coupled_to"),
                                    "drive_link": s.get("drive_link"), "how": s.get("how")}
                                   if shared else None),
            "transport_mode": ground["mode"] if ground else None,
            "km": ground["km"] if ground else None,
            "flags": ["km_missing"] if ground is not None and ground["km"] is None else [],
            # C51 — konto → moms → kostnadsställe → beskrivning med kopieringsknapp.
            "bezala_fill": fill,
            "yes_label": "Couple",
            "no_label": "Not this pair",
            "date": c.txn_date.isoformat() if c.txn_date else None,
            "receipt": {
                "id": m.id, "message_id": m.message_id, "vendor": m.vendor,
                "amount": m.amount, "currency": m.currency, "date": m.receipt_date,
                "file_name": m.file_name, "drive_file_id": m.drive_file_id,
                "drive_link": m.drive_link, "source": m.upload_source,
            },
            "card_line": {
                "bill_line_id": bid, "description": c.description, "vendor": c.vendor,
                "amount": c.amount, "currency": c.currency,
                "date": c.txn_date.isoformat() if c.txn_date else None,
                "age_days": (today - c.txn_date).days if c.txn_date else None,
            },
            "score": s.get("score"),
            "conversion": s.get("conversion"),
            "trip": ({"id": trip.id, "destination": trip.destination,
                      "dates": f"{trip.start_date} – {trip.end_date}"} if trip else None),
        })
    return out


def _trip_completeness_text(c: dict) -> str:
    """'3/4 found, missing: hotel' ur checklistans completeness."""
    kinds = c.get("missing_kinds") or []
    found = c.get("found")
    total = c.get("total")
    head = f"{found}/{total} found" if total else "nothing expected"
    if kinds:
        names = []
        for k in kinds:
            base = k.split(" ")[0]
            if base not in names:
                names.append(base)
        head += ", missing: " + ", ".join(names)
    if c.get("card_lines_without_receipt"):
        head += f", {c['card_lines_without_receipt']} card line(s) without receipt"
    return head


def _trip_items(db: Session) -> list[dict]:
    from app.models import CalendarTripDay
    from app.services.calendar_trip_edit import summary
    from app.services.trip_completeness import _today, checklists_for

    today = _today()
    rows = (db.query(CalendarTrip)
            .filter(CalendarTrip.status.in_(("suggested", "edited")))
            .filter(CalendarTrip.start_date <= today)
            .order_by(CalendarTrip.start_date).all())
    try:
        lists = checklists_for(db, rows)
    except Exception:  # noqa: BLE001 — checklistan får aldrig fälla kön
        db.rollback()
        logger.exception("[c48-queue] checklistor för resekort misslyckades")
        lists = {}
    out = []
    for t in rows:
        s = summary(t)
        cl = lists.get(t.id) or {}
        comp = dict(cl.get("completeness") or {})
        items = [i for i in cl.get("items") or []
                 if i.get("status") in ("found", "missing")]
        if items:
            comp["total"] = len(items)
            comp["found"] = sum(1 for i in items if i.get("status") == "found")
        meals = sum(d.free_meals or 0 for d in db.query(CalendarTripDay)
                    .filter(CalendarTripDay.trip_id == t.id).all())
        text = _trip_completeness_text(comp) if comp else None
        out.append({
            "id": f"trip:{t.id}",
            "kind": "trip",
            "title": f"Trip {t.destination or '?'}",
            "subtitle": (f"{t.start_date} – {t.end_date}"
                         + (f" - {text}" if text else "")
                         + (f" - {meals} free meal(s)" if meals else "")),
            "yes_label": "Approve trip",
            "no_label": "Not a business trip",
            "date": t.start_date.isoformat(),
            "trip": s,
            "completeness": text,
            "completeness_detail": comp or None,
            "free_meals": meals,
        })
    return out


def _subscription_items(db: Session) -> list[dict]:
    from app.services.subscriptions import ACCOUNT_MISSING, serialize

    out = []
    for sub in (db.query(Subscription)
                .filter(Subscription.review_status == "suggested").all()):
        s = serialize(db, sub)
        amount = (f"about {_money(sub.expected_amount, sub.currency)} / month"
                  if sub.expected_amount else "amount from next charge")
        if sub.kind == "likely_private":
            out.append({
                "id": f"subpriv:{sub.id}",
                "kind": "subpriv",
                "title": f"{sub.name}: private subscription?",
                "subtitle": f"{amount} - {s['card_lines']} charge(s) on the company card",
                "yes_label": "Private - mark as private expense",
                "no_label": "Business subscription",
                "subscription": s,
            })
            continue
        blocked = sub.account is None
        out.append({
            "id": f"sub:{sub.id}",
            "kind": "sub",
            "title": f"Subscription: {sub.name}?",
            "subtitle": amount + (f" - {ACCOUNT_MISSING}" if blocked else ""),
            "yes_label": "Yes, auto-handle it",
            "no_label": "Not a subscription",
            "warning": ACCOUNT_MISSING if blocked else None,
            "blocked": blocked,
            "subscription": s,
        })
    return out


def _subscription_flag_items(db: Session) -> list[dict]:
    from app.services.subscriptions import line_flags, serialize

    out = []
    for f in line_flags(db):
        c, sub = f["line"], f["sub"]
        line = {"bill_line_id": c.bill_line_id, "description": c.description,
                "amount": c.amount, "currency": c.currency,
                "date": c.txn_date.isoformat() if c.txn_date else None}
        if f["kind"] == "subdev":
            out.append({
                "id": f"subdev:{c.bill_line_id}", "kind": "subdev",
                "title": f"{sub.name}: amount changed",
                "subtitle": (f"{_money(c.amount, c.currency)} (expected "
                             f"{_money(sub.expected_amount, sub.currency)})"),
                "yes_label": "OK, new price",
                "no_label": "Handle manually",
                "date": line["date"], "card_line": line,
                "subscription": serialize(db, sub),
            })
        else:
            out.append({
                "id": f"subnr:{c.bill_line_id}", "kind": "subnr",
                "title": f"{sub.name}: no receipt yet",
                "subtitle": f"{_money(c.amount, c.currency)} charged {line['date']}",
                "yes_label": "No receipt will come",
                "no_label": "Keep waiting",
                "date": line["date"], "card_line": line,
                "subscription": serialize(db, sub),
            })
    return out


def _duplicate_items(db: Session) -> list[dict]:
    from app.services.refunds import duplicate_charges

    out = []
    for d in duplicate_charges(db):
        out.append({
            "id": d["id"], "kind": "dup",
            "title": f"{d['vendor']}: charged {d['card_lines']} times",
            "subtitle": (f"{_money(d['amount'], d['currency'])} on {d['date']} - "
                         f"{d['card_lines']} charges, {d['receipts']} receipts"),
            "yes_label": "All are real purchases",
            "no_label": "Double charge - expect refund",
            "date": d["date"], "duplicate": d,
        })
    return out


def _no_transaction_items(db: Session) -> list[dict]:
    from app.services.waiting_receipts import list_without_transaction

    out = []
    for r in list_without_transaction(db):
        out.append({
            "id": f"notxn:{r['message_id']}", "kind": "notxn",
            "title": f"{r['vendor'] or 'Receipt'}: no card payment found",
            "subtitle": (f"{_money(r['amount'], r['currency'])}, {r['date'] or '?'} - "
                         f"waiting {r['waiting_days']} days"),
            "yes_label": "Private / other card",
            "no_label": "Keep waiting",
            "date": r["date"], "receipt": r,
        })
    return out


def _refund_items(db: Session) -> list[dict]:
    from app.services.refunds import overdue_refunds

    return [{
        "id": f"refund:{r['id']}", "kind": "refund",
        "title": f"{r['vendor'] or 'Refund'}: refund not seen",
        "subtitle": f"{_money(r['amount'], r['currency'])} - expected for {r['age_days']} days",
        "yes_label": "Settled - close it",
        "no_label": "Keep waiting",
        "date": r["date"], "refund": r,
    } for r in overdue_refunds(db)]


BUILDERS = (_receipt_items, _subscription_flag_items, _duplicate_items, _refund_items,
            _no_transaction_items, _subscription_items, _trip_items)


def list_pending(db: Session, limit: int | None = None, offset: int = 0) -> dict:
    hidden, later = _hidden_and_later(db)
    items: list[dict] = []
    errors = []
    for build in BUILDERS:
        try:
            items.extend(build(db))
        except Exception as exc:  # noqa: BLE001 — en del får aldrig fälla kön
            db.rollback()
            logger.exception("[c47-queue] %s misslyckades", build.__name__)
            errors.append(f"{build.__name__}: {exc}")
    items = [i for i in items if i["id"] not in hidden]
    for i in items:
        i["later"] = i["id"] in later
    items.sort(key=lambda i: (
        i["later"], later.get(i["id"], datetime.min),
        KIND_ORDER.get(i["kind"], 9), i.get("date") or "",
    ))
    counts: dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    offset = max(0, int(offset or 0))
    page = items[offset:offset + limit] if limit else items[offset:]
    out = {"count": len(items), "total": len(items), "offset": offset,
           "returned": len(page), "counts": counts, "items": page}
    if offset + len(page) < len(items):
        out["next_offset"] = offset + len(page)
    if errors:
        out["errors"] = errors
    return out


# ---------- beslut ----------


def _record(db: Session, item_id: str, decision: str, source: str,
            undo: dict | None = None, result: dict | None = None) -> QueueDecision:
    d = QueueDecision(item_id=item_id, kind=_kind(item_id), decision=decision,
                      source=source, undo_data=undo, result=result)
    db.add(d)
    db.commit()
    return d


def _sub(db: Session, sub_id: str) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.id == int(sub_id)).first()
    if sub is None:
        raise QueueError(f"Subscription {sub_id} not found.")
    return sub


def _card_line(db: Session, bid: str) -> CardLine:
    c = db.query(CardLine).filter(CardLine.bill_line_id == bid).first()
    if c is None:
        raise QueueError(f"Card line {bid} not found.")
    return c


def _receipt(db: Session, mid: str) -> ProcessedMessage:
    m = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == mid).first()
    if m is None:
        raise QueueError(f"Receipt {mid} not found.")
    return m


def decide(db: Session, item_id: str, decision: str, *, source: str = "ui",
           couple=None) -> dict:
    """Utför beslutet. couple(db, bill_line_id, message_id) = koppling
    (default card_line_coupling.couple_receipt → Bezala-utkast)."""
    decision = (decision or "").strip().lower()
    if decision not in DECISIONS:
        raise QueueError("decision must be 'yes', 'no' or 'later'.")
    item_id = (item_id or "").strip()
    kind = _kind(item_id)
    parts = item_id.split(":")
    if kind not in KIND_ORDER:
        raise QueueError(f"Unknown item '{item_id}'. Use list_pending_decisions.")
    if decision == "later":
        _record(db, item_id, "later", source)
        return {"item_id": item_id, "decision": "later", "result": "skipped for now"}

    undo: dict = {}
    result: dict = {}
    if kind == "receipt":
        if len(parts) < 3:
            raise QueueError("Bad receipt item id.")
        bid, mid = parts[1], item_id.split(":", 2)[2]
        if decision == "yes":
            if couple is None:
                from app.services.card_line_coupling import couple_receipt as couple
            result = couple(db, bid, mid)
            undo = {"message_id": mid, "bill_line_id": bid}
        else:
            result = {"rejected_pair": [bid, mid]}
    elif kind == "trip":
        from app.services import calendar_trip_edit as edit

        tid = int(parts[1])
        if decision == "yes":
            edit.approve_trip(db, tid)
            result = {"trip_id": tid, "status": "approved"}
        else:
            edit.reject_trip(db, tid, "Not a business trip (Travel Tinder)")
            result = {"trip_id": tid, "status": "rejected"}
        undo = {"trip_id": tid}
    elif kind == "sub":
        from app.services.subscriptions import confirm, serialize

        sub = _sub(db, parts[1])
        if decision == "yes":
            undo = {"before": confirm(db, sub)}
        else:
            undo = {"before": {"active": sub.active, "review_status": sub.review_status}}
            sub.active = False
            sub.review_status = "dismissed"
            db.commit()
        result = serialize(db, sub)
    elif kind == "subpriv":
        from app.services.subscriptions import mark_private, serialize

        sub = _sub(db, parts[1])
        if decision == "yes":
            res = mark_private(db, sub)
            undo = {"before": res["before"], "private": True}
            result = {**serialize(db, sub), "private_card_lines": res["lines"]}
        else:
            undo = {"before": {"kind": sub.kind}}
            sub.kind = "business"
            db.commit()
            result = serialize(db, sub)
    elif kind == "subdev":
        line = _card_line(db, parts[1])
        if decision == "yes":
            from app.services.subscriptions import active_for_line

            sub = active_for_line(db.query(Subscription).all(), line)
            if sub is not None:
                undo = {"subscription_id": sub.id, "expected_amount": sub.expected_amount}
                sub.expected_amount = float(line.amount)
                db.commit()
                result = {"subscription": sub.name, "expected_amount": sub.expected_amount}
        else:
            result = {"note": "flag dismissed - handle the card line manually"}
    elif kind == "subnr":
        line = _card_line(db, parts[1])
        if decision == "yes":
            undo = {"status": line.status}
            line.status = "receipt_not_available"
            line.set_by = "user"
            db.commit()
            result = {"bill_line_id": line.bill_line_id, "status": line.status}
        else:
            result = {"note": "keep waiting for the receipt"}
    elif kind == "dup":
        if decision == "no":
            from app.services.refunds import duplicate_charges, expect_refund

            dup = next((d for d in duplicate_charges(db) if d["id"] == item_id), None)
            if dup is None:
                raise QueueError("Duplicate charge no longer found.")
            bid = dup["bill_line_ids"][-1]
            line = _card_line(db, bid)
            r = expect_refund(db, bid, "Double charge - refund expected",
                              vendor=dup["vendor"], amount=dup["amount"],
                              currency=dup["currency"], txn_date=line.txn_date)
            undo = {"refund_id": r["id"]}
            result = {"expected_refund": r}
        else:
            result = {"note": "all charges are real purchases"}
    elif kind == "notxn":
        m = _receipt(db, item_id.split(":", 1)[1])
        undo = {"waiting_status": m.waiting_status,
                "waiting_since": m.waiting_since.isoformat() if m.waiting_since else None}
        if decision == "yes":
            m.waiting_status = "private"
        else:
            m.waiting_status = "waiting_for_transaction"
            m.waiting_since = datetime.utcnow()
        db.commit()
        result = {"message_id": m.message_id, "waiting_status": m.waiting_status}
    elif kind == "refund":
        r = db.query(ExpectedRefund).filter(ExpectedRefund.id == int(parts[1])).first()
        if r is None:
            raise QueueError(f"Expected refund {parts[1]} not found.")
        if decision == "yes":
            undo = {"status": r.status}
            r.status = "closed"
            db.commit()
        result = {"refund_id": r.id, "status": r.status}
    _record(db, item_id, decision, source, undo=undo or None,
            result=_jsonable(result))
    logger.info("[c47-queue] %s → %s (%s)", item_id, decision, source)
    return {"item_id": item_id, "decision": decision, "result": result}


def _jsonable(value):
    import json

    try:
        json.dumps(value, default=str)
        return json.loads(json.dumps(value, default=str))
    except Exception:  # noqa: BLE001
        return None


def unmatch_receipt(db: Session, message_id: str) -> dict:
    """Koppla loss: radera Bezala-utkastet (samma väg som desktopens
    Frikoppla), lägg tillbaka kortraden som 'missing' och kvittot i kön."""
    from app.main import unmatch_receipt as _unmatch

    from fastapi import HTTPException

    m = _receipt(db, message_id)
    old_tx = m.bezala_transaction_id
    try:
        out = _unmatch(message_id=message_id, db=db, _=None)
    except HTTPException as exc:
        detail = exc.detail.get("error") if isinstance(exc.detail, dict) else exc.detail
        raise QueueError(f"Could not uncouple: {detail}") from exc
    lines = []
    if old_tx:
        lines = db.query(CardLine).filter(CardLine.bezala_transaction_id == old_tx).all()
    lines += [c for c in db.query(CardLine).filter(CardLine.note.like(f"%{message_id}"))
              .all() if c not in lines]
    for c in lines:
        c.status = "missing"
        c.resolved_at = None
        c.bezala_transaction_id = None
        c.note = None
    if m.waiting_status == "coupled" or m.upload_source in ("mobile", "mail_kvitto"):
        m.waiting_status = "waiting_for_transaction"
        m.waiting_since = m.waiting_since or datetime.utcnow()
    for d in (db.query(QueueDecision)
              .filter(QueueDecision.kind == "receipt")
              .filter(QueueDecision.undone_at.is_(None))
              .filter(QueueDecision.item_id.like(f"%:{message_id}")).all()):
        d.undone_at = datetime.utcnow()
    db.commit()
    return {**out, "card_lines_back_in_queue": [c.bill_line_id for c in lines]}


def undo_last(db: Session, *, source: str | None = None) -> dict:
    q = db.query(QueueDecision).filter(QueueDecision.undone_at.is_(None))
    d = q.order_by(QueueDecision.id.desc()).first()
    if d is None:
        raise QueueError("Nothing to undo.")
    undo = d.undo_data or {}
    kind = d.kind
    if d.decision != "later":
        if kind == "receipt" and d.decision == "yes" and undo.get("message_id"):
            unmatch_receipt(db, undo["message_id"])
        elif kind == "trip":
            from app.services import calendar_trip_edit as edit

            if d.decision == "yes":
                edit.unapprove_trip(db, undo["trip_id"])
            else:
                edit.unreject_trip(db, undo["trip_id"])
        elif kind == "sub" and undo.get("before"):
            from app.services.subscriptions import restore

            restore(db, _sub(db, d.item_id.split(":")[1]), undo["before"])
        elif kind == "subpriv" and undo.get("before"):
            sub = _sub(db, d.item_id.split(":")[1])
            if undo.get("private"):
                from app.services.subscriptions import undo_private

                undo_private(db, sub, undo["before"])
            else:
                for k, v in undo["before"].items():
                    setattr(sub, k, v)
        elif kind == "subdev" and undo.get("subscription_id"):
            sub = _sub(db, str(undo["subscription_id"]))
            sub.expected_amount = undo.get("expected_amount")
        elif kind == "subnr" and undo.get("status"):
            _card_line(db, d.item_id.split(":")[1]).status = undo["status"]
        elif kind == "dup" and undo.get("refund_id"):
            r = db.query(ExpectedRefund).filter(ExpectedRefund.id == undo["refund_id"]).first()
            if r is not None:
                db.delete(r)
        elif kind == "notxn" and undo:
            m = _receipt(db, d.item_id.split(":", 1)[1])
            m.waiting_status = undo.get("waiting_status")
            ws = undo.get("waiting_since")
            m.waiting_since = datetime.fromisoformat(ws) if ws else None
        elif kind == "refund" and undo.get("status"):
            r = db.query(ExpectedRefund).filter(
                ExpectedRefund.id == int(d.item_id.split(":")[1])).first()
            if r is not None:
                r.status = undo["status"]
    d.undone_at = datetime.utcnow()
    db.commit()
    logger.info("[c47-queue] ångrat %s (%s)", d.item_id, d.decision)
    return {"undone": d.item_id, "decision": d.decision}


def last_decision(db: Session) -> dict | None:
    d = (db.query(QueueDecision).filter(QueueDecision.undone_at.is_(None))
         .order_by(QueueDecision.id.desc()).first())
    if d is None:
        return None
    return {"item_id": d.item_id, "decision": d.decision,
            "at": d.created_at.isoformat() if d.created_at else None}

