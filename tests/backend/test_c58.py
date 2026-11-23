"""C58 - Resor: färre onödiga frågor, aldrig dubbla bil-ben, hotellnätter.

Del 1: HEL-frågan bara när resan är slut, inte vid Moovy (±1 dygn), inte
       när Mikko redan lagt in bil-ben.
Del 2: manuellt bil-ben + Moovy-kvitto -> inga extra (automatiska) ben.
Del 3: chippet visar vilka kvitton som saknas (missing_receipts).
Del 4: val per hotellnatt (inget hotell / redan i Bezala / faktureras direkt
       till företaget / kvitto kommer) + ångra, hotellnätter i Admin-raden,
       Netvisor utan utgiftsrad, tips (bara läsning), PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

import app.models_netvisor  # noqa: E402,F401 - netvisor_reports i test-databasen
from app.services import trip_completeness as tc  # noqa: E402
from app.services import trip_legs as tl  # noqa: E402
from app.services.calendar_trip_edit import TripEditError  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _receipt  # noqa: E402
from tests.backend.test_c53 import _car_env, _flight, _trip  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401

TODAY = date(2026, 11, 23)

STO = [
    _flight("AY 803", "HEL", "ARN", "2026-11-24T06:00:00+00:00", "2026-11-24T07:05:00+00:00"),
    _flight("AY 814", "ARN", "HEL", "2026-11-25T15:55:00+00:00", "2026-11-25T16:55:00+00:00"),
]
OSLO_PAST = [
    _flight("AY 911", "HEL", "OSL", "2026-10-21T04:25:00+00:00", "2026-10-21T05:50:00+00:00"),
    _flight("AY 918", "OSL", "HEL", "2026-10-22T18:30:00+00:00", "2026-10-22T19:55:00+00:00"),
]
ANG = [
    _flight("AY 959", "HEL", "CPH", "2026-01-20T05:00:00+00:00", "2026-01-20T06:40:00+00:00"),
    _flight("AY 960", "CPH", "HEL", "2026-01-22T17:25:00+00:00", "2026-01-22T19:00:00+00:00"),
]


@pytest.fixture(autouse=True)
def _today():
    with patch("app.services.trip_completeness._today", return_value=TODAY):
        yield


def _kinds(view):
    return [q["kind"] for q in view["questions"]]


def _manual_car(db, trip_id, d, direction, energy):  # noqa: F811
    frm, to = ("Porvoo", "HEL") if direction == "out" else ("HEL", "Porvoo")
    return tl.add_trip_leg(db, trip_id, mode="car_phev", date_=d, km=48, from_place=frm,
                           to_place=to, energy_type=energy, is_estimate=True,
                           note="Mikko confirmed: company car to airport")


# ---------------------------------------------------------------- Del 1


def test_no_question_before_the_trip_has_ended(db):  # noqa: F811
    t = _trip(db, date(2026, 11, 24), date(2026, 11, 25), STO, time_source="flight_default",
              dest="Stockholm")
    view = tl.get_trip_legs(db, t.id)
    assert "home_transport" not in _kinds(view)
    assert "home_transport_unknown" not in view["legs_flags"]


def test_question_after_the_trip_without_moovy(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_PAST,
              time_source="flight_default")
    assert "home_transport" in _kinds(tl.get_trip_legs(db, t.id))


@pytest.mark.parametrize("offset,asked", [(-1, False), (0, False), (1, False), (2, True)])
def test_moovy_within_one_day_answers_the_question(db, offset, asked):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_PAST,
              time_source="flight_default")
    day = (date(2026, 10, 21) + timedelta(days=offset)) if offset < 0 \
        else date(2026, 10, 22) + timedelta(days=offset)
    _receipt(db, day, "Moovy", 37.49, waiting=False, category="Parkering")
    assert ("home_transport" in _kinds(tl.get_trip_legs(db, t.id))) is asked


def test_manual_car_leg_means_no_question(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_PAST,
              time_source="flight_default")
    _manual_car(db, t.id, "2026-10-21", "out", "electricity")
    view = tl.get_trip_legs(db, t.id)
    assert "home_transport" not in _kinds(view)


# ---------------------------------------------------------------- Del 2


def _seed_146_147(db):  # noqa: F811
    t146 = _trip(db, date(2026, 11, 24), date(2026, 11, 25), STO, dest="Stockholm")
    t147 = _trip(db, date(2026, 11, 27), date(2026, 11, 28), [
        _flight("AY 911", "HEL", "OSL", "2026-11-27T04:25:00+00:00", "2026-11-27T05:50:00+00:00"),
        _flight("AY 918", "OSL", "HEL", "2026-11-28T18:30:00+00:00", "2026-11-28T19:55:00+00:00"),
    ])
    for t, out, back in ((t146, "2026-11-24", "2026-11-25"), (t147, "2026-11-27", "2026-11-28")):
        _manual_car(db, t.id, out, "out", "electricity")
        _manual_car(db, t.id, back, "back", "petrol")
    return t146, t147


def test_manual_car_legs_and_moovy_give_no_extra_legs(db):  # noqa: F811
    """146/147: manuella bil-ben (48 km, el ut, bensin hem) + Moovy -> 2 bil-ben."""
    t146, t147 = _seed_146_147(db)
    _receipt(db, date(2026, 11, 25), "Moovy", 20.0, waiting=False, category="Parkering")
    _receipt(db, date(2026, 11, 28), "Moovy", 20.0, waiting=False, category="Parkering")
    for t in (t146, t147):
        for _ in range(2):  # två omräkningar - fortfarande samma ben
            view = tl.get_trip_legs(db, t.id)
        cars = [leg for leg in view["legs"] if leg["mode"] == "car_phev"]
        assert len(cars) == 2
        assert all(c["source"] == "manual" and c["set_by"] == "user" for c in cars)
        assert [c["energy_type"] for c in cars] == ["electricity", "petrol"]
        assert view["admin"]["company_car_info_only"]["km"] == 96
        assert view["questions"] == []


def test_old_auto_car_legs_are_removed_when_manual_exists(db):  # noqa: F811
    """Om boten redan skapat Moovy-ben och Mikko sedan lägger in egna: auto-benen tas bort."""
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_PAST)  # time_source=parking
    first = tl.get_trip_legs(db, t.id)
    assert sum(1 for leg in first["legs"] if leg["mode"] == "car_phev") == 2
    _manual_car(db, t.id, "2026-10-21", "out", "electricity")
    _manual_car(db, t.id, "2026-10-22", "back", "petrol")
    view = tl.get_trip_legs(db, t.id)
    cars = [leg for leg in view["legs"] if leg["mode"] == "car_phev"]
    assert len(cars) == 2 and all(c["source"] == "manual" for c in cars)


def test_manual_legs_are_locked(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_PAST)
    leg = _manual_car(db, t.id, "2026-10-21", "out", "electricity")
    tl.get_trip_legs(db, t.id)
    from app.models import TripLeg

    row = db.get(TripLeg, leg["leg_id"])
    assert row is not None and row.km == 48 and row.source == "manual"


# ---------------------------------------------------------------- Del 3 + 4


def _seed_130(db):  # noqa: F811
    """Resa 130: Ängelholm 20-22 jan. Natt 20 jan har Riverside 890 SEK, natt 21 saknas."""
    t = _trip(db, date(2026, 1, 20), date(2026, 1, 22), ANG, dest="Ängelholm")
    _receipt(db, date(2026, 1, 21), "Riverside Hotel", 890.0, "SEK", waiting=False,
             category="Hotell", message_id="riverside-890")
    return t


def _hotel(cl, night):
    return next(i for i in cl["items"] if i["kind"] == "hotel" and i["slot"] == night)


def test_missing_receipts_listed_for_the_chip(db):  # noqa: F811
    t = _seed_130(db)
    cl = tc.get_trip_checklist(db, t.id)
    assert {"kind": "hotel", "slot": "2026-01-21"} in cl["completeness"]["missing_receipts"]
    assert _hotel(cl, "2026-01-20")["status"] == "found"


@pytest.mark.parametrize("choice,status,counted,nights", [
    ("no_hotel", "not_applicable", False, 1),
    ("handled_in_bezala", "found", False, 2),
    ("invoiced_to_company", "found", False, 2),
    ("receipt_coming", "missing", False, 2),
])
def test_each_choice(db, choice, status, counted, nights):  # noqa: F811
    t = _seed_130(db)
    cl = tc.set_hotel_night_choice(db, t.id, "2026-01-21", choice)
    row = _hotel(cl, "2026-01-21")
    assert row["status"] == status and row["resolution"] == choice and row["resolved_at"]
    assert row["set_by"] == "user"
    # 'kvitto kommer' räknas inte som saknat de första dagarna.
    hotel_missing = [k for k in cl["completeness"]["missing_kinds"] if k.startswith("hotel")]
    assert len(hotel_missing) == (1 if counted else 0)
    hn = tl.hotel_nights(db, t)
    assert hn["nights"] == nights
    assert tl.get_trip_legs(db, t.id)["admin"]["hotel_nights"] == nights


def test_receipt_coming_warns_again_after_a_week(db):  # noqa: F811
    t = _seed_130(db)
    tc.set_hotel_night_choice(db, t.id, "2026-01-21", "receipt_coming")
    with patch("app.services.trip_completeness._today", return_value=TODAY + timedelta(days=8)):
        cl = tc.get_trip_checklist(db, t.id)
    assert "hotel 2026-01-21" in cl["completeness"]["missing_kinds"]
    assert cl["completeness"]["waiting"] == 0


def test_receipt_coming_then_receipt_arrives(db):  # noqa: F811
    t = _trip(db, date(2026, 4, 22), date(2026, 4, 23), [
        _flight("AY 961", "HEL", "CPH", "2026-04-22T05:00:00+00:00", "2026-04-22T06:40:00+00:00"),
        _flight("AY 962", "CPH", "HEL", "2026-04-23T17:25:00+00:00", "2026-04-23T19:00:00+00:00"),
    ], dest="Malmö")
    tc.set_hotel_night_choice(db, t.id, "2026-04-22", "receipt_coming")
    _receipt(db, date(2026, 4, 23), "Hotel Terazza", 1200.0, "SEK", waiting=False,
             category="Hotell")
    cl = tc.get_trip_checklist(db, t.id)
    assert _hotel(cl, "2026-04-22")["status"] == "found"
    assert not [k for k in cl["completeness"]["missing_kinds"] if k.startswith("hotel")]


def test_undo(db):  # noqa: F811
    t = _seed_130(db)
    tc.set_hotel_night_choice(db, t.id, "2026-01-21", "no_hotel")
    cl = tc.set_hotel_night_choice(db, t.id, "2026-01-21", "undo")
    row = _hotel(cl, "2026-01-21")
    assert row["status"] == "missing" and row["resolution"] is None and row["set_by"] == "auto"
    assert tl.hotel_nights(db, t)["nights"] == 2


def test_bad_input(db):  # noqa: F811
    t = _seed_130(db)
    with pytest.raises(TripEditError):
        tc.set_hotel_night_choice(db, t.id, "2026-01-21", "maybe")
    with pytest.raises(TripEditError):
        tc.set_hotel_night_choice(db, t.id, "2026-01-25", "no_hotel")


def test_nothing_prefilled(db):  # noqa: F811
    t = _seed_130(db)
    cl = tc.get_trip_checklist(db, t.id)
    assert all(i.get("resolution") is None for i in cl["items"])


def test_130_and_135_invoiced_to_company_are_done(db):  # noqa: F811
    """Mikkos svar: båda nätterna betalda direkt av företaget."""
    from app.services.netvisor_entry import build_entry
    from app.services.netvisor_payload import build_payload

    t130 = _seed_130(db)
    t135 = _trip(db, date(2026, 4, 22), date(2026, 4, 23), [
        _flight("AY 961", "HEL", "CPH", "2026-04-22T05:00:00+00:00", "2026-04-22T06:40:00+00:00"),
        _flight("AY 962", "CPH", "HEL", "2026-04-23T17:25:00+00:00", "2026-04-23T19:00:00+00:00"),
    ], dest="Malmö", status="approved")
    tc.set_hotel_night_choice(db, t130.id, "2026-01-21", "invoiced_to_company",
                              "Riverside Hotel Ängelholm - betald direkt av företaget")
    cl = tc.set_hotel_night_choice(db, t135.id, "2026-04-22", "invoiced_to_company",
                                   "Hotel Terazza - betald direkt av företaget")
    assert _hotel(cl, "2026-04-22")["note"].startswith("Hotel Terazza")
    hotel_missing = [m for m in cl["completeness"]["missing_receipts"] if m["kind"] == "hotel"]
    assert hotel_missing == []
    assert tl.hotel_nights(db, t130)["nights"] == 2
    assert tl.hotel_nights(db, t135)["invoiced_to_company"] == ["2026-04-22"]
    entry = build_entry(db, t135.id)
    assert entry["hotel_invoiced_to_company"] == ["2026-04-22"]
    assert any("invoiced directly to the company" in n for n in entry["notes"])
    payload = build_payload(db, t135.id)["payload"]["tripexpense"]
    # Bara traktamentsrader - ingen utgiftsrad för hotellet.
    assert set(payload) == {"header", "description", "dailycompensationlines"}


def test_choice_survives_calendar_refresh(db):  # noqa: F811
    t = _seed_130(db)
    tc.set_hotel_night_choice(db, t.id, "2026-01-21", "handled_in_bezala", "Bezala 4712556")
    marks = tc.snapshot_user_marks(db, [t.id])
    assert marks[0]["resolution"] == "handled_in_bezala"
    from app.models import CalendarTrip

    tc.delete_for_trips(db, [t.id])
    new = CalendarTrip(start_date=t.start_date, end_date=t.end_date, destination="Ängelholm",
                       status="suggested", flights=ANG, hotels=[], source_event_ids=[], flags=[])
    db.add(new)
    db.flush()
    assert tc.restore_user_marks(db, marks, [new]) == 1
    db.commit()
    row = _hotel(tc.get_trip_checklist(db, new.id), "2026-01-21")
    assert row["resolution"] == "handled_in_bezala" and row["status"] == "found"


def test_tips_are_read_only(db):  # noqa: F811
    from app.services.c58_hotel_nights import hotel_night_tips

    t = _seed_130(db)
    tc.get_trip_checklist(db, t.id)

    class FakeBezala:
        def list_transactions(self):
            return [
                {"transaction": {"id": 4712556, "date": "2026-01-21", "original_amount": 890,
                                 "original_currency": "SEK", "description": "Riverside Hotel"}},
                {"transaction": {"id": 1, "date": "2026-01-21", "description": "Taxi"}},
                {"transaction": {"id": 2, "date": "2026-03-01", "description": "Hotel X"}},
            ]

        def __getattr__(self, name):  # allt annat än GET-listan är förbjudet
            raise AssertionError(f"tips must not call {name}")

    out = hotel_night_tips(db, t.id, client=FakeBezala())
    assert [n["night"] for n in out["nights"]] == ["2026-01-21"]
    tips = out["nights"][0]["tips"]
    assert {"bot", "bezala"} == {x["source"] for x in tips}
    assert [x["bezala_transaction_id"] for x in tips if x["source"] == "bezala"] == ["4712556"]
    assert tc.get_trip_checklist(db, t.id)["items"] and _hotel(
        tc.get_trip_checklist(db, t.id), "2026-01-21")["resolution"] is None


def test_api_endpoint(db):  # noqa: F811
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers.c58 import get_db, require_auth  # samma objekt som routern använder

    t = _seed_130(db)
    app.dependency_overrides[get_db] = lambda: db
    try:
        c = TestClient(app)
        r = c.post(f"/api/c58/calendar-trips/{t.id}/hotel-night",
                   json={"night": "2026-01-21", "choice": "no_hotel"})
        assert r.status_code == 401
        app.dependency_overrides[require_auth] = lambda: None
        r = c.post(f"/api/c58/calendar-trips/{t.id}/hotel-night",
                   json={"night": "2026-01-21", "choice": "no_hotel"})
        assert r.status_code == 200, r.text
        assert _hotel(r.json(), "2026-01-21")["resolution"] == "no_hotel"
        r = c.post(f"/api/c58/calendar-trips/{t.id}/hotel-night",
                   json={"night": "2026-01-21", "choice": "bogus"})
        assert r.status_code == 400
        r = c.get(f"/api/c58/calendar-trips/{t.id}/hotel-tips?bezala=false")
        assert r.status_code == 200
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- PostgreSQL


def test_postgres_migration_and_choices(pg_url):  # noqa: F811
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.orm import sessionmaker

    from app import db as app_db
    from app import models  # noqa: F401
    from app.db import Base

    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    Base.metadata.create_all(bind=engine)
    # Gammal tabell utan de nya kolumnerna -> migreringen lägger till dem.
    with engine.begin() as c:
        c.execute(text("ALTER TABLE trip_expectations DROP COLUMN resolution"))
        c.execute(text("ALTER TABLE trip_expectations DROP COLUMN resolved_at"))
    with patch.object(app_db, "engine", engine):
        app_db._apply_schema_migrations()
        app_db._apply_schema_migrations()  # idempotent
    cols = {c["name"] for c in inspect(engine).get_columns("trip_expectations")}
    assert {"resolution", "resolved_at"} <= cols
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        t = _seed_130(s)
        t135 = _trip(s, date(2026, 4, 22), date(2026, 4, 23), [
            _flight("AY 961", "HEL", "CPH", "2026-04-22T05:00:00+00:00",
                    "2026-04-22T06:40:00+00:00"),
            _flight("AY 962", "CPH", "HEL", "2026-04-23T17:25:00+00:00",
                    "2026-04-23T19:00:00+00:00"),
        ], dest="Malmö")
        tc.set_hotel_night_choice(s, t.id, "2026-01-21", "invoiced_to_company")
        cl = tc.set_hotel_night_choice(s, t135.id, "2026-04-22", "invoiced_to_company",
                                       "Hotel Terazza")
        assert _hotel(cl, "2026-04-22")["status"] == "found"
        assert tl.get_trip_legs(s, t.id)["admin"]["hotel_nights"] == 2
        _seed_146_147(s)
        _receipt(s, date(2026, 11, 25), "Moovy", 20.0, waiting=False, category="Parkering")
        from app.models import CalendarTrip

        t146 = s.query(CalendarTrip).filter(CalendarTrip.destination == "Stockholm").one()
        view = tl.get_trip_legs(s, t146.id)
        assert sum(1 for leg in view["legs"] if leg["mode"] == "car_phev") == 2
        cl = tc.set_hotel_night_choice(s, t.id, "2026-01-21", "undo")
        assert _hotel(cl, "2026-01-21")["resolution"] is None
    finally:
        s.close()
        with engine.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        engine.dispose()
