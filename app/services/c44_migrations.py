"""C44 — körs i bakgrunden vid uppstart (blockerar inte healthcheck).

1. Läs detaljer ur mailtexten (Gmail, read-only) för parkerings- och
   flygkvitton: Moovy infart/utfart, Finnair/Amadeus-segment, PNR, biljett.
2. Markera dubblettkvitton (samma belopp + datum + biljett/bokning).
   Bezala rörs aldrig — rapporten loggas med prefixet [c44-dupes].
3. Resetider ur parkeringen ('suggested') / förslag (låsta resor).
4. Räkna om alla checklistor. Flygposter som ändras loggas med
   [c44-flight] (C44: flygkvitto måste matcha flightnummer/datum).
Idempotent — kan köras vid varje uppstart.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


def run_c44(db) -> dict:
    from app.models import CalendarTrip
    from app.services import receipt_details as rd
    from app.services.trip_completeness import checklists_for
    from app.services.trip_parking import apply_parking_times

    out: dict = {}
    try:
        out["details"] = rd.enrich_receipts(db, limit=400)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("[c44] kvittodetaljer misslyckades")
    try:
        out["duplicates"] = rd.mark_duplicates(db)
        rep = rd.duplicate_report(db, 2026)
        logger.info(
            "[c44-dupes] 2026: %d grupper, %d dubblettkvitton, %d dubbletter med Bezala-utkast",
            rep["groups"], rep["duplicate_receipts"], rep["duplicates_with_bezala_draft"],
        )
        for g in rep["items"]:
            logger.info(
                "[c44-dupes] %s %s %s %s pnr=%s kept=%s receipts=%s",
                g["date"], g["vendor"], g["amount"], g["currency"], g["booking_ref"],
                g["kept_message_id"],
                [(r["message_id"], "draft" if r["bezala_draft"] else "-") for r in g["receipts"]],
            )
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("[c44] dubblettkontroll misslyckades")
    try:
        out["parking"] = apply_parking_times(db)
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("[c44] parkeringstider misslyckades")
    try:
        trips = db.query(CalendarTrip).order_by(CalendarTrip.start_date).all()
        lists = checklists_for(db, trips)
        for t in trips:
            flight = next((i for i in lists[t.id]["items"] if i["kind"] == "flight"), None)
            parking = next((i for i in lists[t.id]["items"] if i["kind"] == "parking"), None)
            logger.info(
                "[c44-check] trip=%s %s–%s flight=%s %s parking=%s %s time_source=%s",
                t.id, t.start_date, t.end_date,
                flight and flight["status"], flight and (flight["receipt"] or {}).get("message_id"),
                parking and parking["status"], parking and (parking["receipt"] or {}).get("message_id"),
                t.time_source,
            )
    except Exception:  # noqa: BLE001
        db.rollback()
        logger.exception("[c44] omräkning av checklistor misslyckades")
    logger.info("[c44] klart: %s", out)
    return out


def start_background() -> threading.Thread:
    def work():
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            run_c44(db)
        except Exception:  # noqa: BLE001
            logger.exception("[c44] bakgrundskörning misslyckades")
        finally:
            db.close()

    t = threading.Thread(target=work, name="c44-startup", daemon=True)
    t.start()
    return t
