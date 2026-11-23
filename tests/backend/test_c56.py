"""C56 - städning av AI-förslagen på /trips (täckta, gamla, dubbletter).

Data efter Mikkos exempel i prod 2026-11-23: 'Oslo 21 oktober 2026' och
'Oslo 21 okt 2026' (två förslag, samma resa 140), 'Göteborg 31 aug - 1 sep'
två gånger, 'Köpenhamn & Malmö 20-21 Mar 2025' (gammal).
Postgres-testet kör sammanslagningen mot en riktig Postgres (status 'merged'
måste rymmas i trips.status).
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


def _receipt(db, mid, vendor, amount, day):
    from app.models import ProcessedMessage

    db.add(ProcessedMessage(
        message_id=mid, sender="x@example.com", subject=vendor, status="saved",
        vendor=vendor, amount=amount, currency="EUR", receipt_date=day,
        received_at=datetime(2026, 11, 1), processed_at=datetime(2026, 11, 1)))


def _suggestion(db, title, start, end, receipts, destination=None):
    from app.models import Trip, TripMessage

    t = Trip(title=title, destination=destination, start_date=start, end_date=end,
             status="suggested", base_currency="EUR", ai_confidence=90,
             total_amount=sum(r[2] for r in receipts))
    db.add(t)
    db.flush()
    for mid, vendor, amount, day in receipts:
        _receipt(db, mid, vendor, amount, day)
        db.add(TripMessage(trip_id=t.id, message_id=mid, added_by="ai_suggestion"))
    db.flush()
    return t


def _calendar(db, tid, start, end, destination, status="edited"):
    from app.models import CalendarTrip, CalendarTripDay

    db.add(CalendarTrip(id=tid, start_date=start, end_date=end, destination=destination,
                        status=status, source_event_ids=[], flights=[], hotels=[]))
    db.flush()
    db.add(CalendarTripDay(trip_id=tid, day_date=start, destination=destination))
    db.flush()


@pytest.fixture()
def seeded(db):  # noqa: F811
    _calendar(db, 140, date(2026, 10, 21), date(2026, 10, 22), "Oslo")
    _calendar(db, 139, date(2026, 8, 31), date(2026, 9, 1), "Gothenburg")
    _calendar(db, 82, date(2026, 8, 25), date(2026, 8, 26), "Malmö → Helsingborg")
    _calendar(db, 141, date(2026, 11, 3), date(2026, 11, 4), "Stockholm", "suggested")
    o1 = _suggestion(db, "Oslo 21 oktober 2026", date(2026, 10, 21), date(2026, 10, 22), [
        ("o1", "Finnair", 600.0, "2026-10-21"), ("o2", "Scandic", 68.0, "2026-10-21")])
    o2 = _suggestion(db, "Oslo 21 okt 2026", date(2026, 10, 21), date(2026, 10, 21), [
        ("o3", "Finnair", 600.0, "2026-10-21"), ("o4", "Flytoget", 20.0, "2026-10-21"),
        ("o5", "Taxi", 63.02, "2026-10-21")])
    g1 = _suggestion(db, "Göteborg 31 aug - 1 sep 2026", date(2026, 8, 31), date(2026, 9, 1), [
        ("g1", "SAS", 439.6, "2026-08-31")])
    k1 = _suggestion(db, "Köpenhamn 25-26 aug 2026", date(2026, 8, 25), date(2026, 8, 26), [
        ("k1", "Finnair", 400.0, "2026-08-25")], destination="Köpenhamn")
    s1 = _suggestion(db, "Stockholm 3 nov 2026", date(2026, 11, 3), date(2026, 11, 3), [
        ("s1", "Finnair", 446.77, "2026-11-03"), ("s2", "Finnair", 446.77, "2026-11-03"),
        ("s3", "Taxi", 0.02, "2026-11-03")])
    old = _suggestion(db, "Köpenhamn & Malmö 20-21 Mar 2025", date(2025, 3, 20),
                      date(2025, 3, 21), [("m1", "SAS", 300.0, "2025-03-20")])
    # Ett förslag utan kalenderresa och två dubbletter av det.
    r1 = _suggestion(db, "Riga 2-3 dec 2026", date(2026, 12, 2), date(2026, 12, 3), [
        ("r1", "airBaltic", 210.0, "2026-12-02"), ("r2", "Hotel", 120.0, "2026-12-02")],
        destination="Riga")
    r2 = _suggestion(db, "Riga 2 dec 2026", date(2026, 12, 2), date(2026, 12, 2), [
        ("r1b", "airBaltic", 210.0, "2026-12-02")], destination="Riga")
    tallinn = _suggestion(db, "Tallinn 2 dec 2026", date(2026, 12, 2), date(2026, 12, 2), [
        ("t1", "Tallink", 50.0, "2026-12-02")], destination="Tallinn")
    db.commit()
    return dict(o1=o1.id, o2=o2.id, g1=g1.id, k1=k1.id, s1=s1.id, old=old.id,
                r1=r1.id, r2=r2.id, tallinn=tallinn.id)


def _by_id(result):
    return {r["id"]: r for r in result["suggestions"]}


def test_covered_by_calendar_trip(db, seeded):  # noqa: F811
    from app.services.c56_trip_suggestions import analyse

    rows = _by_id(analyse(db))
    assert rows[seeded["o1"]]["covered_by"]["trip_id"] == 140
    assert rows[seeded["o2"]]["covered_by"]["trip_id"] == 140
    assert rows[seeded["g1"]]["covered_by"]["trip_id"] == 139  # Göteborg = Gothenburg
    assert rows[seeded["k1"]]["covered_by"]["trip_id"] == 82   # CPH = Malmö-resa
    assert rows[seeded["s1"]]["covered_by"]["trip_id"] == 141
    assert not any(rows[seeded[k]]["visible"] for k in ("o1", "o2", "g1", "k1", "s1"))


def test_old_hidden_and_counts(db, seeded):  # noqa: F811
    from app.services.c56_trip_suggestions import analyse

    result = analyse(db)
    rows = _by_id(result)
    assert rows[seeded["old"]]["old"] is True
    assert rows[seeded["old"]]["visible"] is False
    # Riga (behålls, flest kvitton) + Tallinn är kvar; Riga 2 dec är dubblett.
    visible = {r["id"] for r in result["suggestions"] if r["visible"]}
    assert visible == {seeded["r1"], seeded["tallinn"]}
    assert result["counts"] == {"total": 9, "visible": 2, "covered": 5, "old": 1,
                                "empty": 0, "duplicates": 2, "calendar_trips": 4}


def test_duplicate_groups_keep_most_receipts(db, seeded):  # noqa: F811
    from app.services.c56_trip_suggestions import analyse

    result = analyse(db)
    plan = {p["keep"]: p for p in result["merge_plan"]}
    assert plan[seeded["o2"]]["merge"] == [seeded["o1"]]  # 3 kvitton > 2
    assert plan[seeded["o2"]]["receipts_moved"] == 2
    assert plan[seeded["r1"]]["merge"] == [seeded["r2"]]
    # Tallinn samma dag men annan stad - ingen dubblett.
    assert all(seeded["tallinn"] not in p["merge"] for p in result["merge_plan"])
    assert seeded["tallinn"] not in plan


def test_receipt_warnings(db, seeded):  # noqa: F811
    from app.services.c56_trip_suggestions import analyse

    w = _by_id(analyse(db))[seeded["s1"]]["receipt_warnings"]
    assert "same_amount_twice:Finnair:446.77" in w
    assert "tiny_amount:Taxi:0.02" in w


def test_dry_run_changes_nothing(db, seeded):  # noqa: F811
    from app.models import Trip, TripMessage
    from app.services.c56_trip_suggestions import merge_duplicates

    before = [(t.id, t.status) for t in db.query(Trip).order_by(Trip.id)]
    n = db.query(TripMessage).count()
    res = merge_duplicates(db)
    assert res["applied"] is False and len(res["merge_plan"]) == 2
    db.expire_all()
    assert [(t.id, t.status) for t in db.query(Trip).order_by(Trip.id)] == before
    assert db.query(TripMessage).count() == n


def _apply_and_check(db, seeded):  # noqa: F811
    from app.models import Trip, TripMessage
    from app.services.c56_trip_suggestions import analyse, merge_duplicates

    res = merge_duplicates(db, apply=True)
    assert res["applied"] is True
    db.expire_all()
    assert db.get(Trip, seeded["o1"]).status == "merged"
    assert db.get(Trip, seeded["r2"]).status == "merged"
    keep = {tm.message_id for tm in db.query(TripMessage).filter(
        TripMessage.trip_id == seeded["o2"], TripMessage.removed_at.is_(None))}
    assert keep == {"o1", "o2", "o3", "o4", "o5"}
    assert float(db.get(Trip, seeded["o2"]).total_amount) == pytest.approx(1351.02)
    after = analyse(db)
    assert after["merge_plan"] == []
    assert after["counts"]["duplicates"] == 0
    # Andra körningen gör ingenting.
    assert merge_duplicates(db, apply=True)["merged"] == []


def test_apply_merges(db, seeded):  # noqa: F811
    _apply_and_check(db, seeded)


def test_calendar_receipts_still_listed_after_merge(db, seeded):  # noqa: F811
    from app.services.c56_trip_suggestions import merge_duplicates
    from app.services.calendar_trip_store import _linked_receipts

    merge_duplicates(db, apply=True)
    ids = {r["message_id"] for r in _linked_receipts(db, date(2026, 10, 21), date(2026, 10, 22))}
    assert ids == {"o1", "o2", "o3", "o4", "o5"}


def test_merged_fits_status_column():
    from app.models import Trip

    assert len("merged") <= Trip.__table__.c.status.type.length


def test_postgres_merge(pg_url):  # noqa: F811
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base

    engine = create_engine(pg_url)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        ids = seeded.__wrapped__(s)
        _apply_and_check(s, ids)
    finally:
        s.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_api_endpoints(db, seeded):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.models import CalendarTrip
    from app.routers.c56 import get_db, require_auth, router

    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[require_auth] = lambda: None
    c = TestClient(api)
    assert c.get("/api/c56/suggestions").json()["counts"]["visible"] == 2
    r = c.post("/api/c56/calendar-trips/140/approve")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(CalendarTrip, 140).status == "approved"
    assert c.post("/api/c56/calendar-trips/140/unapprove").status_code == 200
    db.expire_all()
    assert db.get(CalendarTrip, 140).status in ("edited", "suggested")
    assert c.post("/api/c56/calendar-trips/999/approve").status_code == 404


def test_api_requires_login():
    from fastapi.testclient import TestClient

    from app.main import app as main_app

    c = TestClient(main_app)
    assert c.get("/api/c56/suggestions").status_code == 401
    assert c.post("/api/c56/calendar-trips/1/approve").status_code == 401
