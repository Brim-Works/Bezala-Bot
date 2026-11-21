"""C40 — MCP-server (Streamable HTTP) på /mcp för Claude-chatten.

Reseverktygen läser och ändrar BARA `calendar_trips` / `calendar_trip_days`.
C43-verktygen (list_missing, mark_private_expense, mark_receipt_not_available,
clear_bill_line_status) läser Bezalas saknade kvitton och skriver bara i
`bill_line_statuses`; mark_private_expense(create_draft=True) skapar ett
Bezala-UTKAST. Inget skickas för attest och inget går till Netvisor.

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
    "Receipts: list_missing shows company-card rows in Bezala that still lack a "
    "receipt. Rows flagged 'possible_private' (parking outside any trip) — ask Mikko "
    "whether it was private. mark_private_expense / mark_receipt_not_available only "
    "on Mikko's word. Nothing is ever submitted for approval in Bezala."
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
    purpose (English), status, flags, travel times and flight times (Helsinki).

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


# ---------- C43: kortrader utan kvitto ----------


def _missing_rows() -> list[dict]:
    from app.services.bezala_client import BezalaClient
    from app.services.bill_line_status import normalize_missing_receipt

    bezala = BezalaClient()
    try:
        return [normalize_missing_receipt(r) for r in bezala.list_missing_receipts()]
    finally:
        bezala.close()


def _snapshot(bill_line_id: str) -> dict | None:
    try:
        return next(
            (r for r in _missing_rows() if str(r.get("id")) == str(bill_line_id)),
            None,
        )
    except Exception:  # noqa: BLE001
        logger.warning("MCP: kunde inte hämta kortrad %s från Bezala", bill_line_id)
        return None


@mcp.tool()
async def list_missing(include_handled: bool = False) -> dict:
    """Company-card rows in Bezala that still lack a receipt: bill_line_id,
    description, amount, currency, date, trip_id (calendar trip around the date)
    and flags. 'possible_private' = parking (e.g. Helsinki airport / Moovy)
    outside any calendar trip — ask Mikko if it was a private expense.
    Rows marked private_expense or receipt_not_available are handled and not
    counted as missing (include_handled=True lists them too)."""
    from app.services.bill_line_status import build_missing_list

    rows = await anyio.to_thread.run_sync(_missing_rows)
    return await _run(build_missing_list, rows, include_handled=include_handled)


@mcp.tool()
async def mark_private_expense(
    bill_line_id: str, note: str | None = None, create_draft: bool = False,
) -> dict:
    """Mark a company-card row as a PRIVATE expense (paid by mistake with the
    company card). The Bezala comment becomes 'Private expense – to be deducted
    from salary.' so payroll deducts it. create_draft=True also creates a Bezala
    DRAFT for the row with that comment — it is never submitted for approval.
    Only use on Mikko's word."""
    from app.services import bill_line_status as bls

    snap = await anyio.to_thread.run_sync(_snapshot, bill_line_id)

    def work(db):
        try:
            row = bls.set_status(
                db, bill_line_id, bls.STATUS_PRIVATE_EXPENSE, note=note, snapshot=snap,
            )
        except bls.BillLineStatusError as exc:
            raise TripEditError(str(exc)) from exc
        if create_draft:
            from app.services.bezala_client import BezalaClient

            bezala = BezalaClient()
            try:
                bls.create_private_expense_draft(db, bezala, bill_line_id, snapshot=snap)
            finally:
                bezala.close()
        return bls.serialize_status(row)

    return await _run(work)


@mcp.tool()
async def mark_receipt_not_available(bill_line_id: str, note: str) -> dict:
    """Mark a company-card row as 'receipt not available' (the vendor gives no
    receipt). note = short English explanation, required. The row stops counting
    as missing. Only use on Mikko's word."""
    from app.services import bill_line_status as bls

    snap = await anyio.to_thread.run_sync(_snapshot, bill_line_id)

    def work(db):
        try:
            row = bls.set_status(
                db, bill_line_id, bls.STATUS_RECEIPT_NOT_AVAILABLE, note=note,
                snapshot=snap,
            )
        except bls.BillLineStatusError as exc:
            raise TripEditError(str(exc)) from exc
        return bls.serialize_status(row)

    return await _run(work)


@mcp.tool()
async def clear_bill_line_status(bill_line_id: str) -> dict:
    """Undo mark_private_expense / mark_receipt_not_available for a row
    (it counts as missing again). Does not delete anything in Bezala."""
    from app.services import bill_line_status as bls

    def work(db):
        return {"bill_line_id": bill_line_id, "cleared": bls.clear_status(db, bill_line_id)}

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
