"""C56 tillägg - kvitton fördelas på EN kalenderresa och samma biljett
räknas en gång. Bara visning/summor: inga kvitton eller resor ändras.

(a) samma biljett två kvitton (bokningsbekräftelse + e-biljett) räknas en gång
(b) Flytoget tur och retur (2 × 276 NOK samma dag) räknas båda
(c) ett förslag som täcker flera resor fördelas per datum
(d) kvitto utan passande resa / kvitto utan uppgifter
Plus: lika läge (26 nov mellan två resor) gissas inte, Mikkos checklista vinner,
0,02-delbetalningar räknas båda, och allt körs även mot en riktig Postgres.
"""

from __future__ import annotations

import os
from datetime import date, datetime

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401


def _msg(db, mid, vendor, amount, day, currency="EUR", **kw):
    from app.models import ProcessedMessage

    db.add(ProcessedMessage(
        message_id=mid, sender="x@example.com", subject=kw.pop("subject", vendor),
        status="saved", vendor=vendor, amount=amount, currency=currency,
        receipt_date=day, received_at=datetime(2026, 11, 1),
        processed_at=datetime(2026, 11, 1), **kw))


def _sugg(db, sid, start, end, mids, status="suggested"):
    from app.models import Trip, TripMessage

    db.add(Trip(id=sid, title=f"s{sid}", start_date=start, end_date=end,
                status=status, base_currency="EUR"))
    db.flush()
    for m in mids:
        db.add(TripMessage(trip_id=sid, message_id=m, added_by="ai_suggestion"))


def _cal(db, tid, start, end, dest, status="suggested"):
    from app.models import CalendarTrip, CalendarTripDay

    db.add(CalendarTrip(id=tid, start_date=start, end_date=end, destination=dest,
                        status=status, source_event_ids=[], flights=[], hotels=[]))
    db.flush()
    db.add(CalendarTripDay(trip_id=tid, day_date=start, destination=dest))


def seed(db):  # noqa: F811
    # Kalenderresor (som i prod).
    _cal(db, 72, date(2026, 3, 5), date(2026, 3, 5), "Helsingborg", "edited")
    _cal(db, 131, date(2026, 3, 10), date(2026, 3, 11), "Malmö")
    _cal(db, 133, date(2026, 4, 13), date(2026, 4, 14), "Oslo")
    _cal(db, 134, date(2026, 4, 16), date(2026, 4, 16), "Stockholm")
    _cal(db, 136, date(2026, 4, 30), date(2026, 4, 30), "Stockholm")
    _cal(db, 140, date(2026, 10, 21), date(2026, 10, 22), "Oslo")
    _cal(db, 84, date(2026, 10, 13), date(2026, 10, 14), "Gothenburg", "edited")
    _cal(db, 146, date(2026, 11, 24), date(2026, 11, 25), "Stockholm")
    _cal(db, 147, date(2026, 11, 27), date(2026, 11, 28), "Oslo")
    # (a) samma biljett två gånger: e-biljett (original) + bokningsbekräftelse.
    _msg(db, "et-0305", "Finnair", 611.04, "2026-03-05", booking_ref="7SWR9A")
    _msg(db, "bc-0305", "Finnair", 611.04, "2026-03-05", booking_ref="7SWR9A",
         duplicate_of="et-0305")
    _msg(db, "et-0310", "Finnair", 367.38, "2026-03-10", booking_ref="8VJBIP")
    _msg(db, "bc-0310", "Finnair", 367.38, "2026-03-10", booking_ref="8VJBIP",
         duplicate_of="et-0310")
    _msg(db, "sk-0310", "Skånetrafiken", 150.0, "2026-03-10", "SEK")
    _msg(db, "sk-0311", "Skånetrafiken", 150.0, "2026-03-11", "SEK")
    _msg(db, "null-1", None, None, None)
    _sugg(db, 26, date(2026, 3, 5), date(2026, 3, 11),
          ["et-0305", "bc-0305", "et-0310", "bc-0310", "sk-0310", "sk-0311", "null-1"])
    # (c) april: ett förslag med kvitton för tre resor + ett kvitto utan resa.
    _msg(db, "ay-0413", "Finnair", 438.86, "2026-04-13")
    _msg(db, "ay-0416", "Finnair", 317.13, "2026-04-16")
    _msg(db, "ay-0430", "Finnair", 366.32, "2026-04-30")
    _msg(db, "ay-0421", "Finnair", 577.5, "2026-04-21")  # (d) ingen resa 20-22 apr
    _sugg(db, 9, date(2026, 4, 13), date(2026, 4, 30),
          ["ay-0413", "ay-0416", "ay-0430", "ay-0421"])
    # (b) Flytoget tur och retur samma dag, olika biljetter.
    _msg(db, "ay-1021", "Finnair", 630.51, "2026-10-21")
    _msg(db, "ft-out", "Flytoget", 276.0, "2026-10-21", "NOK")
    _msg(db, "ft-back", "Flytoget", 276.0, "2026-10-21", "NOK")
    _sugg(db, 18, date(2026, 10, 21), date(2026, 10, 21), ["ay-1021", "ft-out", "ft-back"])
    # 0,02-delbetalningar (märkta som dubblett av C44) räknas båda.
    _msg(db, "ay-1013", "Finnair", 667.37, "2026-10-13")
    _msg(db, "p1", "Finnair", 0.02, "2026-10-13", booking_ref="8X3V65")
    _msg(db, "p2", "Finnair", 0.02, "2026-10-13", booking_ref="8X3V65", duplicate_of="p1")
    _sugg(db, 17, date(2026, 10, 13), date(2026, 10, 14), ["ay-1013", "p1", "p2"])
    # 26 nov: HEL-CPH mellan Stockholm (24-25) och Oslo (27-28) - gissas inte.
    _msg(db, "ay-1126", "Finnair", 134.48, "2026-11-26",
         subject="YOUR ETICKET RECEIPT", file_name="20261126 Finnair Helsingfors-Kopenhamn.pdf")
    _msg(db, "ay-1124", "Finnair", 540.79, "2026-11-24")
    _msg(db, "ay-1127", "Finnair", 209.48, "2026-11-27")
    _sugg(db, 28, date(2026, 11, 24), date(2026, 11, 26), ["ay-1124", "ay-1126"])
    _sugg(db, 23, date(2026, 11, 26), date(2026, 11, 28), ["ay-1126", "ay-1127"])
    db.commit()


def _sums(a, tid):
    from app.services.c56_receipt_allocation import totals

    return totals(a["by_trip"][tid])


def _run(db):  # noqa: F811
    from app.services.c56_receipt_allocation import allocate

    seed(db)
    return allocate(db)


def test_a_same_ticket_counted_once(db):  # noqa: F811
    a = _run(db)
    assert _sums(a, 72) == {"EUR": 611.04}
    r72 = {r["message_id"]: r for r in a["by_trip"][72]}
    assert r72["bc-0305"]["counted"] is False and r72["bc-0305"]["duplicate_of"] == "et-0305"
    # Malmö: biljetten en gång + Skånetrafiken 150 SEK på två olika dagar.
    assert _sums(a, 131) == {"EUR": 367.38, "SEK": 300.0}


def test_b_flytoget_round_trip_both_counted(db):  # noqa: F811
    a = _run(db)
    assert _sums(a, 140) == {"EUR": 630.51, "NOK": 552.0}


def test_c_multi_trip_suggestion_split_by_date(db):  # noqa: F811
    a = _run(db)
    assert _sums(a, 133) == {"EUR": 438.86}
    assert _sums(a, 134) == {"EUR": 317.13}
    assert _sums(a, 136) == {"EUR": 366.32}
    # Ett kvitto ligger på exakt en resa.
    seen = [r["message_id"] for lst in a["by_trip"].values() for r in lst]
    assert len(seen) == len(set(seen))


def test_d_receipt_without_trip_and_missing_info(db):  # noqa: F811
    a = _run(db)
    un = {r["message_id"]: r for r in a["unassigned"]}
    assert un["ay-0421"]["reason"] == "no_trip"
    assert [r["message_id"] for r in a["missing_info"]] == ["null-1"]


def test_tie_is_not_guessed(db):  # noqa: F811
    a = _run(db)
    un = {r["message_id"]: r for r in a["unassigned"]}
    assert un["ay-1126"]["reason"] == "ambiguous"
    assert un["ay-1126"]["candidates"] == [146, 147]
    assert _sums(a, 146) == {"EUR": 540.79}
    assert _sums(a, 147) == {"EUR": 209.48}


def test_checklist_pin_wins(db):  # noqa: F811
    from app.models import TripExpectation
    from app.services.c56_receipt_allocation import allocate

    seed(db)
    db.add(TripExpectation(trip_id=147, kind="flight", slot="", status="found",
                           set_by="user", message_id="ay-1126"))
    db.commit()
    a = allocate(db)
    assert _sums(a, 147) == {"EUR": 343.96}
    assert all(r["message_id"] != "ay-1126" for r in a["unassigned"])


def test_tiny_part_payments_both_counted(db):  # noqa: F811
    a = _run(db)
    assert _sums(a, 84) == {"EUR": 667.41}


def test_nothing_is_written(db):  # noqa: F811
    from app.models import CalendarTrip, ProcessedMessage, Trip, TripMessage
    from app.services.c56_receipt_allocation import allocate

    seed(db)
    before = (
        [(t.id, t.status) for t in db.query(CalendarTrip).order_by(CalendarTrip.id)],
        [(t.id, t.status) for t in db.query(Trip).order_by(Trip.id)],
        db.query(TripMessage).count(),
        [(m.message_id, m.duplicate_of) for m in db.query(ProcessedMessage).order_by(ProcessedMessage.id)],
    )
    allocate(db)
    db.rollback()
    after = (
        [(t.id, t.status) for t in db.query(CalendarTrip).order_by(CalendarTrip.id)],
        [(t.id, t.status) for t in db.query(Trip).order_by(Trip.id)],
        db.query(TripMessage).count(),
        [(m.message_id, m.duplicate_of) for m in db.query(ProcessedMessage).order_by(ProcessedMessage.id)],
    )
    assert before == after


def test_list_trips_and_endpoint_use_allocation(db):  # noqa: F811
    from app.services.calendar_trip_store import list_trips, receipts_without_trip

    seed(db)
    trips = {t["id"]: t for t in list_trips(db)}
    assert trips[133]["receipt_totals"] == {"EUR": 438.86}
    assert trips[134]["receipt_totals"] == {"EUR": 317.13}
    extra = receipts_without_trip(db)
    assert {r["message_id"] for r in extra["unassigned"]} == {"ay-0421", "ay-1126"}
    assert len(extra["missing_info"]) == 1


def test_postgres(pg_url):  # noqa: F811
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base
    from app.services.c56_receipt_allocation import allocate

    engine = create_engine(pg_url)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        seed(s)
        a = allocate(s)
        assert _sums(a, 72) == {"EUR": 611.04}
        assert _sums(a, 140) == {"EUR": 630.51, "NOK": 552.0}
        assert _sums(a, 133) == {"EUR": 438.86}
        assert {r["message_id"] for r in a["unassigned"]} == {"ay-0421", "ay-1126"}
    finally:
        s.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_compare_before_after(db):  # noqa: F811
    from app.services.c56_receipt_allocation import compare

    seed(db)
    rows = {r["trip_id"]: r for r in compare(db)["trips"]}
    # Före: april-förslaget gav samma summa på alla tre resor.
    assert rows[133]["before"] == rows[134]["before"] == rows[136]["before"]
    assert rows[133]["after"] == {"EUR": 438.86}
    assert rows[72]["before"]["EUR"] > rows[72]["after"]["EUR"] == 611.04
    assert rows[72]["duplicates_counted_once"] == 1
