"""C49 - traktamente till Netvisor: status, underlag, payload, avstängd klient."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import httpx  # noqa: E402
import pytest  # noqa: E402

import app.models_netvisor  # noqa: E402,F401
from tests.backend.test_c42 import db  # noqa: E402,F401

FLIGHTS = [
    {"id": "out", "flight_no": "AY 951", "origin": "HEL", "destination_iata": "CPH"},
    {"id": "home", "flight_no": "AY 960", "origin": "CPH", "destination_iata": "HEL"},
]


def _mk(s, trip_id, start, end, days, *, destination, purpose, country="SE",
        status="approved", travel_start=None, travel_end=None, flights=FLIGHTS):
    """days: list of dicts (destination, free_meals, hotel, country)."""
    from datetime import timedelta

    from app.models import CalendarTrip, CalendarTripDay

    t = CalendarTrip(
        id=trip_id, start_date=start, end_date=end, destination=destination,
        country=country, purpose=purpose, status=status, source_event_ids=[],
        hotels=[], flights=list(flights), travel_start_at=travel_start,
        travel_end_at=travel_end,
    )
    s.add(t)
    s.flush()
    for i, d in enumerate(days):
        s.add(CalendarTripDay(
            trip_id=trip_id, day_date=start + timedelta(days=i),
            destination=d.get("destination", destination), meetings=[],
            source_event_ids=[], free_meals=d.get("free_meals"),
            free_meal_sources=d.get("sources") or [],
            hotel={"name": d["hotel"]} if d.get("hotel") else None,
            country=d.get("country", country),
        ))
    s.commit()
    return t


def trip_93(s, status="edited"):
    return _mk(
        s, 93, date(2026, 1, 13), date(2026, 1, 14),
        [{"hotel": "Clarion Hotel Sea U"}, {}],
        destination="Helsingborg",
        purpose="Team syncs and management meeting, Helsingborg office",
        status=status,
        travel_start="2026-01-13T05:07:00+02:00", travel_end="2026-01-14T23:05:00+02:00",
    )


def trip_79(s, status="approved"):
    return _mk(
        s, 79, date(2026, 5, 7), date(2026, 5, 8),
        [{"free_meals": 2}, {"free_meals": 1}],
        destination="Båstad", purpose="HRM SE kick-off, Båstad", status=status,
        travel_start="2026-05-07T04:39:00+03:00", travel_end="2026-05-08T20:59:00+03:00",
    )


def trip_130(s):
    return _mk(
        s, 130, date(2026, 1, 20), date(2026, 1, 22),
        [{"destination": "Ängelholm", "free_meals": 0, "hotel": "Hotel A"},
         {"destination": "Ljungby", "free_meals": 0, "hotel": "Riverside Hotel"},
         {"destination": "Ljungby", "free_meals": 0}],
        destination="Ängelholm",
        purpose="HRM SE leadership meeting and office meetings, Ljungby",
        travel_start="2026-01-20T16:10:00+02:00", travel_end="2026-01-22T22:55:00+02:00",
    )


def trip_domestic(s, trip_id=200, end_hour="18:30"):
    return _mk(
        s, trip_id, date(2026, 3, 10), date(2026, 3, 10), [{"destination": "Oulu"}],
        destination="Oulu", country="FI", purpose="Customer day Agda PS, Oulu",
        travel_start="2026-03-10T06:00:00+02:00",
        travel_end=f"2026-03-10T{end_hour}:00+02:00",
    )


# ------------------------------------------------------------ Del 1: status


def test_status_table_and_seed_93(db):
    from app.services.netvisor_status import list_status, seed_once

    trip_93(db)
    trip_79(db)
    rows = list_status(db, 2026)
    assert [r["trip_id"] for r in rows] == [93, 79]
    r93 = rows[0]
    assert r93["netvisor_status"] == "created_manual"
    assert r93["netvisor_report_id"] == "1655"
    assert r93["note"] == "Test report, awaiting Riikka's review"
    assert rows[1]["netvisor_status"] == "not_created"
    assert seed_once(db) is False  # idempotent


def test_seed_skips_when_trip_missing_or_other(db):
    from app.models_netvisor import NetvisorReport
    from app.services.netvisor_status import seed_once

    assert seed_once(db) is False
    _mk(db, 93, date(2026, 2, 1), date(2026, 2, 2), [{}, {}], destination="Malmö",
        purpose="x")
    assert seed_once(db) is False
    assert db.query(NetvisorReport).count() == 0


def test_list_status_only_approved_and_edited(db):
    from app.services.netvisor_status import list_status

    trip_79(db)
    _mk(db, 5, date(2026, 2, 1), date(2026, 2, 2), [{}, {}], destination="Malmö",
        purpose="x", status="suggested")
    _mk(db, 6, date(2025, 2, 1), date(2025, 2, 2), [{}, {}], destination="Malmö",
        purpose="x", status="approved")
    assert [r["trip_id"] for r in list_status(db, 2026)] == [79]


def test_set_status_one_row_per_trip(db):
    from app.models_netvisor import NetvisorReport
    from app.services.calendar_trip_edit import TripEditError
    from app.services.netvisor_status import is_created, set_status

    trip_79(db)
    set_status(db, 79, "created_manual", "2000", "by hand")
    out = set_status(db, 79, "not_created")
    assert out["status"] == "not_created"
    assert db.query(NetvisorReport).filter_by(trip_id=79).count() == 1
    assert not is_created(db, 79)
    with pytest.raises(TripEditError):
        set_status(db, 79, "sent")
    with pytest.raises(TripEditError):
        set_status(db, 999, "created_manual", "1")
    with pytest.raises(TripEditError):
        set_status(db, 79, "created_api")


def test_set_status_never_changes_trip(db):
    from app.models import CalendarTrip
    from app.services.netvisor_status import set_status

    trip_79(db, status="edited")
    set_status(db, 79, "created_manual", "1700")
    assert db.get(CalendarTrip, 79).status == "edited"


# ------------------------------------------------------------ Del 2: underlag


def test_entry_93_two_days_already_created(db):
    from app.services.netvisor_entry import build_entry
    from app.services.netvisor_status import seed_once

    trip_93(db)
    seed_once(db)
    e = build_entry(db, 93)
    assert e["title"] == "Business trip Helsingborg, Sweden 13.1.2026-14.1.2026"
    assert len(e["rows"]) == 1
    r = e["rows"][0]
    assert (r["start_date"], r["start_time"]) == ("13.1.2026", "05:07")
    assert (r["end_date"], r["end_time"]) == ("14.1.2026", "23:05")
    assert r["object"] == "Sverige" and r["by_air_or_sea"] is True
    assert r["started_24h_periods"] == 2
    assert r["compensation_type"] == "foreign"
    assert r["settings"]["meals_included"] is False
    assert r["settings"]["over_15_km"] and r["settings"]["over_5_km"]
    assert r["settings"]["night_travel"] is False
    assert r["settings"]["night_travel_check"] is None  # hotell natten 13-14
    assert r["description"] == "Travel for team syncs and management meeting in Helsingborg"
    assert e["description"] == r["description"]
    assert any("Already created in Netvisor (created_manual, report 1655)" in w
               for w in e["warnings"])
    assert any("only approved" in w for w in e["warnings"])
    assert e["ready"] is False


def test_entry_79_split_on_meals(db):
    from app.services.netvisor_entry import build_entry

    trip_79(db)
    e = build_entry(db, 79)
    assert len(e["rows"]) == 2
    r1, r2 = e["rows"]
    assert r1["settings"]["meals_included"] is True
    assert r1["settings"]["meals_included_days"] == ["7.5.2026"]
    assert r2["settings"]["meals_included"] is False
    assert (r1["start_date"], r1["start_time"]) == ("7.5.2026", "04:39")
    assert (r1["end_date"], r1["end_time"]) == ("8.5.2026", "00:00")
    assert (r2["start_date"], r2["start_time"]) == ("8.5.2026", "00:00")
    assert (r2["end_date"], r2["end_time"]) == ("8.5.2026", "20:59")
    assert any("Inklusive kost" in n and "7.5.2026" in n for n in e["notes"])
    # Ingen hotellnatt hittad 7-8 maj -> flagga (inte varning).
    assert r1["settings"]["night_travel_check"]
    assert e["title"] == "Business trip Båstad, Sweden 7.5.2026-8.5.2026"
    assert e["warnings"] == [] and e["ready"] is True


def test_entry_130_description_without_duplicates(db):
    from app.services.netvisor_entry import build_entry

    trip_130(db)
    e = build_entry(db, 130)
    assert len(e["rows"]) == 1
    desc = e["rows"][0]["description"]
    assert desc == (
        "Travel for HRM SE leadership meeting and office meetings in Angelholm and Ljungby"
    )
    assert desc.count("Ljungby") == 1 and desc.count("Angelholm") == 1
    assert e["title"] == "Business trip Ängelholm and Ljungby, Sweden 20.1.2026-22.1.2026"
    assert e["rows"][0]["settings"]["night_travel_check"] is None


def test_entry_domestic(db):
    from app.services.netvisor_entry import build_entry

    trip_domestic(db)
    e = build_entry(db, 200)
    assert e["title"] == "Business trip Oulu, Finland 10.3.2026"  # ett dygn: ett datum
    r = e["rows"][0]
    assert r["domestic"] is True and r["object"] == "Finland"
    assert r["compensation_type"] == "domesticfull"
    assert any("Domestic trip" in n and "No amount" in n for n in e["notes"])
    assert "amount" not in r


def test_domestic_classification():
    from app.services.netvisor_entry import compensation_type

    assert compensation_type("FI", 12) == "domesticfull"
    assert compensation_type("FI", 8) == "domestichalf"
    assert compensation_type("FI", 5) is None
    assert compensation_type("SE", 3) == "foreign"
    assert compensation_type(None, 30) is None


def test_entry_warnings(db):
    from app.services.netvisor_entry import build_entry

    _mk(db, 300, date(2026, 4, 1), date(2026, 4, 2), [{"country": None}, {"country": None}],
        destination="Köpenhamn", country=None, purpose="Kunddag med kund – Malmö")
    e = build_entry(db, 300)
    w = " | ".join(e["warnings"])
    assert "Missing travel time" in w
    assert "Country is missing" in w
    assert "long dash" in w
    assert "purpose should be in English" in w
    assert "destination should be in English" in w
    assert "–" not in e["title"] and all("–" not in r["description"] for r in e["rows"])


def test_entry_country_split(db):
    from app.services.netvisor_entry import build_entry

    _mk(db, 301, date(2026, 6, 1), date(2026, 6, 2),
        [{"country": "DK", "destination": "Copenhagen"},
         {"country": "NO", "destination": "Oslo"}],
        destination="Copenhagen → Oslo", country="DK,NO", purpose="Customer days",
        travel_start="2026-06-01T06:00:00+03:00", travel_end="2026-06-02T21:00:00+03:00")
    e = build_entry(db, 301)
    assert [r["object"] for r in e["rows"]] == ["Danmark", "Norge"]
    assert any("country changes" in n for n in e["notes"])


def test_entry_never_changes_trip(db):
    from app.models import CalendarTrip
    from app.services.netvisor_entry import build_entry

    trip_79(db, status="edited")
    before = db.get(CalendarTrip, 79).updated_at
    build_entry(db, 79)
    t = db.get(CalendarTrip, 79)
    assert t.status == "edited" and t.updated_at == before


# ------------------------------------------------------------ Del 3: payload


def test_payload_93_xml(db):
    from app.services.netvisor_payload import preview

    trip_93(db, status="approved")
    out = preview(db, 93)
    assert out["schema_errors"] == []
    root = ET.fromstring(out["xml"])
    te = root.find("tripexpense")
    assert te.findtext("header") == "Business trip Helsingborg, Sweden 13.1.2026-14.1.2026"
    assert te.find("customlines") is None
    lines = te.findall("dailycompensationlines/dailycompensationline")
    assert len(lines) == 1
    ln = lines[0]
    assert ln.findtext("timeofdeparture") == "2026-01-13 05:07:00.000"
    assert ln.findtext("returntime") == "2026-01-14 23:05:00.000"
    assert ln.findtext("compensationtype") == "foreign"
    assert ln.findtext("amount") == "2"
    assert ln.findtext("linestatus") == "open"
    assert ln.findtext("employeeidentifier") == "EMPLOYEE_NUMBER"
    assert ln.find("routedescription") is None
    assert {"amount", "compensationtype", "routedescription"} <= set(out["uncertain_fields"])
    assert "_country" not in str(out["json"])


def test_payload_79_two_lines(db):
    from app.services.netvisor_payload import preview

    trip_79(db)
    out = preview(db, 79)
    lines = out["json"]["tripexpense"]["dailycompensationlines"]
    assert len(lines) == 2
    assert lines[0]["returntime"] == lines[1]["timeofdeparture"] == "2026-05-08 00:00:00.000"
    assert out["ready"] is True


def test_payload_short_domestic_has_no_line(db):
    from app.services.netvisor_payload import preview

    trip_domestic(db, 201, end_hour="10:00")
    out = preview(db, 201)
    assert out["json"]["tripexpense"]["dailycompensationlines"] == []
    assert out["ready"] is False


def test_validate_xml_catches_errors():
    from app.services.netvisor_payload import validate_xml

    bad = (
        "<root><tripexpense><header></header><customlines/>"
        "<dailycompensationlines><dailycompensationline>"
        "<employeeidentifier>1</employeeidentifier><compensationtype>abroad</compensationtype>"
        "<amount>x</amount><linedescription>a – b</linedescription>"
        "<timeofdeparture>13.1.2026 05:07</timeofdeparture>"
        "<returntime>2026-01-14 23:05:00.000</returntime><linestatus>paid</linestatus>"
        "<routedescription>HEL-CPH</routedescription>"
        "</dailycompensationline></dailycompensationlines></tripexpense></root>"
    )
    errs = " | ".join(validate_xml(bad))
    for part in ("header is required", "customlines", "abroad", "timeofdeparture must be",
                 "amount must be a number", "linestatus must be 'open'", "routedescription",
                 "long dash"):
        assert part in errs, part


# ------------------------------------------------------------ Del 4: klient


ENV = {
    "NETVISOR_ENABLED": "true",
    "NETVISOR_BASE_URL": "https://isvapi.netvisor.fi",
    "NETVISOR_PARTNER_ID": "pid",
    "NETVISOR_PARTNER_KEY": "pkey-secret",
    "NETVISOR_CUSTOMER_ID": "cid",
    "NETVISOR_CUSTOMER_KEY": "ckey-secret",
    "NETVISOR_ORGANISATION_ID": "1967543-8",
    "NETVISOR_EMPLOYEE_IDENTIFIER": "42",
}
OK_BODY = (
    "<Root><ResponseStatus><Status>OK</Status><TimeStamp>03.05.2023 21:00:00</TimeStamp>"
    "</ResponseStatus><Replies><InsertedDataIdentifier>1802</InsertedDataIdentifier>"
    "</Replies></Root>"
)


def test_disabled_by_default(monkeypatch):
    from app.services.netvisor_client import NetvisorClient, NetvisorConfigError, is_enabled

    monkeypatch.delenv("NETVISOR_ENABLED", raising=False)
    assert is_enabled() is False
    with pytest.raises(NetvisorConfigError, match="NETVISOR_ENABLED is false"):
        NetvisorClient.from_env()


def test_missing_config_names_only():
    from app.services.netvisor_client import NetvisorClient, NetvisorConfigError

    env = dict(ENV)
    env.pop("NETVISOR_CUSTOMER_KEY")
    env["NETVISOR_PARTNER_ID"] = " "
    with pytest.raises(NetvisorConfigError) as exc:
        NetvisorClient.from_env(env)
    msg = str(exc.value)
    assert "NETVISOR_CUSTOMER_KEY" in msg and "NETVISOR_PARTNER_ID" in msg
    assert "pkey-secret" not in msg and "1967543-8" not in msg
    cfg = NetvisorClient.from_env(ENV).config
    assert "secret" not in repr(cfg)


def test_mac_matches_documented_scheme():
    import hashlib
    import hmac

    from app.services.netvisor_client import calculate_mac

    args = ("https://isvapi.netvisor.fi/accounting.nv", "ClientName", "cid",
            "2023-05-03 21:00:00.000", "FI", "1967543-8", "123456", "1683147600",
            "ckey", "pkey")
    msg = "&".join(args).encode("iso-8859-15")
    expected = hmac.new(b"ckey&pkey", msg, hashlib.sha256).hexdigest()
    assert calculate_mac(*args) == expected
    assert expected == expected.lower() and len(expected) == 64
    # Å/ä/ö kodas som ISO-8859-15 före beräkningen.
    umlaut = calculate_mac(*(("https://x/ä.nv",) + args[1:]))
    assert umlaut == hmac.new(
        b"ckey&pkey", "&".join(("https://x/ä.nv",) + args[1:]).encode("iso-8859-15"),
        hashlib.sha256,
    ).hexdigest()


def _client(handler):
    from app.services.netvisor_client import NetvisorClient

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return NetvisorClient.from_env(
        ENV, http=http,
        now=lambda: datetime(2023, 5, 3, 21, 0, tzinfo=timezone.utc),
        new_transaction_id=lambda: "tx-1",
    )


def test_headers_signed():
    from app.services.netvisor_client import calculate_mac

    c = _client(lambda r: httpx.Response(200, text=OK_BODY))
    url = "https://isvapi.netvisor.fi/tripexpense.nv"
    h = c.headers(url)
    assert h["X-Netvisor-Authentication-Timestamp"] == "2023-05-03 21:00:00.000"
    assert h["X-Netvisor-Authentication-TimestampUnix"] == "1683147600"
    assert h["X-Netvisor-Authentication-MACHashCalculationAlgorithm"] == "HMACSHA256"
    assert h["X-Netvisor-Authentication-MAC"] == calculate_mac(
        url, "Bezala Bot", "cid", "2023-05-03 21:00:00.000", "EN", "1967543-8", "tx-1",
        "1683147600", "ckey-secret", "pkey-secret",
    )
    assert "ckey-secret" not in " ".join(h.values())
    assert "pkey-secret" not in " ".join(h.values())


def test_send_trip_success_and_idempotent(db):
    from app.services.netvisor_client import NetvisorError
    from app.services.netvisor_status import status_of

    trip_79(db)
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, text=OK_BODY)

    c = _client(handler)
    out = c.send_trip(db, 79)
    assert out["status"] == "created_api" and out["netvisor_report_id"] == "1802"
    assert len(seen) == 1
    req = seen[0]
    assert str(req.url) == "https://isvapi.netvisor.fi/tripexpense.nv"
    root = ET.fromstring(req.content.decode("utf-8"))
    lines = root.findall("tripexpense/dailycompensationlines/dailycompensationline")
    assert len(lines) == 2
    assert all(ln.findtext("linestatus") == "open" for ln in lines)
    assert all(ln.findtext("employeeidentifier") == "42" for ln in lines)
    assert root.find("tripexpense/customlines") is None
    with pytest.raises(NetvisorError, match="already in Netvisor"):
        c.send_trip(db, 79)
    assert len(seen) == 1
    assert status_of(db, 79)["status"] == "created_api"


def test_send_refuses_manual_and_unapproved(db):
    from app.services.netvisor_client import NetvisorError
    from app.services.netvisor_status import seed_once

    calls = []
    c = _client(lambda r: calls.append(r) or httpx.Response(200, text=OK_BODY))
    trip_93(db, status="approved")
    seed_once(db)
    with pytest.raises(NetvisorError, match="already in Netvisor"):
        c.send_trip(db, 93)
    trip_130(db)
    from app.models import CalendarTrip

    db.get(CalendarTrip, 130).status = "edited"
    db.commit()
    with pytest.raises(NetvisorError, match="not approved"):
        c.send_trip(db, 130)
    _mk(db, 300, date(2026, 4, 1), date(2026, 4, 2), [{}, {}], destination="Malmö",
        purpose="Customer day")  # saknar tider -> varning
    with pytest.raises(NetvisorError, match="warnings"):
        c.send_trip(db, 300)
    assert calls == []


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="<Root><ResponseStatus><Status>FAILED</Status>"
                            "<Status>AUTHENTICATION_FAILED</Status></ResponseStatus></Root>"),
    httpx.Response(200, text="not xml"),
    httpx.Response(200, text="<Root><ResponseStatus><Status>OK</Status></ResponseStatus></Root>"),
])
def test_send_errors_leave_status_untouched(db, response):
    from app.services.netvisor_client import NetvisorError
    from app.services.netvisor_status import status_of

    trip_79(db)
    c = _client(lambda r: response)
    with pytest.raises(NetvisorError):
        c.send_trip(db, 79)
    assert status_of(db, 79)["status"] == "not_created"


def test_send_transport_error(db):
    from app.services.netvisor_client import NetvisorError

    trip_79(db)

    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(NetvisorError, match="ConnectError"):
        _client(handler).send_trip(db, 79)


def test_no_send_entrypoints():
    """Ingen UI-endpoint och inget MCP-verktyg skickar till Netvisor."""
    import inspect

    from app import mcp_netvisor
    from app.routers import netvisor as router_mod

    for mod in (mcp_netvisor, router_mod):
        src = inspect.getsource(mod)
        assert "send_trip" not in src and "NetvisorClient" not in src.replace(
            "NetvisorClient (avstängd)", "")
    methods = {m for r in router_mod.router.routes for m in r.methods}
    assert methods == {"GET"}


# ------------------------------------------------------------ Del 5: översikt


def test_overview_ready_filter(db):
    from app.services.netvisor_status import overview

    trip_93(db, status="approved")  # created_manual -> inte klar
    trip_79(db)                    # approved, inga varningar -> klar
    _mk(db, 7, date(2026, 2, 3), date(2026, 2, 4), [{}, {}], destination="Malmö",
        purpose="Customer day", status="suggested",
        travel_start="2026-02-03T06:00:00+02:00", travel_end="2026-02-04T21:00:00+02:00")
    _mk(db, 8, date(2026, 2, 10), date(2026, 2, 11), [{}, {}], destination="Malmö",
        purpose="x", status="rejected")
    out = overview(db, 2026)
    by_id = {r["trip_id"]: r for r in out["trips"]}
    assert [r["trip_id"] for r in out["trips"]] == [93, 7, 8, 79]
    assert by_id[93]["netvisor_status"] == "created_manual"
    assert by_id[79]["ready_for_netvisor"] is True
    assert by_id[7]["ready_for_netvisor"] is False
    assert by_id[8]["trip_status"] == "rejected"
    ready = overview(db, 2026, ready_only=True)
    assert [r["trip_id"] for r in ready["trips"]] == [79] and ready["ready_count"] == 1


# ------------------------------------------------------------ API (Resor-vyn)


def test_api_endpoints(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers.netvisor import get_db, require_auth, router

    trip_79(db)
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[require_auth] = lambda: None
    c = TestClient(api)
    r = c.get("/api/netvisor/trips/79/entry")
    assert r.status_code == 200, r.text
    assert len(r.json()["rows"]) == 2
    assert c.get("/api/netvisor/trips/999/entry").status_code == 404
    x = c.get("/api/netvisor/trips/79/payload.xml")
    assert x.status_code == 200 and x.text.startswith("<?xml")
    assert "attachment" in x.headers["content-disposition"]
    j = c.get("/api/netvisor/trips/79/payload.json").json()
    assert len(j["payload"]["tripexpense"]["dailycompensationlines"]) == 2
    assert c.get("/api/netvisor/overview?ready_only=true").json()["count"] == 1
    assert c.get("/api/netvisor/status").json()["trips"][0]["trip_id"] == 79


def test_api_requires_login():
    from fastapi.testclient import TestClient

    from app.main import app as main_app

    assert TestClient(main_app).get("/api/netvisor/overview").status_code == 401
