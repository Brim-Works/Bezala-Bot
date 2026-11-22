"""C49 Del 1 - Netvisor-status per resa.

Skriver BARA tabellen `netvisor_reports`. Resor (`calendar_trips`) ändras
aldrig härifrån. Inget skickas till Netvisor.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.models import CalendarTrip, MaintenanceTask
from app.models_netvisor import CREATED_STATUSES, NETVISOR_STATUSES, NetvisorReport
from app.services.calendar_trip_edit import TripEditError

logger = logging.getLogger(__name__)

SEED_TASK = "c49_seed_netvisor_trip_93_v1"
SEED_TRIP_ID = 93
SEED_REPORT_ID = "1655"
SEED_NOTE = "Test report, awaiting Riikka's review"

LISTED_TRIP_STATUSES = ("approved", "edited")


def get_report(db: Session, trip_id: int) -> NetvisorReport | None:
    return db.query(NetvisorReport).filter(NetvisorReport.trip_id == trip_id).first()


def status_of(db: Session, trip_id: int) -> dict:
    row = get_report(db, trip_id)
    if row is None:
        return {"status": "not_created", "netvisor_report_id": None, "note": None,
                "updated_at": None}
    return {
        "status": row.status,
        "netvisor_report_id": row.netvisor_report_id,
        "note": row.note,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def is_created(db: Session, trip_id: int) -> bool:
    row = get_report(db, trip_id)
    return bool(row and row.status in CREATED_STATUSES)


def set_status(
    db: Session,
    trip_id: int,
    status: str,
    netvisor_report_id: str | None = None,
    note: str | None = None,
    source: str = "mcp",
) -> dict:
    """Sätt Netvisor-status för en resa (en rad per resa, uppdateras)."""
    status = (status or "").strip()
    if status not in NETVISOR_STATUSES:
        raise TripEditError(
            f"Unknown Netvisor status '{status}'. Use one of: {', '.join(NETVISOR_STATUSES)}."
        )
    trip = db.get(CalendarTrip, trip_id)
    if trip is None:
        raise TripEditError(f"Trip {trip_id} not found.")
    report_id = (str(netvisor_report_id).strip() if netvisor_report_id is not None else None) or None
    if status in CREATED_STATUSES and not report_id and status == "created_api":
        raise TripEditError("created_api needs the netvisor_report_id returned by Netvisor.")
    row = get_report(db, trip_id)
    if row is None:
        row = NetvisorReport(trip_id=trip_id)
        db.add(row)
    row.status = status
    row.netvisor_report_id = report_id
    row.note = note.strip() if note else None
    row.source = source
    db.commit()
    db.refresh(row)
    logger.info("C49: Netvisor-status resa %s = %s (%s)", trip_id, status, report_id)
    return {"trip_id": trip_id, **status_of(db, trip_id)}


def seed_once(db: Session) -> bool:
    """Engångs: resa 93 (Helsingborg 13-14 jan) är redan skapad för hand.

    Sätts bara om resan finns och verkligen är Helsingborg 2026-01-13 och om
    det inte redan finns en rad för den."""
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == SEED_TASK).first():
        return False
    trip = db.get(CalendarTrip, SEED_TRIP_ID)
    if trip is None or trip.start_date != date(2026, 1, 13) or "Helsingborg" not in (
        trip.destination or ""
    ):
        # Resan finns inte (t.ex. testdatabas) - försök igen nästa gång.
        return False
    if get_report(db, SEED_TRIP_ID) is None:
        db.add(NetvisorReport(
            trip_id=SEED_TRIP_ID, status="created_manual",
            netvisor_report_id=SEED_REPORT_ID, note=SEED_NOTE, source="seed",
        ))
    db.add(MaintenanceTask(name=SEED_TASK))
    db.commit()
    logger.info("C49: resa 93 markerad created_manual (Netvisor %s)", SEED_REPORT_ID)
    return True


def list_status(db: Session, year: int = 2026) -> list[dict]:
    """Alla godkända/redigerade resor för året med Netvisor-status, på datum."""
    try:
        seed_once(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("C49: seed av resa 93 misslyckades")
    trips = (
        db.query(CalendarTrip)
        .filter(CalendarTrip.status.in_(LISTED_TRIP_STATUSES))
        .filter(CalendarTrip.start_date >= date(year, 1, 1))
        .filter(CalendarTrip.start_date <= date(year, 12, 31))
        .order_by(CalendarTrip.start_date, CalendarTrip.id)
        .all()
    )
    reports = {
        r.trip_id: r
        for r in db.query(NetvisorReport).filter(
            NetvisorReport.trip_id.in_([t.id for t in trips] or [-1])
        )
    }
    out = []
    for t in trips:
        r = reports.get(t.id)
        out.append({
            "trip_id": t.id,
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "destination": t.destination,
            "country": t.country,
            "trip_status": t.status,
            "netvisor_status": r.status if r else "not_created",
            "netvisor_report_id": r.netvisor_report_id if r else None,
            "note": r.note if r else None,
        })
    return out


def overview(db: Session, year: int = 2026, ready_only: bool = False) -> dict:
    """C49 Del 5 - alla resor för året: datum, ort, land, status,
    Netvisor-status och varningar. ready_only = 'Klara för Netvisor'
    (approved + not_created + inga varningar)."""
    from app.services.netvisor_entry import build_entry

    try:
        seed_once(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("C49: seed av resa 93 misslyckades")
    trips = (
        db.query(CalendarTrip)
        .filter(CalendarTrip.start_date >= date(year, 1, 1))
        .filter(CalendarTrip.start_date <= date(year, 12, 31))
        .order_by(CalendarTrip.start_date, CalendarTrip.id)
        .all()
    )
    rows = []
    for t in trips:
        nv = status_of(db, t.id)
        if t.status == "rejected":
            warnings: list[str] = []
        else:
            try:
                warnings = build_entry(db, t.id)["warnings"]
            except Exception as exc:  # noqa: BLE001
                warnings = [f"Could not build the Netvisor entry: {exc}"]
        ready = t.status == "approved" and nv["status"] == "not_created" and not warnings
        rows.append({
            "trip_id": t.id,
            "start_date": t.start_date.isoformat(),
            "end_date": t.end_date.isoformat(),
            "destination": t.destination,
            "country": t.country,
            "trip_status": t.status,
            "netvisor_status": nv["status"],
            "netvisor_report_id": nv["netvisor_report_id"],
            "warnings": warnings,
            "ready_for_netvisor": ready,
        })
    ready_count = sum(1 for r in rows if r["ready_for_netvisor"])
    if ready_only:
        rows = [r for r in rows if r["ready_for_netvisor"]]
    return {"year": year, "count": len(rows), "ready_count": ready_count, "trips": rows}
