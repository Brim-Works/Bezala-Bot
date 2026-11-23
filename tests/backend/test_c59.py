"""C59 - stora bokstäver i syftet, måltider per dag, Arlanda Express-riktning,
hyrbil (Hertz) på resa 135."""

from __future__ import annotations

import os
from datetime import date, datetime

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services import trip_legs as tl  # noqa: E402
from app.services.trip_completeness import classify_receipt, is_car_rental  # noqa: E402
from app.services.trip_english import (  # noqa: E402
    meals_text_sv,
    netvisor_line_description,
    netvisor_purpose,
)
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _receipt  # noqa: E402
from tests.backend.test_c53 import _flight, _trip  # noqa: E402
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("TRIP_CAR_HOME_KM_ONE_WAY", "TRIP_CAR_OUT_ELECTRIC_SHARE",
              "TRIP_CAR_BACK_ELECTRIC_SHARE", "TRIP_CAR_ELECTRIC_KWH_PER_100KM",
              "TRIP_CAR_PETROL_L_PER_100KM_DISCHARGED", "FLIGHT_SHORT_MAX_KM",
              "FLIGHT_LONG_MIN_KM"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- Del 1 syfte


@pytest.mark.parametrize("purpose,expected", [
    ("Agda PS Partner Day", "Agda PS Partner Day"),
    ("HRM SE kick-off", "HRM SE kick-off"),
    ("Board meeting at TooEasy", "board meeting at TooEasy"),
    ("Pricing and BPO workshop", "pricing and BPO workshop"),
    ("Leadership day and Azets meeting", "leadership day and Azets meeting"),
    ("Azets meeting", "Azets meeting"),
    ("Management meeting", "management meeting"),
])
def test_purpose_keeps_proper_names_and_acronyms(purpose, expected):
    assert netvisor_purpose(purpose) == expected


def test_trip_134_description():
    assert netvisor_line_description(["Stockholm"], "Agda PS Partner Day") == (
        "Travel for Agda PS Partner Day in Stockholm")
    assert netvisor_line_description(["Malmö → Ljungby"], "Pricing and BPO workshop") == (
        "Travel for pricing and BPO workshop in Malmo and Ljungby")


def test_second_phrase_keeps_proper_name():
    from app.services.trip_english import generate_purpose

    p = generate_purpose([{"summary": "Management meeting"},
                          {"summary": "Agda PS Kunddag Malmö"}], "Malmö")
    assert "Agda PS" in p and "agda" not in p


# ---------------------------------------------------------------- Del 2 måltider


def test_meals_per_day_trip_133():
    days = [("2026-04-13", 1, ["lunch_calendar"], "SE"),
            ("2026-04-14", 1, ["lunch_customer_day"], "SE")]
    text = meals_text_sv(days)
    assert text == "gratis lunch 13 apr, 14 apr"
    assert "2 gratis" not in text


def test_meals_per_day_trip_79():
    days = [("2026-05-07", 2, ["lunch_calendar", "dinner_kickoff"], "SE"),
            ("2026-05-08", 1, ["lunch_calendar"], "SE")]
    assert meals_text_sv(days) == "2 gratis måltider 7 maj; gratis lunch 8 maj"


def test_meals_none():
    assert meals_text_sv([("2026-04-13", 0, [], "SE")]) is None


def test_decision_card_shows_meals_per_day(db):  # noqa: F811
    from app.services.decision_queue import list_pending
    from tests.backend.test_c49 import _mk

    _mk(db, 133, date(2026, 4, 13), date(2026, 4, 14),
        [{"free_meals": 1, "sources": ["lunch_calendar"]},
         {"free_meals": 1, "sources": ["lunch_calendar"]}],
        destination="Malmö", purpose="Workshop", status="suggested")
    card = next(i for i in list_pending(db, limit=50)["items"] if i["id"] == "trip:133")
    assert "gratis lunch 13 apr, 14 apr" in card["subtitle"]
    assert "free meal" not in card["subtitle"]


# ---------------------------------------------------------------- Del 3 Arlanda


def _r(db, day, vendor, amount, currency, file_name, subject="Receipt", **kw):  # noqa: F811
    m = _receipt(db, day, vendor, amount, currency, waiting=False,
                 category=kw.pop("category", "Kollektivtrafik"), subject=subject, **kw)
    m.file_name = file_name
    db.commit()
    return m


def _trip_134(db):  # noqa: F811
    return _trip(db, date(2026, 4, 16), date(2026, 4, 16), [
        _flight("AY 801", "HEL", "ARN", "2026-04-16T03:45:00+00:00", "2026-04-16T04:45:00+00:00"),
        _flight("AY 812", "ARN", "HEL", "2026-04-16T11:55:00+00:00", "2026-04-16T12:55:00+00:00"),
    ], dest="Stockholm")


def test_arlanda_express_direction_from_purchase_time(db):  # noqa: F811
    """Prod resa 134: biljetten säger ingen riktning; filnamnet (AI) säger
    Stockholm-Arlanda för båda. Köpt 07:04 (efter landning) = Arlanda -> City,
    köpt 12:36 (före hemflyget) = City -> Arlanda."""
    t = _trip_134(db)
    _r(db, date(2026, 4, 16), "Arlanda Express", 100.0, "SEK",
       "20260416 Arlanda Express Enkelresa Stockholm-Arlanda.pdf",
       "Arlanda express biljett och kvitto - Ordernummer 7307781",
       message_id="19d95dcddfd6b740")  # Gmail-id = 10:36 UTC
    _r(db, date(2026, 4, 16), "Arlanda Express", 100.0, "SEK",
       "20260416 Arlanda Express Enkelresa vuxen.pdf",
       "Arlanda express biljett och kvitto - Ordernummer 7305162",
       summary="A-Train AB, Box 130, 101 22 Stockholm; giltig till och från Arlanda",
       message_id="19d94ad5a19ff37f")  # Gmail-id = 05:04 UTC
    view = tl.get_trip_legs(db, t.id)
    trains = sorted((x for x in view["legs"] if x["mode"] == "train"),
                    key=lambda x: x["receipt_message_id"])
    assert [(x["from"], x["to"]) for x in trains] == [
        ("Arlanda Airport", "Stockholm C"), ("Stockholm C", "Arlanda Airport")]
    assert all(x["km"] == 39 and not x["excluded"] for x in trains)
    assert view["admin"]["train_km"] == 78  # inte dubbelt
    assert all("purchase time" in x["note"] for x in trains)
    assert not any("check direction" in x["note"] for x in trains)


def test_bought_at_uses_received_at_when_not_gmail_id():
    class R:
        message_id = "manual:abc"
        received_at = datetime(2026, 4, 16, 5, 4)

    assert tl._bought_at(R()).hour == 5


def test_time_direction_unknown_before_landing():
    flights = [{"arr_at": "2026-04-16T04:45:00+00:00"}, {"dep_at": "2026-04-16T11:55:00+00:00"}]
    assert tl._time_direction(datetime(2026, 4, 15, 20, 0), "Arlanda Airport", "Stockholm C",
                              flights) == (None, None)


# ---------------------------------------------------------------- Del 4 hyrbil


HERTZ_SUMMARY = ("Hertz car rental Malmö (Volvo EX30, 376 km), 22-23 April 2026. "
                 "Car type: VOLVO EX30 AUT 69KWH. Delivery: MALMÖ DT/KIOSK "
                 "Km out: 19839 Return: MALMÖ DT/KIOSK Km in: 20215")


@pytest.mark.parametrize("vendor", ["Hertz", "Avis Budget", "Sixt", "Europcar"])
def test_rental_vendors_are_not_local_transport(vendor):
    text = f"{vendor} rental receipt malmo".lower()
    assert is_car_rental(None, text)
    assert classify_receipt("Bilhyra", text) == {"car_rental"}
    assert classify_receipt(None, text) == {"car_rental"}
    assert classify_receipt("Kollektivtrafik", "hyrbil malmö") == {"car_rental"}
    assert "local" in classify_receipt("Kollektivtrafik", "skanetrafiken")


def test_rental_km_from_odometer():
    assert tl.rental_km(HERTZ_SUMMARY) == 376
    assert tl.rental_km("Mileage: 376 km (unlimited km)") == 376
    assert tl.rental_km("Hertz Malmö") is None
    assert tl.is_electric_rental(HERTZ_SUMMARY)


def _trip_135(db):  # noqa: F811
    from app.models import CalendarTripDay

    t = _trip(db, date(2026, 4, 22), date(2026, 4, 23), [
        _flight("AY 961", "HEL", "CPH", "2026-04-22T15:00:00+00:00", "2026-04-22T16:40:00+00:00"),
        _flight("AY 962", "CPH", "HEL", "2026-04-23T17:25:00+00:00", "2026-04-23T19:00:00+00:00"),
    ], dest="Malmö → Ljungby")
    for d, city in ((date(2026, 4, 22), "Malmö"), (date(2026, 4, 23), "Ljungby")):
        db.add(CalendarTripDay(trip_id=t.id, day_date=d, destination=city, airport="CPH",
                               meetings=[], source_event_ids=[], country="SE"))
    db.commit()
    _r(db, date(2026, 4, 22), "Skånetrafiken", 300.0, "SEK",
       "20260422 Skanetrafiken Oresundszon CPH-Malmo.pdf", "Tack för ditt köp")
    _r(db, date(2026, 4, 22), "Hertz", 1145.0, "SEK", "20260422 Hertz Bilhyra Malmo.pdf",
       "INVOICE SPECIFICATION 52607241669", category="Bilhyra", summary=HERTZ_SUMMARY)
    return t


def test_hertz_gives_car_leg_not_train(db):  # noqa: F811
    t = _trip_135(db)
    view = tl.get_trip_legs(db, t.id)
    cars = [x for x in view["legs"] if x["mode"] == "car_private"]
    trains = [x for x in view["legs"] if x["mode"] == "train"]
    assert len(cars) == 1
    car = cars[0]
    assert (car["from"], car["to"], car["km"]) == ("Malmo", "Ljungby - Malmo", 376)
    assert car["source"] == "receipt" and not car["is_estimate"]
    assert car["energy_type"] == "electricity" and car["energy_amount"] is None
    assert car["admin_category"] == "non_company_car_km"
    assert not car["company_car_info_only"]
    # Tåget CPH -> Malmö är kvar, Hertz blev inget tågben.
    assert [(x["from"], x["to"]) for x in trains] == [("Copenhagen Airport", "Malmo C")]
    assert view["admin"]["non_company_car_km"] == 376
    assert view["admin"]["train_km"] == 30.4
    q = [x for x in view["questions"] if x["kind"] == "kwh_missing"]
    assert len(q) == 1 and "kWh saknas, fråga Mikko" in q[0]["text_sv"]
    # Ingen dubbelräkning vid ny synk.
    again = tl.get_trip_legs(db, t.id)
    assert again["admin"]["non_company_car_km"] == 376


def test_rental_leg_does_not_block_home_company_car_legs(db):  # noqa: F811
    from app.models import TripLeg

    t = _trip_135(db)
    tl.get_trip_legs(db, t.id)
    assert db.query(TripLeg).filter(TripLeg.leg_key.like("rental:%")).count() == 1
    assert not tl._own_car_legs(db, t)


def test_checklist_hertz_is_rental_not_local(db):  # noqa: F811
    from app.services.trip_completeness import get_trip_checklist

    t = _trip_135(db)
    cl = get_trip_checklist(db, t.id)
    local = [i for i in cl["items"] if i["kind"] == "local"]
    rental = [i for i in cl["items"] if i["kind"] == "car_rental"]
    assert all((i.get("receipt") or {}).get("vendor") != "Hertz" for i in local)
    assert len(rental) == 1 and rental[0]["receipt"]["vendor"] == "Hertz"
    assert rental[0]["status"] == "found"


# ---------------------------------------------------------------- PostgreSQL


def test_postgres_rental_leg_and_arlanda_direction(pg_url):  # noqa: F811
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.models_netvisor  # noqa: F401
    from app import models  # noqa: F401
    from app.db import Base

    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        t = _trip_135(s)
        view = tl.get_trip_legs(s, t.id)
        cars = [x for x in view["legs"] if x["mode"] == "car_private"]
        assert len(cars) == 1 and cars[0]["km"] == 376
        assert view["admin"]["non_company_car_km"] == 376
        assert tl.get_trip_legs(s, t.id)["admin"]["non_company_car_km"] == 376
        t134 = _trip_134(s)
        _r(s, date(2026, 4, 16), "Arlanda Express", 100.0, "SEK",
           "20260416 Arlanda Express Enkelresa Stockholm-Arlanda.pdf",
           "Ordernummer 7307781", message_id="19d95dcddfd6b740")
        _r(s, date(2026, 4, 16), "Arlanda Express", 100.0, "SEK",
           "20260416 Arlanda Express Enkelresa vuxen.pdf",
           "Ordernummer 7305162", message_id="19d94ad5a19ff37f")
        trains = sorted((x for x in tl.get_trip_legs(s, t134.id)["legs"]
                         if x["mode"] == "train"), key=lambda x: x["receipt_message_id"])
        assert [x["from"] for x in trains] == ["Arlanda Airport", "Stockholm C"]
    finally:
        s.close()
        with engine.begin() as c:
            c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        engine.dispose()
