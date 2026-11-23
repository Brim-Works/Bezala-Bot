"""C56 tillägg - kvitton på rätt kalenderresa, och samma biljett räknas en gång.

Förut fick en kalenderresa ALLA kvitton från varje AI-förslag vars datum
överlappade resan. Ett förslag som täckte flera resor (t.ex. 'Apr-Apr' med 11
kvitton) gav då samma summa på tre resor, och Finnairs bokningsbekräftelse +
e-biljettkvitto räknades dubbelt.

Nu (bara visning och summor - inget kvitto, inget i Bezala och inga resor
ändras):
  1. Varje kvitto (unikt message_id) från AI-förslagen hamnar på exakt EN
     kalenderresa:
       a. resan vars checklista eller delsträckor redan pekar på kvittot
          (Mikkos egna markeringar först, sedan botens), annars
       b. resan vars datum ±1 dag täcker kvittodatumet. Finns flera: datum
          inom resan slår marginaldagen, sedan stad i kvittotexten. Är det
          fortfarande lika gissar vi inte - kvittot blir 'utan resa'.
  2. Kvitto utan leverantör och belopp: 'saknar uppgifter', räknas inte.
  3. Dubbletter: ett kvitto med duplicate_of (C44: samma belopp + datum +
     bokningsnummer/e-biljettnummer) räknas inte när originalet också finns.
     Belopp under 1 EUR (delbetalningar, t.ex. 2 × 0,02) räknas alltid.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import (
    CalendarTrip,
    ProcessedMessage,
    Trip,
    TripExpectation,
    TripLeg,
    TripMessage,
)
from app.services.c56_trip_suggestions import _calendar_keys, _city_keys

MARGIN = timedelta(days=1)
# Hemmaänden står i nästan varje flygkvitto - säger inget om resmålet.
_HOME = {"helsinki", "vantaa"}
TINY = 1.0


def _day(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _pins(db: Session) -> dict[str, int]:
    """message_id -> kalenderresa som checklistan/delsträckorna redan pekar på."""
    pins: dict[str, int] = {}
    rows = (db.query(TripExpectation)
            .filter(TripExpectation.message_id.isnot(None))
            .filter(TripExpectation.status == "found").all())
    # Mikkos egna markeringar först.
    for r in sorted(rows, key=lambda r: (r.set_by != "user", r.trip_id)):
        pins.setdefault(r.message_id, r.trip_id)
    for leg in (db.query(TripLeg).filter(TripLeg.receipt_message_id.isnot(None))
                .filter(TripLeg.excluded.is_(False)).order_by(TripLeg.trip_id).all()):
        pins.setdefault(leg.receipt_message_id, leg.trip_id)
    return pins


def _receipt_dict(msg: ProcessedMessage, suggestion_id: int) -> dict:
    return {
        "message_id": msg.message_id,
        "vendor": msg.vendor,
        "amount": msg.amount,
        "currency": msg.currency,
        "receipt_date": msg.receipt_date,
        "trip_id": suggestion_id,
        "booking_ref": msg.booking_ref,
        "duplicate_of": None,
        "counted": True,
    }


def allocate(db: Session, trips: list[CalendarTrip] | None = None) -> dict:
    """{'by_trip': {calendar_trip_id: [kvitto]}, 'unassigned': [...],
    'missing_info': [...], 'cancelled_by_trip': {id: [bokning]},
    'cancelled_without_trip': [bokning]}. Kvitton med counted=False är
    dubbletter; avbokade bokningar (C57) finns bara i cancelled_*."""
    if trips is None:
        trips = db.query(CalendarTrip).all()
    rows = (
        db.query(TripMessage, ProcessedMessage)
        .join(Trip, Trip.id == TripMessage.trip_id)
        .join(ProcessedMessage, ProcessedMessage.message_id == TripMessage.message_id)
        .filter(TripMessage.removed_at.is_(None),
                ProcessedMessage.deleted_at.is_(None),
                Trip.status.in_(("suggested", "active")))
        .order_by(TripMessage.trip_id, ProcessedMessage.id)
        .all()
    )
    msgs: dict[str, tuple[ProcessedMessage, int]] = {}
    for tm, msg in rows:
        msgs.setdefault(msg.message_id, (msg, tm.trip_id))

    by_trip: dict[int, list[dict]] = {t.id: [] for t in trips}
    unassigned: list[dict] = []
    missing_info: list[dict] = []
    keys = {t.id: _calendar_keys(db, t) for t in trips}
    trip_ids = set(by_trip)
    pins = {m: t for m, t in _pins(db).items() if t in trip_ids}
    placed: dict[str, int] = {}

    def place(msg: ProcessedMessage, suggestion_id: int) -> dict:
        r = _receipt_dict(msg, suggestion_id)
        if msg.message_id in pins:
            r["placed_by"] = "checklist"
            return {**r, "_trip": pins[msg.message_id]}
        d = _day(msg.receipt_date)
        if d is None:
            return {**r, "reason": "no_date"}
        cands = [t for t in trips if t.start_date - MARGIN <= d <= t.end_date + MARGIN]
        if not cands:
            return {**r, "reason": "no_trip"}
        text = " ".join(filter(None, [msg.subject, msg.file_name, msg.summary, msg.vendor]))
        rkeys = _city_keys(text) - _HOME

        def score(t: CalendarTrip) -> tuple[int, int]:
            return (int(t.start_date <= d <= t.end_date), int(bool(rkeys & keys[t.id])))

        ranked = sorted(cands, key=score, reverse=True)
        if len(ranked) > 1 and score(ranked[0]) == score(ranked[1]):
            return {**r, "reason": "ambiguous",
                    "candidates": sorted(t.id for t in ranked if score(t) == score(ranked[0]))}
        r["placed_by"] = "date"
        return {**r, "_trip": ranked[0].id}

    # C57 - avbokade och återbetalda bokningar: räknas inte, hamnar inte i
    # 'utan resa'. En hopfälld rad per bokning på resan (eller utan resa).
    from app.services.c57_cancelled import cancelled_messages

    cancelled = cancelled_messages(db)
    cancelled_by_trip: dict[int, list[dict]] = {}
    cancelled_without_trip: list[dict] = []
    bookings: dict[str, dict] = {}
    for msg, sid in msgs.values():
        info = cancelled.get(msg.message_id)
        if info is None:
            continue
        b = bookings.setdefault(info["booking"], {
            k: info[k] for k in ("booking", "vendor", "booking_ref", "amount", "currency",
                                 "purchase_date", "refund_date", "reason", "label_sv",
                                 "label_en")
        } | {"receipts": [], "counted": False, "_msgs": []})
        b["_msgs"].append((msg, sid))
        b["receipts"].append(_receipt_dict(msg, sid) | {"counted": False, "cancelled": True})
    for b in bookings.values():
        # Placera bokningen efter köpkvittot (originalet, inte dubbletten).
        msg, sid = sorted(b.pop("_msgs"), key=lambda mt: (bool(mt[0].duplicate_of), mt[0].id))[0]
        res = place(msg, sid)
        tid = res.get("_trip")
        b["receipts"].sort(key=lambda r: r["message_id"])
        if tid is None:
            b["reason_no_trip"] = res.get("reason")
            if res.get("candidates"):
                b["candidates"] = res["candidates"]
            cancelled_without_trip.append(b)
        else:
            cancelled_by_trip.setdefault(tid, []).append(b)

    # Original först, dubbletter sist (de följer originalets resa).
    order = sorted((mt for mt in msgs.values() if mt[0].message_id not in cancelled),
                   key=lambda mt: (bool(mt[0].duplicate_of and mt[0].duplicate_of in msgs),
                                   mt[0].id))
    for msg, sid in order:
        if msg.vendor is None and msg.amount is None:
            missing_info.append(_receipt_dict(msg, sid) | {"counted": False})
            continue
        dup = msg.duplicate_of if msg.duplicate_of in msgs else None
        if dup and (msg.amount is None or abs(float(msg.amount)) >= TINY):
            r = _receipt_dict(msg, sid) | {"duplicate_of": dup, "counted": False}
            if dup in placed:
                by_trip[placed[dup]].append(r)
            # Originalet saknar resa: det står redan i listan 'utan resa'.
            continue
        res = place(msg, sid)
        tid = res.pop("_trip", None)
        if tid is None:
            unassigned.append(res)
        else:
            placed[msg.message_id] = tid
            by_trip[tid].append(res)

    for lst in by_trip.values():
        lst.sort(key=lambda r: (r.get("receipt_date") or "", r["message_id"]))
    unassigned.sort(key=lambda r: (r.get("receipt_date") or "", r["message_id"]))
    return {"by_trip": by_trip, "unassigned": unassigned, "missing_info": missing_info,
            "cancelled_by_trip": cancelled_by_trip,
            "cancelled_without_trip": cancelled_without_trip}


def totals(receipts: list[dict]) -> dict[str, float]:
    """Summa per valuta, bara räknade kvitton."""
    out: dict[str, float] = {}
    for r in receipts:
        if not r.get("counted", True) or r.get("amount") is None:
            continue
        cur = r.get("currency") or "EUR"
        out[cur] = round(out.get(cur, 0.0) + float(r["amount"]), 2)
    return out


def compare(db: Session) -> dict:
    """Read-only jämförelse per kalenderresa: gammal summa (alla kvitton från
    överlappande förslag, som före C56-tillägget) och ny summa."""
    from app.services.calendar_trip_store import _linked_receipts

    trips = (db.query(CalendarTrip).filter(CalendarTrip.status != "rejected")
             .order_by(CalendarTrip.start_date, CalendarTrip.id).all())
    a = allocate(db)
    rows = []
    for t in trips:
        old = [r | {"counted": True} for r in _linked_receipts(db, t.start_date, t.end_date)]
        rows.append({
            "trip_id": t.id,
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "destination": t.destination,
            "status": t.status,
            "before": totals(old),
            "after": totals(a["by_trip"].get(t.id, [])),
            "receipts_after": len([r for r in a["by_trip"].get(t.id, []) if r["counted"]]),
            "duplicates_counted_once": len([r for r in a["by_trip"].get(t.id, [])
                                            if not r["counted"]]),
            "cancelled_not_counted": [
                {k: b[k] for k in ("booking_ref", "vendor", "amount", "currency")}
                for b in a["cancelled_by_trip"].get(t.id, [])],
        })
    return {"trips": rows, "unassigned": a["unassigned"],
            "cancelled_without_trip": a["cancelled_without_trip"],
            "missing_info_count": len(a["missing_info"])}
