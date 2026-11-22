"""C49 - MCP-verktyg för Netvisor-underlag och Netvisor-status.

Inget verktyg skickar något till Netvisor. set_netvisor_status ändrar bara
Bezala Bots egen tabell `netvisor_reports`.
"""

from __future__ import annotations

import app.models_netvisor  # noqa: F401
from app.mcp_server import _run, mcp


@mcp.tool()
async def get_netvisor_entry(trip_id: int) -> dict:
    """What to fill in in Netvisor's per diem calculator (Dagpenningskalkylator)
    for a trip: title ('Business trip {City}, {Country} {d.m.yyyy}-{d.m.yyyy}'),
    description (purpose, English) and one or more calculator rows: by air/sea,
    object (country in Swedish as Netvisor shows it), start/end date (d.m.yyyy) and
    time (HH:MM, Helsinki), line description and settings (>15 km, >5 km,
    'Inklusive kost' + which days, night travel = no, flagged if a night has no
    hotel). A trip is split into several rows when only some days have 2 free
    meals or the country changes. warnings: missing time/country, purpose not in
    English, long dashes, not approved, already created in Netvisor. Bezala Bot
    never calculates amounts - Netvisor does. Read-only."""
    from app.services.netvisor_entry import build_entry

    return await _run(build_entry, trip_id)


@mcp.tool()
async def list_netvisor_status(year: int = 2026) -> dict:
    """All approved and edited trips of the year with their Netvisor status
    (not_created | created_manual | created_api), Netvisor report id and note,
    sorted by date. Read-only."""
    from app.services.netvisor_status import list_status

    trips = await _run(list_status, year)
    return {"year": year, "count": len(trips), "trips": trips}


@mcp.tool()
async def set_netvisor_status(
    trip_id: int,
    status: str,
    netvisor_report_id: str | None = None,
    note: str | None = None,
) -> dict:
    """Record a trip's Netvisor status in Bezala Bot (nothing is sent to Netvisor):
    'created_manual' when Mikko created the travel expense report by hand
    (give the Netvisor report id), 'not_created' to clear it. A trip marked
    created_manual/created_api never gets its entry sent again. Only on Mikko's
    word."""
    from app.services.netvisor_status import set_status

    return await _run(set_status, trip_id, status, netvisor_report_id, note, "mcp")
