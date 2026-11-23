"""C58 - hotellnätter utan kvitto: Mikkos val + tips.

Mikko väljer själv per natt (inget förifylls): 'Det fanns inget hotell',
'Redan hanterat i Bezala', 'Faktureras direkt till företaget' eller
'Kvitto kommer'. Valet sparas i bottens egen tabell (trip_expectations).

Tipsen är bara läsning: kvitton i boten nära natten och utlägg i Bezala
(GET /transactions) med hotellord nära datumet. Inget kopplas, inget
skickas till Bezala eller Netvisor.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import CalendarTrip, TripExpectation
from app.services.calendar_trip_edit import TripEditError

logger = logging.getLogger(__name__)

_BEZALA_CACHE: dict = {"at": 0.0, "rows": None}
_BEZALA_TTL = 600  # sekunder
_HOTEL_WORDS = ("hotel", "hotell", "hostel", "majoitus", "lodging", "accommodation",
                "scandic", "clarion", "radisson", "riverside", "terazza", "nordic choice",
                "best western", "elite", "comfort", "quality", "first hotel", "booking.com")


def _bezala_transactions(client=None) -> list[dict]:
    now = time.time()
    if client is None and _BEZALA_CACHE["rows"] is not None \
            and now - _BEZALA_CACHE["at"] < _BEZALA_TTL:
        return _BEZALA_CACHE["rows"]
    close = False
    if client is None:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
        close = True
    try:
        rows = [t for t in (client.list_transactions() or []) if isinstance(t, dict)]
    finally:
        if close:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    if close:
        _BEZALA_CACHE.update(at=now, rows=rows)
    return rows


def _tx_date(t: dict) -> date | None:
    for k in ("date", "transaction_date", "purchase_date", "created_at"):
        raw = str(t.get(k) or "")[:10]
        try:
            return date.fromisoformat(raw)
        except ValueError:
            continue
    return None


def _tx_amount(t: dict):
    for k in ("original_amount", "billed_amount", "amount", "sum", "total"):
        if t.get(k) is not None:
            return t.get(k)
    return None


def bezala_hotel_hits(nights: list[date], client=None) -> dict[str, list[dict]]:
    """Utlägg i Bezala med hotellord inom natten -1 .. +3 dagar. Bara läsning."""
    from app.services.bezala_write_guard import _unwrap

    out: dict[str, list[dict]] = {n.isoformat(): [] for n in nights}
    for raw in _bezala_transactions(client):
        t = _unwrap(raw)
        d = _tx_date(t)
        if d is None:
            continue
        text = json.dumps(t, default=str, ensure_ascii=False).lower()
        if not any(w in text for w in _HOTEL_WORDS):
            continue
        for n in nights:
            if n - timedelta(days=1) <= d <= n + timedelta(days=3):
                out[n.isoformat()].append({
                    "source": "bezala",
                    "bezala_transaction_id": str(t.get("id")) if t.get("id") is not None else None,
                    "date": d.isoformat(),
                    "amount": _tx_amount(t),
                    "currency": t.get("original_currency") or t.get("currency"),
                    "text": str(t.get("description") or t.get("comment") or t.get("merchant")
                                or "")[:160],
                    "state": t.get("state"),
                })
    return out


def bot_hotel_hits(db: Session, trip: CalendarTrip, nights: list[date],
                   receipts=None) -> dict[str, list[dict]]:
    """Hotellkvitton i boten nära natten (även de som redan täcker en annan natt)."""
    from app.services.trip_completeness import load_receipts

    receipts = receipts if receipts is not None else load_receipts(db)
    out: dict[str, list[dict]] = {n.isoformat(): [] for n in nights}
    for r in receipts:
        if "hotel" not in r.kinds or r.day is None:
            continue
        for n in nights:
            if n - timedelta(days=2) <= r.day <= n + timedelta(days=3):
                out[n.isoformat()].append({
                    "source": "bot", "message_id": r.message_id, "vendor": r.vendor,
                    "date": r.day.isoformat(), "amount": r.amount, "currency": r.currency,
                    "bezala_transaction_id": r.bill_line_id,
                })
    return out


def hotel_night_tips(db: Session, trip_id: int, *, use_bezala: bool = True,
                     client=None) -> dict:
    """Tips för resans hotellnätter utan kvitto. Väljer aldrig åt Mikko."""
    trip = db.get(CalendarTrip, trip_id)
    if trip is None:
        raise TripEditError(f"Trip {trip_id} not found")
    rows = (db.query(TripExpectation)
            .filter(TripExpectation.trip_id == trip.id, TripExpectation.kind == "hotel")
            .all())
    open_nights = []
    for r in rows:
        if not r.slot or len(r.slot) != 10:
            continue
        if r.status == "missing" or r.resolution:
            open_nights.append(date.fromisoformat(r.slot))
    open_nights.sort()
    bot = bot_hotel_hits(db, trip, open_nights)
    bezala: dict[str, list[dict]] = {n.isoformat(): [] for n in open_nights}
    bezala_error = None
    if use_bezala and open_nights:
        try:
            bezala = bezala_hotel_hits(open_nights, client)
        except Exception as exc:  # noqa: BLE001 - tipsen får aldrig fälla vyn
            logger.warning("C58: Bezala-tips misslyckades: %s", exc)
            bezala_error = str(exc)[:200]
    return {
        "trip_id": trip.id,
        "nights": [{"night": n.isoformat(), "tips": bot[n.isoformat()] + bezala[n.isoformat()]}
                   for n in open_nights],
        "bezala_error": bezala_error,
        "note": "Tips only - nothing is coupled or sent.",
    }
