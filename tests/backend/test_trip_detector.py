"""Resor R1 — tester för trip_detector (+ store/endpoints).

Kärnfall (enligt uppdraget):
  - flyg → hemresa samma dag
  - flyg → hemresa nästa dag
  - flera resor samma vecka
  - event utan flyg
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "")
os.environ.setdefault("DRIVE_REFRESH_TOKEN", "")
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services.calendar_client import normalize_event  # noqa: E402
from app.services.trip_detector import (  # noqa: E402
    classify,
    detect_trips,
    flight_destination,
)

HEL = timezone(timedelta(hours=2))
STO = timezone(timedelta(hours=1))
HOMES = ("helsinki", "helsingfors", "vantaa", "hel")


def ev(eid, summary, start, end, *, location="", event_type="default"):
    return {
        "id": eid,
        "summary": summary,
        "description": "",
        "location": location,
        "start": start,
        "end": end,
        "all_day": isinstance(start, date) and not isinstance(start, datetime),
        "event_type": event_type,
        "organizer": "",
        "transparency": "opaque",
    }


def flight(eid, dest, start, end, no="AY 801"):
    return ev(eid, f"Flight to {dest} ({no})", start, end, event_type="fromGmail")


# ---------- Klassificering ----------


def test_classify_flight_hotel_meeting():
    d = datetime(2026, 3, 2, 7, 0, tzinfo=HEL)
    assert classify(flight("f", "Stockholm", d, d)) == "flight"
    assert classify(ev("x", "Finnair AY 123 HEL-ARN", d, d)) == "flight"
    assert classify(ev("h", "Stay at Scandic Continental", d, d)) == "hotel"
    assert classify(ev("m", "Q2 review", d, d)) == "meeting"
    # Flygbolagsnamn utan rutt är ett möte, inte ett flyg.
    assert classify(ev("m2", "Finnair kundmöte", d, d)) == "meeting"
    assert classify(ev("m3", "Weekly check-in", d, d)) == "meeting"
    assert classify(ev("b", "Födelsedag", d, d, event_type="birthday")) == "ignored"


def test_flight_destination_variants():
    d = datetime(2026, 3, 2, 7, 0, tzinfo=HEL)
    assert flight_destination(ev("a", "Flight to Stockholm (AY 801)", d, d)) == "Stockholm"
    assert flight_destination(ev("b", "Flyg till Oslo (SK 1234)", d, d)) == "Oslo"
    assert flight_destination(ev("c", "AY 123 Helsinki → Riga", d, d)) == "Riga"
    assert flight_destination(ev("d", "AY 123", d, d)) is None
    # Bara flygplatskod → stadsnamn.
    assert flight_destination(ev("e", "Flight to CPH (AY 959)", d, d)) == "Copenhagen"
    assert flight_destination(ev("f", "Flight to HEL (AY 960)", d, d)) == "Helsinki"


# ---------- Kärnfall ----------


def test_round_trip_same_day():
    events = [
        flight("out", "Stockholm", datetime(2026, 3, 2, 7, 30, tzinfo=HEL),
               datetime(2026, 3, 2, 7, 30, tzinfo=STO)),
        ev("m1", "Kundmöte Visma Sverige", datetime(2026, 3, 2, 10, 0, tzinfo=STO),
           datetime(2026, 3, 2, 12, 0, tzinfo=STO)),
        flight("home", "Helsinki", datetime(2026, 3, 2, 18, 0, tzinfo=STO),
               datetime(2026, 3, 2, 20, 0, tzinfo=HEL), no="AY 806"),
    ]
    res = detect_trips(events, HOMES)
    assert len(res["trips"]) == 1
    trip = res["trips"][0]
    assert trip["start_date"] == trip["end_date"] == "2026-03-02"
    assert trip["destination"] == "Stockholm"
    assert trip["open_return"] is False
    assert len(trip["days"]) == 1
    day = trip["days"][0]
    assert day["start_time"] == "07:30"
    assert day["end_time"] == "20:00"
    assert day["destination"] == "Stockholm"
    assert [m["id"] for m in day["meetings"]] == ["m1"]
    assert day["hotel"] is None
    assert set(day["source_event_ids"]) == {"out", "home", "m1"}
    assert res["ungrouped"] == []


def test_round_trip_next_day_with_hotel():
    events = [
        flight("out", "Stockholm", datetime(2026, 4, 14, 16, 0, tzinfo=HEL),
               datetime(2026, 4, 14, 16, 0, tzinfo=STO)),
        # Gmail-format: end = dagen EFTER utcheckning (utcheckning 15/4).
        ev("hotel", "Stay at Scandic Klara", date(2026, 4, 14), date(2026, 4, 16)),
        ev("m1", "Workshop", datetime(2026, 4, 15, 9, 0, tzinfo=STO),
           datetime(2026, 4, 15, 15, 0, tzinfo=STO)),
        flight("home", "Helsinki", datetime(2026, 4, 15, 18, 0, tzinfo=STO),
               datetime(2026, 4, 15, 20, 5, tzinfo=HEL)),
    ]
    trip = detect_trips(events, HOMES)["trips"][0]
    assert (trip["start_date"], trip["end_date"]) == ("2026-04-14", "2026-04-15")
    d1, d2 = trip["days"]
    assert d1["start_time"] == "16:00" and d1["end_time"] is None
    assert d1["hotel"] == {"id": "hotel", "name": "Scandic Klara"}
    assert d2["start_time"] is None and d2["end_time"] == "20:05"
    assert d2["hotel"] is None  # ingen hotellnatt efter hemresan
    assert [m["summary"] for m in d2["meetings"]] == ["Workshop"]
    assert trip["hotels"] == [{"id": "hotel", "name": "Scandic Klara"}]


def test_multiple_trips_same_week():
    events = [
        flight("a1", "Stockholm", datetime(2026, 5, 4, 7, 0, tzinfo=HEL),
               datetime(2026, 5, 4, 7, 0, tzinfo=STO)),
        flight("a2", "Helsinki", datetime(2026, 5, 5, 17, 0, tzinfo=STO),
               datetime(2026, 5, 5, 19, 0, tzinfo=HEL)),
        flight("b1", "Oslo", datetime(2026, 5, 7, 8, 0, tzinfo=HEL),
               datetime(2026, 5, 7, 9, 0, tzinfo=STO)),
        flight("b2", "Helsinki", datetime(2026, 5, 8, 16, 0, tzinfo=STO),
               datetime(2026, 5, 8, 19, 0, tzinfo=HEL)),
    ]
    res = detect_trips(events, HOMES)
    assert [(t["start_date"], t["end_date"], t["destination"]) for t in res["trips"]] == [
        ("2026-05-04", "2026-05-05", "Stockholm"),
        ("2026-05-07", "2026-05-08", "Oslo"),
    ]
    assert res["stats"]["trips"] == 2


def test_events_without_flights():
    events = [
        ev("m1", "Standup", datetime(2026, 6, 1, 9, 0, tzinfo=HEL),
           datetime(2026, 6, 1, 9, 15, tzinfo=HEL)),
        ev("m2", "Lunch", datetime(2026, 6, 2, 11, 0, tzinfo=HEL),
           datetime(2026, 6, 2, 12, 0, tzinfo=HEL)),
        ev("h1", "Hotel Kämp", date(2026, 6, 3), date(2026, 6, 4)),
    ]
    res = detect_trips(events, HOMES)
    assert res["trips"] == []
    # Hotellet kunde inte kopplas till någon resa → rapporteras.
    assert [(u["id"], u["reason"]) for u in res["ungrouped"]] == [
        ("h1", "hotel_without_flight"),
    ]
    assert res["stats"]["meetings"] == 2


# ---------- Kantfall ----------


def test_return_without_departure_is_ungrouped():
    events = [
        flight("home", "Helsinki", datetime(2026, 1, 3, 18, 0, tzinfo=STO),
               datetime(2026, 1, 3, 20, 0, tzinfo=HEL)),
    ]
    res = detect_trips(events, HOMES)
    assert res["trips"] == []
    assert res["ungrouped"][0]["reason"] == "return_without_departure"


def test_open_return_extends_through_hotel():
    events = [
        flight("out", "London", datetime(2026, 8, 10, 8, 0, tzinfo=HEL),
               datetime(2026, 8, 10, 9, 30, tzinfo=timezone.utc)),
        # Utcheckning 12/8 (end exklusivt = 13/8).
        ev("h", "Stay at Hilton London", date(2026, 8, 10), date(2026, 8, 13)),
    ]
    trip = detect_trips(events, HOMES)["trips"][0]
    assert trip["open_return"] is True
    assert trip["end_date"] == "2026-08-12"
    assert trip["end_at"] is None
    assert len(trip["days"]) == 3
    assert [bool(d["hotel"]) for d in trip["days"]] == [True, True, False]


def test_connection_keeps_single_trip():
    events = [
        flight("o1", "Stockholm", datetime(2026, 9, 1, 7, 0, tzinfo=HEL),
               datetime(2026, 9, 1, 7, 0, tzinfo=STO)),
        flight("o2", "London", datetime(2026, 9, 1, 9, 0, tzinfo=STO),
               datetime(2026, 9, 1, 10, 30, tzinfo=timezone.utc)),
        flight("r", "Helsinki", datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc),
               datetime(2026, 9, 3, 17, 0, tzinfo=HEL)),
    ]
    res = detect_trips(events, HOMES)
    assert len(res["trips"]) == 1
    trip = res["trips"][0]
    assert trip["destination"] == "Stockholm → London"
    assert trip["days"][1]["destination"] == "London"


def test_long_gap_closes_trip_as_open_return():
    events = [
        flight("a", "Stockholm", datetime(2026, 2, 1, 7, 0, tzinfo=HEL),
               datetime(2026, 2, 1, 7, 0, tzinfo=STO)),
        flight("b", "Oslo", datetime(2026, 3, 15, 7, 0, tzinfo=HEL),
               datetime(2026, 3, 15, 8, 0, tzinfo=STO)),
        flight("c", "Helsinki", datetime(2026, 3, 16, 17, 0, tzinfo=STO),
               datetime(2026, 3, 16, 20, 0, tzinfo=HEL)),
    ]
    trips = detect_trips(events, HOMES)["trips"]
    assert [(t["destination"], t["open_return"]) for t in trips] == [
        ("Stockholm", True), ("Oslo", False),
    ]


def test_normalize_event_google_payload():
    raw = {
        "id": "abc",
        "status": "confirmed",
        "summary": "Flight to Stockholm (AY 801)",
        "eventType": "fromGmail",
        "start": {"dateTime": "2026-03-02T07:30:00+02:00"},
        "end": {"dateTime": "2026-03-02T07:30:00+01:00"},
    }
    e = normalize_event(raw)
    assert e["start"].hour == 7 and e["event_type"] == "fromGmail"
    assert normalize_event({**raw, "status": "cancelled"}) is None
    allday = normalize_event({"id": "d", "start": {"date": "2026-03-02"},
                              "end": {"date": "2026-03-03"}})
    assert allday["all_day"] is True


# ---------- Store + endpoints ----------


@pytest.fixture()
def client():
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    from app.db import Base
    from app import models  # noqa: F401
    import app.main as main_module

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    @contextmanager
    def scope():
        s = SessionLocal()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    def _get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app = main_module.app
    # Andra testmoduler byter ut app.db.get_db/session_scope permanent.
    # Overrida därför exakt de dependency-objekt som våra routes använder.
    keys = set()
    for route in app.routes:
        if getattr(route, "path", "") in ("/api/trips/refresh", "/api/trips/suggestions"):
            for dep in route.dependant.dependencies:
                keys.add(dep.call)
    saved = dict(app.dependency_overrides)
    for key in keys:
        name = getattr(key, "__name__", "")
        app.dependency_overrides[key] = (lambda: None) if "auth" in name else _get_db

    class _Db:
        session_scope = staticmethod(scope)

    yield TestClient(app), _Db
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def _same_week_events():
    return [
        flight("a1", "Stockholm", datetime(2026, 5, 4, 7, 0, tzinfo=HEL),
               datetime(2026, 5, 4, 7, 0, tzinfo=STO)),
        flight("a2", "Helsinki", datetime(2026, 5, 5, 17, 0, tzinfo=STO),
               datetime(2026, 5, 5, 19, 0, tzinfo=HEL)),
    ]


def test_refresh_endpoint_persists_and_lists(client):
    c, db_module = client
    from app.models import ProcessedMessage, Trip, TripMessage

    # Ett kvitto kopplat till en kvittoresa som överlappar.
    with db_module.session_scope() as s:
        s.add(ProcessedMessage(message_id="m-1", status="saved", vendor="Finnair",
                               amount=250.0, currency="EUR", receipt_date="2026-05-04"))
        t = Trip(title="Stockholm", start_date=date(2026, 5, 4),
                 end_date=date(2026, 5, 5), status="active")
        s.add(t)
        s.flush()
        s.add(TripMessage(trip_id=t.id, message_id="m-1", added_by="manual"))

    with patch("app.services.calendar_client.fetch_events", return_value=_same_week_events()):
        r = c.post("/api/trips/refresh")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ok"
    assert body["stats"]["trips"] == 1

    r = c.get("/api/trips/suggestions?source=calendar")
    trips = r.json()["trips"]
    assert len(trips) == 1
    assert trips[0]["status"] == "suggested"
    assert [d["date"] for d in trips[0]["days"]] == ["2026-05-04", "2026-05-05"]
    assert [rc["message_id"] for rc in trips[0]["receipts"]] == ["m-1"]

    # Ny refresh ersätter förslagen (inga dubbletter).
    with patch("app.services.calendar_client.fetch_events", return_value=_same_week_events()):
        c.post("/api/trips/refresh")
    assert len(c.get("/api/trips/suggestions?source=calendar").json()["trips"]) == 1

    # Default-källan (kvittoresor) är opåverkad.
    default = c.get("/api/trips/suggestions").json()
    assert "source" not in default


def test_refresh_keeps_approved_trips(client):
    c, db_module = client
    from app.models import CalendarTrip

    with db_module.session_scope() as s:
        s.add(CalendarTrip(start_date=date(2026, 5, 4), end_date=date(2026, 5, 5),
                           status="approved", destination="Stockholm"))
    with patch("app.services.calendar_client.fetch_events", return_value=_same_week_events()):
        body = c.post("/api/trips/refresh").json()
    assert body["saved"]["skipped_locked"] == 1
    trips = c.get("/api/trips/suggestions?source=calendar").json()["trips"]
    assert [t["status"] for t in trips] == ["approved"]


def test_refresh_reports_consent_required(client):
    c, _ = client
    from app.services.calendar_client import CalendarAuthError

    with patch("app.services.calendar_client.fetch_events",
               side_effect=CalendarAuthError("consent_required")):
        body = c.post("/api/trips/refresh").json()
    assert body["status"] == "consent_required"


def test_gmail_oauth_scopes_include_calendar_readonly():
    from app.main import OAUTH_SCOPES
    from app.services.gmail_client import SCOPES as GMAIL_SCOPES

    assert "https://www.googleapis.com/auth/calendar.readonly" in OAUTH_SCOPES["gmail"]
    # Gmail-klienten får INTE begära kalender-scopen vid refresh.
    assert not any("calendar" in s for s in GMAIL_SCOPES)
