"""C42 — fullständighetskontroll (resor + kortbetalningar)."""

from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from tests.backend.test_mcp import _call, _full_flow, env  # noqa: E402,F401


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import models  # noqa: F401
    from app.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        yield s
    finally:
        s.close()


def _trip(s, start, end, *, status="suggested", flight_nos=("AY 951", "AY 954"),
          origin="HEL", airport="CPH", destination="Malmö", hotels=None):
    from app.models import CalendarTrip, CalendarTripDay

    t = CalendarTrip(
        start_date=start, end_date=end, destination=destination, country="SE",
        status=status, source_event_ids=[], hotels=[],
        flights=[{"id": f"f{i}", "flight_no": no, "origin": origin if i == 0 else airport,
                  "destination_iata": airport if i == 0 else "HEL"}
                 for i, no in enumerate(flight_nos)],
    )
    s.add(t)
    s.flush()
    d = start
    while d <= end:
        s.add(CalendarTripDay(
            trip_id=t.id, day_date=d, destination=destination, airport=airport,
            meetings=[], source_event_ids=[],
            hotel={"name": hotels[d]} if hotels and d in hotels else None,
        ))
        d += timedelta(days=1)
    s.commit()
    return t


_seq = iter(range(1, 10_000))


def _receipt(s, day, *, category, vendor, subject="", summary="", **kw):
    from app.models import ProcessedMessage

    m = ProcessedMessage(
        message_id=f"msg-{next(_seq)}", status="saved", vendor=vendor,
        category=category, receipt_date=day.isoformat(), subject=subject,
        summary=summary, amount=kw.pop("amount", 100.0), currency="EUR",
        drive_link=f"https://drive/{vendor}", **kw,
    )
    s.add(m)
    s.commit()
    return m


def _items(cl, kind=None):
    return [i for i in cl["items"] if kind is None or i["kind"] == kind]


def _cl(s, trip):
    from app.services.trip_completeness import checklist

    out = checklist(s, trip)
    s.commit()
    return out


# ---------- Del 1: förväntningar ----------


def test_day_trip_hotel_not_applicable(db):
    t = _trip(db, date(2026, 11, 18), date(2026, 11, 18), airport="GOT",
              destination="Gothenburg")
    hotels = _items(_cl(db, t), "hotel")
    assert len(hotels) == 1
    assert hotels[0]["status"] == "not_applicable"
    assert hotels[0]["set_by"] == "auto"


def test_two_nights_two_hotel_items(db):
    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4))
    hotels = _items(_cl(db, t), "hotel")
    assert [h["slot"] for h in hotels] == ["2026-03-02", "2026-03-03"]
    assert {h["status"] for h in hotels} == {"missing"}


def test_one_hotel_receipt_covers_whole_stay(db):
    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4))
    m = _receipt(db, date(2026, 3, 4), category="Hotell", vendor="Scandic Malmö City")
    hotels = _items(_cl(db, t), "hotel")
    assert {h["status"] for h in hotels} == {"found"}
    assert hotels[0]["receipt"]["message_id"] == m.message_id
    assert hotels[0]["receipt"]["drive_link"] == "https://drive/Scandic Malmö City"


def test_two_hotels_only_one_receipt(db):
    """Kalendern visar två hotell — kvitto bara för det första."""
    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4), hotels={
        date(2026, 3, 2): "Stay at Clarion Hotel Malmö Live",
        date(2026, 3, 3): "Stay at Elite Hotel Mollberg",
    })
    _receipt(db, date(2026, 3, 3), category="Hotell", vendor="Clarion Hotel Malmö Live")
    hotels = _items(_cl(db, t), "hotel")
    assert [h["status"] for h in hotels] == ["found", "missing"]
    assert "Mollberg" in hotels[1]["note"]


def test_late_receipt_moves_missing_to_found(db):
    t = _trip(db, date(2026, 3, 2), date(2026, 3, 3))
    cl = _cl(db, t)
    assert _items(cl, "flight")[0]["status"] == "missing"
    assert _items(cl, "parking")[0]["status"] == "missing"
    assert cl["completeness"] == {"status": "incomplete", "missing": 3,
                                  "missing_items": 3, "card_lines_without_receipt": 0,
                                  "missing_kinds": ["flight", "hotel 2026-03-02", "parking"],
                                  "waiting": 0,
                                  "missing_receipts": [
                                      {"kind": "flight", "slot": None},
                                      {"kind": "hotel", "slot": "2026-03-02"},
                                      {"kind": "parking", "slot": None}]}

    # Flygkvitto köpt en månad innan, nämner flightnumret.
    _receipt(db, date(2026, 2, 1), category="Flyg", vendor="Finnair",
             summary="E-ticket AY0951 HEL-CPH 02MAR, AY 954 CPH-HEL")
    _receipt(db, date(2026, 3, 3), category="Parkering", vendor="Moovy",
             subject="Kuitti pysäköinnistä P5")
    _receipt(db, date(2026, 3, 3), category="Hotell", vendor="Scandic")
    cl = _cl(db, t)
    assert [i["status"] for i in cl["items"] if i["kind"] != "local"] == ["found"] * 3
    assert cl["completeness"]["status"] == "complete"


def test_flight_receipt_far_before_without_reference_not_matched(db):
    t = _trip(db, date(2026, 3, 2), date(2026, 3, 3))
    _receipt(db, date(2026, 1, 10), category="Flyg", vendor="Finnair",
             summary="AY 123 HEL-OSL")
    assert _items(_cl(db, t), "flight")[0]["status"] == "missing"


def test_parking_receipt_of_other_trip_not_used(db):
    t = _trip(db, date(2026, 3, 9), date(2026, 3, 10))
    _receipt(db, date(2026, 3, 3), category="Parkering", vendor="Moovy")
    assert _items(_cl(db, t), "parking")[0]["status"] == "missing"


def test_parking_not_applicable_when_not_from_hel(db):
    t = _trip(db, date(2026, 3, 9), date(2026, 3, 10), origin="TKU")
    assert _items(_cl(db, t), "parking")[0]["status"] == "not_applicable"


def test_local_transport_optional_when_airport_not_work_city(db):
    t = _trip(db, date(2026, 3, 2), date(2026, 3, 3))  # CPH → Malmö
    local = _items(_cl(db, t), "local")
    assert [(i["slot"], i["status"]) for i in local] == [("CPH", "optional")]
    _receipt(db, date(2026, 3, 2), category="Kollektivtrafik", vendor="Skånetrafiken")
    cl = _cl(db, t)
    assert _items(cl, "local")[0]["status"] == "found"

    # Arbetsort = flygplatsstad → ingen lokalpost.
    t2 = _trip(db, date(2026, 5, 4), date(2026, 5, 4), airport="GOT",
               destination="Gothenburg")
    assert _items(_cl(db, t2), "local") == []


def test_optional_does_not_make_trip_incomplete(db):
    t = _trip(db, date(2026, 3, 2), date(2026, 3, 2))
    _receipt(db, date(2026, 3, 2), category="Flyg", vendor="Finnair")
    _receipt(db, date(2026, 3, 2), category="Parkering", vendor="Moovy")
    cl = _cl(db, t)
    assert _items(cl, "local")[0]["status"] == "optional"
    assert cl["completeness"]["status"] == "complete"


# ---------- Mikkos markeringar ----------


def test_not_applicable_survives_recalculation(db):
    from app.services.trip_completeness import set_expectation_status

    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4))
    out = set_expectation_status(db, t.id, "hotel", "not_applicable",
                                 note="Stayed with friends", date_="2026-03-03")
    by_slot = {h["slot"]: h for h in _items(out, "hotel")}
    assert by_slot["2026-03-03"]["status"] == "not_applicable"
    assert by_slot["2026-03-03"]["set_by"] == "user"
    assert by_slot["2026-03-02"]["status"] == "missing"

    set_expectation_status(db, t.id, "parking", "not_applicable", note="Took the train")
    # Kvitton tillkommer → omräkning; Mikkos markeringar står kvar.
    _receipt(db, date(2026, 3, 4), category="Hotell", vendor="Scandic")
    _receipt(db, date(2026, 3, 4), category="Parkering", vendor="Moovy")
    for _ in range(2):
        cl = _cl(db, t)
    by_slot = {h["slot"]: h for h in _items(cl, "hotel")}
    assert by_slot["2026-03-03"]["status"] == "not_applicable"
    assert by_slot["2026-03-02"]["status"] == "found"
    parking = _items(cl, "parking")[0]
    assert (parking["status"], parking["set_by"], parking["note"]) == (
        "not_applicable", "user", "Took the train")

    # 'auto' tar bort markeringen.
    out = set_expectation_status(db, t.id, "parking", "auto")
    assert _items(out, "parking")[0]["status"] == "found"


def test_not_applicable_survives_calendar_refresh(db):
    """Förslag får nya id:n vid refresh — markeringen följer med."""
    from app.models import CalendarTrip
    from app.services.calendar_trip_store import persist_detected
    from app.services.trip_completeness import set_expectation_status

    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4))
    set_expectation_status(db, t.id, "hotel", "not_applicable", date_="2026-03-02")
    detected = {"trips": [{
        "start_date": "2026-03-02", "end_date": "2026-03-04", "destination": "Malmö",
        "flights": [{"flight_no": "AY 951", "origin": "HEL"}],
        "days": [{"date": f"2026-03-0{i}", "airport": "CPH", "destination": "Malmö"}
                 for i in (2, 3, 4)],
    }]}
    saved = persist_detected(db, detected)
    assert saved["restored_marks"] == 1
    new = db.query(CalendarTrip).one()
    by_slot = {h["slot"]: h for h in _items(_cl(db, new), "hotel")}
    assert by_slot["2026-03-02"]["status"] == "not_applicable"
    assert by_slot["2026-03-03"]["status"] == "missing"


def test_set_expectation_status_validation(db):
    from app.services.calendar_trip_edit import TripEditError
    from app.services.trip_completeness import set_expectation_status

    t = _trip(db, date(2026, 3, 2), date(2026, 3, 3))
    with pytest.raises(TripEditError, match="Unknown kind"):
        set_expectation_status(db, t.id, "food", "found")
    with pytest.raises(TripEditError, match="Unknown status"):
        set_expectation_status(db, t.id, "hotel", "missing")
    with pytest.raises(TripEditError, match="No hotel night"):
        set_expectation_status(db, t.id, "hotel", "found", date_="2026-03-03")
    with pytest.raises(TripEditError, match="not found"):
        set_expectation_status(db, 999, "hotel", "found")


def test_shortened_trip_drops_stale_nights(db):
    from app.services.calendar_trip_edit import remove_trip_day
    from app.services.trip_completeness import set_expectation_status

    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4))
    set_expectation_status(db, t.id, "hotel", "not_applicable", date_="2026-03-03")
    remove_trip_day(db, t.id, "2026-03-04")
    db.refresh(t)
    hotels = _items(_cl(db, t), "hotel")
    assert [h["slot"] for h in hotels] == ["2026-03-02"]


# ---------- Del 2: kortrader ----------


def _bezala_rows():
    return [
        {"bill_line_id": 2000101, "description": "MIKKO KEINONEN: MOOVY OY, HELSINKI, FI 42.00 EUR",
         "date": "2026-03-03"},
        {"bill_line_id": 2000102, "description": "MIKKO KEINONEN: PRESSBYRAN MALMO C, MALMO, SE 8.50 EUR",
         "date": "2026-03-02"},
        {"bill_line_id": 2000103, "description": "MIKKO KEINONEN: LOVABLE, DOVER, US 100.00 EUR",
         "date": "2026-02-10"},
    ]


def test_card_lines_sync_age_source_and_trip_link(db):
    from app.services.trip_completeness import list_card_lines, sync_card_lines

    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4))
    _receipt(db, date(2026, 1, 5), category="Mjukvara", vendor="Lovable",
             sender="Lovable <billing@lovable.dev>")
    res = sync_card_lines(db, _bezala_rows())
    assert res == {"in_bezala": 3, "added": 3, "resolved": 0}

    with patch("app.services.trip_completeness._today", return_value=date(2026, 3, 10)):
        rows = {r["bill_line_id"]: r for r in list_card_lines(db)}
        cl = _cl(db, t)
    moovy = rows["2000101"]
    assert (moovy["vendor"], moovy["amount"], moovy["age_days"]) == ("MOOVY OY", 42.0, 7)
    assert moovy["trip_id"] == t.id
    assert moovy["suggested_source"]["source"] == "gmail_filter"
    assert moovy["suggested_source"]["sender"] == "noreply@moovy.fi"
    assert rows["2000103"]["trip_id"] is None
    assert rows["2000103"]["suggested_source"]["source"] == "unknown"
    assert "billing@lovable.dev" in rows["2000103"]["suggested_source"]["hint"]
    assert rows["2000102"]["suggested_source"] == {"source": "unknown", "sender": None,
                                                  "hint": None}

    # Kortrader inom resan syns i resans checklista och räknas som saknade.
    assert [c["bill_line_id"] for c in cl["card_lines"]] == ["2000102", "2000101"]
    assert cl["completeness"]["card_lines_without_receipt"] == 2


def test_link_fetch_source(db):
    from app.services.trip_completeness import list_card_lines, sync_card_lines

    sync_card_lines(db, [{"bill_line_id": 7, "date": "2026-03-01",
                          "description": "MIKKO KEINONEN: ARLANDA EXPRESS, STOCKHOLM, SE 30.00 EUR"}])
    row = list_card_lines(db)[0]
    assert row["suggested_source"] == {"source": "link_fetch",
                                      "sender": "noreply@arlandaexpress.se", "hint": None}


def test_receipt_not_available_and_resolution(db):
    from app.services.trip_completeness import (
        list_card_lines, set_card_line_status, sync_card_lines,
    )

    t = _trip(db, date(2026, 3, 2), date(2026, 3, 4))
    sync_card_lines(db, _bezala_rows())
    out = set_card_line_status(db, "2000102", "receipt_not_available", note="Kiosk, no receipt")
    assert out["status"] == "receipt_not_available"
    assert {r["bill_line_id"] for r in list_card_lines(db)} == {"2000101", "2000103"}
    cl = _cl(db, t)
    assert cl["completeness"]["card_lines_without_receipt"] == 1

    # Moovy-raden fick kvitto i Bezala → försvinner ur listan → resolved.
    res = sync_card_lines(db, [r for r in _bezala_rows() if r["bill_line_id"] != 2000101])
    assert res["resolved"] == 1
    assert {r["bill_line_id"] for r in list_card_lines(db)} == {"2000103"}
    # Mikkos receipt_not_available står kvar efter synk.
    from app.models import CardLine
    assert db.query(CardLine).filter_by(bill_line_id="2000102").one().status == \
        "receipt_not_available"


def test_list_missing(db):
    from app.services.trip_completeness import list_missing, sync_card_lines

    old = _trip(db, date(2026, 3, 2), date(2026, 3, 3))
    _trip(db, date(2026, 3, 30), date(2026, 3, 31))  # slutade för nyss
    _receipt(db, date(2026, 3, 3), category="Parkering", vendor="Moovy")
    sync_card_lines(db, _bezala_rows())
    with patch("app.services.trip_completeness._today", return_value=date(2026, 4, 2)):
        out = list_missing(db, older_than_days=5, sync=False)
    kinds = sorted((i["trip_id"], i["kind"], i["slot"]) for i in out["trip_items_missing"])
    assert kinds == [(old.id, "flight", None), (old.id, "hotel", "2026-03-02")]
    assert {k: out["counts"][k] for k in ("trip_items", "card_lines")} == {
        "trip_items": 2, "card_lines": 3}
    assert out["card_lines_without_receipt"][0]["bill_line_id"] == "2000103"  # äldst först


# ---------- MCP + list_trips ----------


def test_mcp_tools_and_completeness(env):
    c, scope, Session = env
    at = _full_flow(c)["access_token"]
    with scope() as s:
        trip_id = _trip(s, date(2026, 3, 2), date(2026, 3, 3)).id
        _receipt(s, date(2026, 3, 3), category="Hotell", vendor="Scandic")

    trips = _call(c, at, "list_trips")["data"]["trips"]
    assert trips[0]["completeness"] == {"status": "incomplete", "missing": 2,
                                        "missing_items": 2,
                                        "card_lines_without_receipt": 0,
                                        "missing_kinds": ["flight", "parking"],
                                        "waiting": 0,
                                        "missing_receipts": [
                                            {"kind": "flight", "slot": None},
                                            {"kind": "parking", "slot": None}]}

    cl = _call(c, at, "get_trip_checklist", trip_id=trip_id)["data"]
    assert [(i["kind"], i["status"]) for i in cl["items"]] == [
        ("flight", "missing"), ("hotel", "found"), ("parking", "missing"),
        ("local", "optional"),
    ]
    cl = _call(c, at, "set_expectation_status", trip_id=trip_id, kind="parking",
               status="not_applicable", note="Taxi to airport")["data"]
    assert cl["completeness"]["missing"] == 1

    res = _call(c, at, "set_expectation_status", trip_id=trip_id, kind="hotel",
                status="bogus")
    assert res["isError"] is True

    with patch("app.services.bezala_client.BezalaClient.list_missing_receipts",
               return_value=_bezala_rows()), \
         patch("app.services.bezala_client.BezalaClient.__init__", return_value=None), \
         patch("app.services.bezala_client.BezalaClient.close", return_value=None), \
         patch("app.services.trip_completeness._today", return_value=date(2026, 3, 20)):
        out = _call(c, at, "list_missing", older_than_days=5)["data"]
    assert out["bezala_sync"]["status"] == "ok"
    assert {k: out["counts"][k] for k in ("trip_items", "card_lines")} == {
        "trip_items": 1, "card_lines": 3}

    res = _call(c, at, "set_card_line_status", bill_line_id="2000103",
                status="receipt_not_available")
    assert res["data"]["status"] == "receipt_not_available"


def test_list_missing_never_writes_to_bezala(env):
    """Hårdregel: bara läsning (list_missing_receipts) mot Bezala."""
    c, scope, _ = env
    at = _full_flow(c)["access_token"]
    with scope() as s:
        _trip(s, date(2026, 3, 2), date(2026, 3, 3))

    from app.services import bezala_client as bc

    calls = []

    def fake_request(self, method, path, *a, **kw):
        calls.append((method, path))
        raise AssertionError("unexpected Bezala request")

    with patch.object(bc.BezalaClient, "__init__", return_value=None), \
         patch.object(bc.BezalaClient, "close", return_value=None), \
         patch.object(bc.BezalaClient, "list_missing_receipts", return_value=[]), \
         patch.object(bc.BezalaClient, "_request", fake_request):
        out = _call(c, at, "list_missing")["data"]
    assert out["bezala_sync"]["status"] == "ok"
    assert calls == []


def test_ui_endpoint_has_checklist(env):
    c, scope, _ = env
    with scope() as s:
        _trip(s, date(2026, 3, 2), date(2026, 3, 2))
    r = c.post("/login", data={"password": "test-password", "next": "/"})
    assert r.status_code == 303
    r = c.get("/api/trips/suggestions", params={"source": "calendar"})
    assert r.status_code == 200, r.text
    trip = r.json()["trips"][0]
    assert trip["completeness"]["status"] == "incomplete"
    assert [i["kind"] for i in trip["checklist"]["items"]] == [
        "flight", "hotel", "parking", "local"]


def test_return_after_midnight_is_day_trip(db):
    """Landar 23:35, hemma 00:35 dagen efter — ingen hotellnatt."""
    t = _trip(db, date(2026, 5, 26), date(2026, 5, 27))
    t.end_at = "2026-05-26T23:35:00+03:00"
    t.travel_end_at = "2026-05-27T00:35:00+03:00"
    db.commit()
    hotels = _items(_cl(db, t), "hotel")
    assert [(h["slot"], h["status"]) for h in hotels] == [(None, "not_applicable")]

    # Utan landningstid: hemkomst 00:17 räknas till dagen innan.
    t2 = _trip(db, date(2026, 10, 13), date(2026, 10, 14))
    t2.travel_end_at = "2026-10-14T00:17:00+03:00"
    db.commit()
    assert _items(_cl(db, t2), "hotel")[0]["status"] == "not_applicable"

    # Riktig övernattning påverkas inte.
    t3 = _trip(db, date(2026, 3, 10), date(2026, 3, 11))
    t3.end_at = "2026-03-11T19:35:00+02:00"
    t3.travel_end_at = "2026-03-11T20:35:00+02:00"
    db.commit()
    assert [h["slot"] for h in _items(_cl(db, t3), "hotel")] == ["2026-03-10"]
