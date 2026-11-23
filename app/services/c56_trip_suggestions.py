"""C56 - städning av AI-förslagen (kvittoresor, tabellen `trips`) på /trips.

Tre regler (Mikko, 2026-11-23):
  1. Ett förslag som täcks av en befintlig kalenderresa (överlappande datum
     + samma stad) visas inte som förslag, bara som "Redan täckt av resa NNN".
  2. Dubbletter bland förslagen (överlappande datum + samma stad) slås ihop
     till det med flest kvitton; övrigas kvitton flyttas dit.
  3. Förslag före 2026-01-01 döljs.

Regel 1 och 3 är ren visning - databasen ändras inte. Regel 2 ändrar
databasen och körs därför bara med apply=True (dry-run först, sedan på
Mikkos ja). Sammanslagna förslag får status 'merged' (ingen rad raderas,
kvitton flyttas med added_by='c56_merge'). Inget skrivs till Bezala eller
Netvisor.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from sqlalchemy.orm import Session

from app.models import CalendarTrip, CalendarTripDay, Trip, TripMessage
from app.services.trip_english import _fold, city_to_english

CUTOFF = date(2026, 1, 1)
TINY_AMOUNT = 1.0

# En flygning till CPH betyder oftast arbete i Skåne/Småland - samma resa.
_OERESUND = {"copenhagen", "kastrup", "malmo", "lund", "helsingborg", "angelholm",
             "landskrona", "ljungby", "vaxjo", "bastad"}

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _city_keys(*texts: str | None) -> set[str]:
    """Ord i titel/destination → kanoniska stadsnycklar (vikta, engelska).
    Månadsnamn m.m. följer med men matchar aldrig en kalenderresas städer."""
    out: set[str] = set()
    for text in texts:
        for word in _WORD_RE.findall(text or ""):
            if len(word) < 3:
                continue
            key = _fold(city_to_english(word) or word)
            out.add(key)
            if key in _OERESUND:
                out.add("@oresund")
    return out


def _calendar_keys(db: Session, trip: CalendarTrip) -> set[str]:
    texts = [trip.destination]
    texts += [d.destination for d in db.query(CalendarTripDay)
              .filter(CalendarTripDay.trip_id == trip.id).all()]
    return _city_keys(*texts)


def _overlaps(a_start: date, a_end: date, b_start: date, b_end: date) -> bool:
    return a_start <= b_end and b_start <= a_end


def _receipt_warnings(messages: list[dict]) -> list[str]:
    """Kvitton som ser ut som samma bokning två gånger, eller småbelopp."""
    warnings: list[str] = []
    seen: dict[tuple, str] = {}
    for m in messages:
        amount = m.get("amount")
        if amount is not None and abs(float(amount)) < TINY_AMOUNT:
            warnings.append(f"tiny_amount:{m.get('vendor') or '?'}:{amount}")
        if amount is None:
            continue
        key = (_fold(m.get("vendor") or ""), round(float(amount), 2), m.get("currency"))
        if key in seen:
            warnings.append(f"same_amount_twice:{m.get('vendor') or '?'}:{amount}")
        else:
            seen[key] = m.get("message_id")
    return warnings


def _serialize(db: Session, trip: Trip) -> dict:
    from app.services.trip_grouper import serialize_trip
    return serialize_trip(db, trip)


def analyse(db: Session) -> dict:
    """Ren läsning: alla förslag (status 'suggested') med täckning, ålder,
    dubblettgrupper och kvittovarningar."""
    trips = (db.query(Trip).filter(Trip.status == "suggested")
             .order_by(Trip.start_date, Trip.id).all())
    calendar = db.query(CalendarTrip).order_by(CalendarTrip.start_date).all()
    cal_keys = {c.id: _calendar_keys(db, c) for c in calendar}

    rows: list[dict] = []
    for t in trips:
        s = _serialize(db, t)
        keys = _city_keys(t.destination, t.title)
        covered = None
        for c in calendar:
            if _overlaps(t.start_date, t.end_date, c.start_date, c.end_date) and (
                    keys & cal_keys[c.id]):
                covered = {"trip_id": c.id, "destination": c.destination,
                           "start_date": c.start_date.isoformat(),
                           "end_date": c.end_date.isoformat(), "status": c.status}
                # Godkänd/redigerad resa vinner över en avvisad.
                if c.status != "rejected":
                    break
        rows.append({
            "id": t.id,
            "title": t.title,
            "destination": t.destination,
            "start_date": s["start_date"],
            "end_date": s["end_date"],
            "total_amount": s["total_amount"],
            "currency": s["base_currency"],
            "receipt_count": s["message_count"],
            "ai_confidence": t.ai_confidence,
            "description": t.description,
            "messages": s["messages"],
            "old": t.end_date < CUTOFF,
            "empty": s["message_count"] == 0,
            "covered_by": covered,
            "receipt_warnings": _receipt_warnings(s["messages"]),
            "_keys": keys,
            "_start": t.start_date,
            "_end": t.end_date,
        })

    # Dubblettgrupper (union-find) bland förslag från 2026 med kvitton.
    live = [r for r in rows if not r["old"] and not r["empty"]]
    parent = {r["id"]: r["id"] for r in live}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(live):
        for b in live[i + 1:]:
            if _overlaps(a["_start"], a["_end"], b["_start"], b["_end"]) and (
                    (a["_keys"] & b["_keys"]) - _NON_CITY):
                parent[find(a["id"])] = find(b["id"])
    groups: dict[int, list[dict]] = {}
    for r in live:
        groups.setdefault(find(r["id"]), []).append(r)

    merge_plan = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keep = sorted(members, key=lambda r: (-r["receipt_count"],
                                              -(r["total_amount"] or 0), r["id"]))[0]
        others = [r for r in members if r["id"] != keep["id"]]
        keep_ids = {m["message_id"] for m in keep["messages"]}
        moved = {m["message_id"] for r in others for m in r["messages"]} - keep_ids
        merge_plan.append({"keep": keep["id"], "keep_title": keep["title"],
                           "merge": [r["id"] for r in others],
                           "merge_titles": [r["title"] for r in others],
                           "receipts_moved": len(moved)})
        keep["duplicates"] = [r["id"] for r in others]
        for r in others:
            r["duplicate_of"] = keep["id"]

    for r in rows:
        r.setdefault("duplicates", [])
        r.setdefault("duplicate_of", None)
        r["visible"] = not (r["old"] or r["empty"] or r["covered_by"] or r["duplicate_of"])
        for k in ("_keys", "_start", "_end"):
            r.pop(k, None)

    return {
        "counts": {
            "total": len(rows),
            "visible": sum(1 for r in rows if r["visible"]),
            "covered": sum(1 for r in rows if r["covered_by"] and not r["old"]),
            "old": sum(1 for r in rows if r["old"]),
            "empty": sum(1 for r in rows if r["empty"] and not r["old"]),
            "duplicates": sum(1 for r in rows if r["duplicate_of"]),
            "calendar_trips": sum(1 for c in calendar if c.status != "rejected"),
        },
        "merge_plan": merge_plan,
        "suggestions": rows,
    }


# Ord som förekommer i titlar men aldrig är städer - får inte göra två
# förslag till dubbletter ("Resa 3 nov" och "Resa 4 nov").
_NON_CITY = {"resa", "trip", "travel", "matka", "jan", "feb", "mar", "apr", "maj", "may",
             "jun", "jul", "aug", "sep", "okt", "oct", "nov", "dec", "januari", "februari",
             "mars", "april", "juni", "juli", "augusti", "september", "oktober",
             "october", "november", "december", "january", "february", "march", "june",
             "july", "august", "och", "and", "tur", "retur"}


def merge_duplicates(db: Session, apply: bool = False) -> dict:
    """Regel 2. apply=False = dry-run (default). apply=True flyttar kvitton till
    det förslag som har flest och sätter övriga till status 'merged'."""
    from app.services.trip_grouper import recalculate_trip_total

    result = analyse(db)
    plan = result["merge_plan"]
    if not apply:
        return {"applied": False, "merge_plan": plan, "counts": result["counts"]}
    now = datetime.utcnow()
    for item in plan:
        keep = db.get(Trip, item["keep"])
        have = {tm.message_id for tm in db.query(TripMessage).filter(
            TripMessage.trip_id == keep.id).all()}
        active_have = {tm.message_id for tm in db.query(TripMessage).filter(
            TripMessage.trip_id == keep.id, TripMessage.removed_at.is_(None)).all()}
        for other_id in item["merge"]:
            other = db.get(Trip, other_id)
            for tm in db.query(TripMessage).filter(
                    TripMessage.trip_id == other_id,
                    TripMessage.removed_at.is_(None)).all():
                if tm.message_id in active_have:
                    continue
                if tm.message_id in have:
                    # Tidigare borttaget från keep - återställ i stället för dubblett.
                    old = db.query(TripMessage).filter(
                        TripMessage.trip_id == keep.id,
                        TripMessage.message_id == tm.message_id).first()
                    old.removed_at = None
                else:
                    db.add(TripMessage(trip_id=keep.id, message_id=tm.message_id,
                                       added_by="c56_merge"))
                    have.add(tm.message_id)
                active_have.add(tm.message_id)
            other.status = "merged"
            other.user_decision_at = now
        db.flush()
        recalculate_trip_total(db, keep)
    db.commit()
    after = analyse(db)
    return {"applied": True, "merged": plan, "counts": after["counts"]}
