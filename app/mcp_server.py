"""C40 — MCP-server (Streamable HTTP) på /mcp för Claude-chatten.

Reseverktygen läser och ändrar BARA `calendar_trips` / `calendar_trip_days`
(C42: även trip_expectations/card_lines). C43 mark_private_expense ändrar
card_lines och skapar — bara med create_draft=True — ett Bezala-UTKAST.
Inget skickas för attest och inget går till Netvisor.

Varje anrop kräver en giltig OAuth-access-token (se app/mcp_oauth.py).
Utan token svarar /mcp 401 med `WWW-Authenticate: Bearer
resource_metadata=…` så att claude.ai startar inloggningen.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db import SessionLocal
from app.mcp_oauth import resource_metadata_url, resource_url, validate_access_token
from app.services import calendar_trip_edit as edit
from app.services import trip_completeness as completeness
from app.services.calendar_trip_edit import TripEditError

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Bezala Bot travel review. Trips are detected from Mikko's Google Calendar. "
    "Use these tools to review, correct and approve trips. Edits only change "
    "Bezala Bot's own trip list; nothing is sent to Bezala or Netvisor. "
    "Times without a timezone are Helsinki time. Everything that goes to Netvisor "
    "is in English: purpose, destination (English city name, e.g. 'Copenhagen', "
    "'Gothenburg', 'Malmö') and country (ISO code). Trip times are travel times "
    "(leaving home → back home: 100 min before a HEL departure, 60 min after a HEL "
    "landing); flight times are shown separately. A flight to CPH usually means "
    "work in Sweden (Malmö, Helsingborg, Växjö, Ljungby). Ask Mikko before approving. "
    "Completeness: get_trip_checklist shows which receipts a trip should have "
    "(flight, one hotel per night, HEL parking, optional local transport) and "
    "whether each is found; list_missing lists everything missing, including "
    "Bezala card payments without a receipt (read-only — nothing is created in Bezala). "
    "Card lines flagged 'possible_private' are parking outside any trip — ask Mikko "
    "whether it was private; if so use mark_private_expense (Bezala comment 'Private "
    "expense – to be deducted from salary.'). Nothing is ever submitted for approval."
)

mcp = FastMCP("Bezala Bot", instructions=INSTRUCTIONS)


async def _run(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Kör DB-logik i tråd med egen session. TripEditError → verktygsfel."""

    def work():
        db = SessionLocal()
        try:
            return fn(db, *args, **kwargs)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return await anyio.to_thread.run_sync(work)
    except TripEditError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("MCP-verktyg misslyckades")
        raise RuntimeError(f"Internal error: {exc}") from exc


# ---------- Verktyg ----------


@mcp.tool()
async def list_trips(
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    flagged_only: bool = False,
) -> dict:
    """List calendar trips: id, dates, destination (work city, English), country,
    purpose (English), status, flags, travel times and flight times (Helsinki),
    and completeness ('complete' | 'incomplete' + number of missing receipts).

    status: 'suggested' | 'edited' | 'approved'. date_from/date_to: YYYY-MM-DD
    (trips overlapping the range). flagged_only: only trips with flags
    ('needs_review' = pick the outbound flight, 'missing_return' = no return flight
    found, 'destination_unconfirmed' = work city unknown, ask Mikko).
    """
    trips = await _run(edit.list_trips, status=status, date_from=date_from,
                       date_to=date_to, flagged_only=flagged_only)
    return {"count": len(trips), "trips": trips}


@mcp.tool()
async def get_trip(trip_id: int) -> dict:
    """Full trip: travel times (travel_start/travel_end) and flight times
    (flight_departure/flight_arrival), every day with travel times (UTC + Helsinki),
    arrival airport, work city (destination) and where it came from
    (destination_source), country + English name, hotel, meetings, purpose,
    linked receipts, flight options and the prepared Netvisor line description."""
    return await _run(edit.get_trip, trip_id)


@mcp.tool()
async def update_trip(
    trip_id: int,
    start: str | None = None,
    end: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    purpose: str | None = None,
    selected_flight_id: str | None = None,
) -> dict:
    """Edit a trip. Sets status to 'edited' (an approved trip must be approved again).

    start/end: TRAVEL times (leaving home/office, back home), ISO datetime, e.g.
    '2026-08-25T06:15' (Helsinki) or with offset/Z. Flight times are not changed.
    destination: work city in English. country: ISO alpha-2, comma-separated for
    several ('SE,NO'). purpose: one sentence in English (sent to Netvisor).
    selected_flight_id: for 'needs_review' trips, the flight_id from flight_options
    that Mikko actually took (travel start is recalculated from it).
    Only omitted fields stay unchanged. Returns the updated trip; non-English text
    is saved but gives a 'warnings' entry.
    """
    return await _run(edit.update_trip, trip_id, start=start, end=end,
                      destination=destination, country=country, purpose=purpose,
                      selected_flight_id=selected_flight_id)


@mcp.tool()
async def update_trip_day(
    trip_id: int,
    date: str,
    start_time: str | None = None,
    end_time: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    hotel: str | None = None,
    purpose: str | None = None,
) -> dict:
    """Edit one day of a trip. date: YYYY-MM-DD. Times HH:MM Helsinki (travel times).
    destination = work city in English (e.g. 'Malmö', 'Copenhagen'); purpose in
    English. Pass an empty string to clear a field. Status → 'edited'.
    Returns the updated trip (with 'warnings' if a field is not in English)."""
    return await _run(edit.update_trip_day, trip_id, date, start_time=start_time,
                      end_time=end_time, destination=destination, country=country,
                      hotel=hotel, purpose=purpose)


@mcp.tool()
async def add_trip_day(
    trip_id: int,
    date: str,
    start_time: str | None = None,
    end_time: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    hotel: str | None = None,
    purpose: str | None = None,
) -> dict:
    """Add a day to a trip (extends the trip if outside it). date: YYYY-MM-DD.
    destination/purpose in English. Status → 'edited'. Returns the updated trip."""
    return await _run(edit.add_trip_day, trip_id, date, start_time=start_time,
                      end_time=end_time, destination=destination, country=country,
                      hotel=hotel, purpose=purpose)


@mcp.tool()
async def remove_trip_day(trip_id: int, date: str) -> dict:
    """Remove a day from a trip (e.g. Mikko was home). Removing the first/last
    day shortens the trip. Status → 'edited'. Returns the updated trip."""
    return await _run(edit.remove_trip_day, trip_id, date)


@mcp.tool()
async def approve_trip(trip_id: int) -> dict:
    """Mark a trip as approved. Only changes the status — nothing is sent anywhere."""
    return await _run(edit.approve_trip, trip_id)


@mcp.tool()
async def unapprove_trip(trip_id: int) -> dict:
    """Undo approval (back to 'edited', or 'suggested' if never edited). Status only."""
    return await _run(edit.unapprove_trip, trip_id)


@mcp.tool()
async def refresh_calendar() -> dict:
    """Re-read Google Calendar and re-run trip detection. Never overwrites
    'edited' or 'approved' trips. Suggested trips get new ids afterwards."""
    from app.services.calendar_trip_store import refresh_from_calendar

    def work(db):
        result = refresh_from_calendar(db)
        if result["status"] == "ok":
            result["trips"] = edit.list_trips(db)
            result["ungrouped"] = [
                {"date": u.get("date"), "summary": u.get("summary"), "reason": u.get("reason")}
                for u in result.get("ungrouped", [])
            ]
        return result

    return await _run(work)


# ---------- C42: fullständighetskontroll ----------


@mcp.tool()
async def get_trip_checklist(trip_id: int) -> dict:
    """Expected receipts for a trip and their status. Items: 'flight' (Finnair/
    Amadeus ticket covering the trip), 'hotel' (one per night, slot = night date;
    a day trip gets 'not_applicable'), 'parking' (HEL parking, company car),
    'local' (optional: train/bus/taxi when the arrival airport is not the work
    city). Status: 'found' (with receipt link), 'missing', 'not_applicable',
    'optional'. set_by 'user' = Mikko's own mark (never overwritten).
    card_lines = Bezala card payments during the trip that have no receipt."""
    return await _run(completeness.get_trip_checklist, trip_id)


@mcp.tool()
async def set_expectation_status(
    trip_id: int,
    kind: str,
    status: str,
    note: str | None = None,
    date: str | None = None,
) -> dict:
    """Mark a checklist item. kind: 'flight' | 'hotel' | 'parking' | 'local'.
    status: 'not_applicable' (e.g. stayed with friends, no parking), 'found'
    (Mikko confirms it is handled), or 'auto' (remove Mikko's mark).
    date: YYYY-MM-DD, only for one hotel night (omit = all nights).
    Mikko's marks survive recalculation and calendar refresh. Ask Mikko first.
    Returns the updated checklist."""
    return await _run(completeness.set_expectation_status, trip_id, kind, status,
                      note=note, date_=date)


@mcp.tool()
async def list_missing(older_than_days: int = 5) -> dict:
    """Everything missing: checklist items with status 'missing' on trips that
    ended at least `older_than_days` days ago, plus Bezala card payments
    (bill_lines) without a receipt that are at least that old — with age in days,
    the trip they fall in (trip_id) and a suggested receipt source
    ('gmail_filter' / 'link_fetch' sender, or 'unknown'). Reads Bezala
    (read-only) first; nothing is created or sent."""
    return await _run(completeness.list_missing, older_than_days)


@mcp.tool()
async def set_card_line_status(
    bill_line_id: str,
    status: str,
    note: str | None = None,
) -> dict:
    """Mark a Bezala card payment without receipt. status:
    'receipt_not_available' (no receipt exists — stop listing it as missing),
    'private_expense' (private purchase on the company card, deducted from salary —
    prefer mark_private_expense) or 'missing' (undo). Only changes Bezala Bot's own
    list; nothing goes to Bezala."""
    return await _run(completeness.set_card_line_status, bill_line_id, status, note=note)


# ---------- C43: privat utlägg ----------


@mcp.tool()
async def mark_private_expense(
    bill_line_id: str, note: str | None = None, create_draft: bool = False,
) -> dict:
    """Mark a Bezala card payment as a PRIVATE expense (paid by mistake with the
    company card). It stops counting as missing, and the Bezala comment becomes
    'Private expense – to be deducted from salary.' so payroll deducts it.
    note: optional English note. create_draft=True also creates a Bezala DRAFT for
    the row with that comment — it is never submitted for approval.
    Only on Mikko's word. Undo: set_card_line_status(bill_line_id, 'missing')."""
    from app.services import bill_line_status as bls
    from app.services.trip_completeness import _today, card_line_dict

    def work(db):
        try:
            row = bls.mark_private_expense(db, bill_line_id, note)
        except bls.BillLineStatusError as exc:
            raise TripEditError(str(exc)) from exc
        if create_draft:
            from app.services.bezala_client import BezalaClient

            bezala = BezalaClient()
            try:
                bls.create_private_expense_draft(db, bezala, bill_line_id)
            finally:
                bezala.close()
            db.refresh(row)
        return card_line_dict(row, _today())

    return await _run(work)


# ---------- ASGI: auth + Streamable HTTP ----------

_manager: StreamableHTTPSessionManager | None = None


@contextlib.asynccontextmanager
async def lifespan():
    """Startas från FastAPI:s lifespan. Ny manager per uppstart (tester
    startar appen flera gånger, och run() får bara köras en gång)."""
    global _manager
    _manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server, stateless=True, json_response=True,
    )
    async with _manager.run():
        try:
            yield
        finally:
            _manager = None


def _check_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        value = ""
    db = SessionLocal()
    try:
        return validate_access_token(db, value.strip(), resource_url(request))
    finally:
        db.close()


class McpEndpoint:
    """Rå ASGI-app för /mcp: validera token, lämna sedan över till SDK:n."""

    async def __call__(self, scope, receive, send):
        request = Request(scope, receive)
        error = await anyio.to_thread.run_sync(_check_token, request)
        if error is not None:
            meta = resource_metadata_url(request)
            response = JSONResponse(
                {"error": "invalid_token", "error_description": error},
                status_code=401,
                headers={
                    "WWW-Authenticate": (
                        f'Bearer error="invalid_token", '
                        f'error_description="{error}", '
                        f'resource_metadata="{meta}"'
                    ),
                },
            )
            await response(scope, receive, send)
            return
        if _manager is None:
            await JSONResponse({"error": "MCP not started"}, status_code=503)(
                scope, receive, send
            )
            return
        await _manager.handle_request(scope, receive, send)
