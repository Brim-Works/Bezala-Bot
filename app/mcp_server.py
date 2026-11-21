"""C40 — MCP-server (Streamable HTTP) på /mcp för Claude-chatten.

Verktygen läser och ändrar BARA `calendar_trips` / `calendar_trip_days`.
Inget skickas till Bezala eller Netvisor.

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
    "Times without a timezone are Helsinki time. Write purposes in English. "
    "Ask Mikko before approving."
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
    """List calendar trips: id, dates, destination, country, purpose, status, flags.

    status: 'suggested' | 'edited' | 'approved'. date_from/date_to: YYYY-MM-DD
    (trips overlapping the range). flagged_only: only trips with flags
    ('needs_review' = pick the outbound flight, 'missing_return' = no return flight found).
    """
    trips = await _run(edit.list_trips, status=status, date_from=date_from,
                       date_to=date_to, flagged_only=flagged_only)
    return {"count": len(trips), "trips": trips}


@mcp.tool()
async def get_trip(trip_id: int) -> dict:
    """Full trip: every day with times (UTC + Helsinki), destination, country,
    hotel, meetings, purpose, linked receipts and flight options."""
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

    start/end: ISO datetime, e.g. '2026-08-25T07:25' (Helsinki) or with offset/Z.
    country: ISO alpha-2, comma-separated for several ('DK,NO'). purpose: in English.
    selected_flight_id: for 'needs_review' trips, the flight_id from flight_options
    that Mikko actually took. Only omitted fields stay unchanged. Returns the updated trip.
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
    """Edit one day of a trip. date: YYYY-MM-DD. Times HH:MM Helsinki time.
    Pass an empty string to clear a field. Status → 'edited'. Returns the updated trip."""
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
    Status → 'edited'. Returns the updated trip."""
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
