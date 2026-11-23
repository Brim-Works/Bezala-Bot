"""C54 - rutttabell för km, Admins sju rader och samma siffror överallt."""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.data import route_km as rk  # noqa: E402
from app.services import trip_legs as tl  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _receipt  # noqa: E402
from tests.backend.test_c53 import OSLO_FLIGHTS, _flight, _trip  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("TRIP_CAR_HOME_KM_ONE_WAY", "TRIP_CAR_OUT_ELECTRIC_SHARE",
              "TRIP_CAR_BACK_ELECTRIC_SHARE", "TRIP_CAR_ELECTRIC_KWH_PER_100KM",
              "TRIP_CAR_PETROL_L_PER_100KM_DISCHARGED", "FLIGHT_SHORT_MAX_KM",
              "FLIGHT_LONG_MIN_KM"):
        monkeypatch.delenv(k, raising=False)


def _r(db, day, vendor, amount, currency, file_name, subject="Receipt", **kw):  # noqa: F811
    m = _receipt(db, day, vendor, amount, currency, waiting=False,
                 category=kw.pop("category", "Kollektivtrafik"), subject=subject, **kw)
    m.file_name = file_name
    db.commit()
    return m


def _oslo_trip_140(db):  # noqa: F811
    """Oslo 21 okt (prod resa 140): ut- och hemresa med Flytoget + ett
    samlingskvitto för samma resa som det första."""
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS)
    d = date(2026, 10, 21)
    a = _r(db, d, "Flytoget", 276.0, "NOK", "20261021 Flytoget Oslo Lufthavn Sk yen.pdf",
           "Receipt from the Airport Express Train 21.10.26 07:52")
    b = _r(db, d, "Flytoget", 276.0, "NOK", "20261021 Flytoget Sk yen till Oslo Lufthavn.pdf",
           "Receipt from the Airport Express Train 21.10.26 07:51")
    c = _r(db, d, "Flytoget", 276.0, "NOK", "20261021 Flytoget Oslo Lufthavn til Skoyen.pdf",
           "Receipts from the Airport Express Train for journeys made 21-10-2026 - 21-10-2026")
    _receipt(db, d, "Moovy", 37.49, waiting=False, category="Parkering")
    return t, a, b, c


# ---------------------------------------------------------------- Del 1 rutttabell


def test_flytoget_both_directions_52_4():
    assert rk.lookup_km("Oslo Airport", "Skoyen", "flytoget")["km"] == 52.4
    assert rk.lookup_km("Skoyen", "Oslo Airport", "flytoget")["km"] == 52.4
    assert rk.lookup_km("Oslo Airport", "Oslo S")["km"] == 48.1
    assert rk.lookup_km("Oslo Airport", "Nationaltheatret")["km"] == 49.5
    assert rk.lookup_km("Lysaker", "Oslo Airport")["km"] == 55.1
    assert rk.lookup_km("Arlanda Airport", "Stockholm C")["km"] == 39.0
    assert rk.lookup_km("Copenhagen Airport", "Malmo C") is None  # inte i kvittona


def test_places_and_direction_from_receipt_names():
    assert rk.direction_from(["20261021 Flytoget Oslo Lufthavn Sk yen.pdf"])[:2] == \
        ("Oslo Airport", "Skoyen")
    assert rk.direction_from(["20261021 Flytoget Skøyen til Oslo Lufthavn.pdf"])[:2] == \
        ("Skoyen", "Oslo Airport")
    assert rk.direction_from(["Enkelresa Stockholm-Arlanda.pdf"])[:2] == \
        ("Stockholm C", "Arlanda Airport")
    assert rk.direction_from(["20260120 Skånetrafiken CPH Kastrup-Angelholm.pdf"])[:2] == \
        ("Copenhagen Airport", "Angelholm")
    assert rk.places_in("Arlanda Express Tur och retur biljett") == []
    assert rk.is_round_trip("Arlanda Express Tur och retur biljett")


def test_oslo_21_oct_gives_two_train_legs_of_52_4(db):  # noqa: F811
    t, a, b, c = _oslo_trip_140(db)
    view = tl.get_trip_legs(db, t.id)
    trains = [leg for leg in view["legs"] if leg["mode"] == "train"]
    active = [leg for leg in trains if not leg["excluded"]]
    assert [leg["km"] for leg in active] == [52.4, 52.4]
    assert all(leg["source"] == "route_table" and leg["is_estimate"] for leg in active)
    assert {(leg["from"], leg["to"]) for leg in active} == {
        ("Oslo Airport", "Skoyen"), ("Skoyen", "Oslo Airport")}
    dup = [leg for leg in trains if leg["excluded"]]
    assert len(dup) == 1 and dup[0]["receipt_message_id"] == c.message_id
    assert "duplicate of receipt" in dup[0]["note"]
    assert view["admin"]["train_km"] == 104.8 and view["admin"]["estimate"]["train_km"]
    cars = [leg for leg in view["legs"] if leg["mode"] == "car_phev"]
    assert [leg["km"] for leg in cars] == [48, 48]
    assert "km_missing" not in view["legs_flags"]
    # kvitto + Bezala-utlägg bredvid sträckan
    first = active[0]
    assert first["receipt"]["vendor"] == "Flytoget" and first["receipt"]["amount"] == 276.0
    assert "bezala_transaction_id" in first["receipt"]


def test_receipt_km_wins_over_table(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS)
    _r(db, date(2026, 10, 21), "Flytoget", 276.0, "NOK", "Flytoget Oslo Lufthavn Skoyen.pdf",
       ai_description_en="Airport train Oslo Airport - Skoyen, 53,1 km")
    train = [leg for leg in tl.get_trip_legs(db, t.id)["legs"] if leg["mode"] == "train"][0]
    assert train["km"] == 53.1 and train["source"] == "receipt"


def test_manual_km_is_never_overwritten(db):  # noqa: F811
    t, *_ = _oslo_trip_140(db)
    rows, _ = tl.sync_trip_legs(db, t)
    train = [r for r in rows if r.mode == "train" and not r.excluded][0]
    tl.update_trip_leg(db, train.id, km=50)
    rows, _ = tl.sync_trip_legs(db, t)
    again = [r for r in rows if r.id == train.id][0]
    assert again.km == 50 and again.source == "manual" and not again.is_estimate
    # även en auto-rad med source=manual och km skrivs inte över
    again.set_by = "auto"
    db.commit()
    rows, _ = tl.sync_trip_legs(db, t)
    assert [r for r in rows if r.id == train.id][0].km == 50


def test_missing_route_gives_km_missing_with_reason(db):  # noqa: F811
    t = _trip(db, date(2026, 3, 5), date(2026, 3, 5), [
        _flight("AY 951", "HEL", "CPH", "2026-03-05T05:10:00+00:00", "2026-03-05T06:45:00+00:00"),
        _flight("AY 960", "CPH", "HEL", "2026-03-05T17:30:00+00:00", "2026-03-05T19:55:00+00:00"),
    ], dest="Helsingborg")
    _r(db, date(2026, 3, 5), "Skånetrafiken", 490.0, "SEK",
       "20260305 Skånetrafiken Öresundszon 24 timmar.pdf", "Tack för ditt köp")
    _r(db, date(2026, 3, 5), "Skånetrafiken", 245.0, "SEK",
       "20260305 Skånetrafiken Kastrup-Helsingborg.pdf", "Tack för ditt köp")
    view = tl.get_trip_legs(db, t.id)
    trains = [leg for leg in view["legs"] if leg["mode"] == "train"]
    assert all(leg["km"] is None and leg["km_missing"] for leg in trains)
    assert {leg["km_reason"] for leg in trains} == {"no_route_in_table"}
    hb = [leg for leg in trains if leg["to"] == "Helsingborg C"][0]
    assert hb["from"] == "Copenhagen Airport"  # riktningen visas även utan km
    assert "km_missing" in view["legs_flags"]
    assert view["admin"]["missing"]["train_km"] == 2
    assert {m["reason"] for m in view["legs_missing"]} == {"no_route_in_table"}


def test_direction_unknown_same_day_and_context_on_overnight(db):  # noqa: F811
    day_trip = _trip(db, date(2026, 4, 16), date(2026, 4, 16), [
        _flight("AY 801", "HEL", "ARN", "2026-04-16T03:45:00+00:00", "2026-04-16T04:45:00+00:00"),
        _flight("AY 812", "ARN", "HEL", "2026-04-16T11:55:00+00:00", "2026-04-16T12:55:00+00:00"),
    ], dest="Stockholm")
    _r(db, date(2026, 4, 16), "Arlanda Express", 100.0, "SEK",
       "20260416 Arlanda Express Enkelresa vuxen.pdf", "Arlanda express biljett och kvitto")
    leg = [x for x in tl.get_trip_legs(db, day_trip.id)["legs"] if x["mode"] == "train"][0]
    assert leg["km"] is None and leg["km_reason"] == "direction_unknown"

    t = _trip(db, date(2026, 11, 3), date(2026, 11, 4), [
        _flight("AY 823", "HEL", "ARN", "2026-11-03T05:40:00+00:00", "2026-11-03T06:40:00+00:00"),
        _flight("AY 818", "ARN", "HEL", "2026-11-04T18:20:00+00:00", "2026-11-04T19:20:00+00:00"),
    ], dest="Stockholm")
    _r(db, date(2026, 11, 3), "Arlanda Express", 100.0, "SEK",
       "20261103 Arlanda Express Enkelresa vuxen.pdf", "Arlanda express ticket and receipt")
    leg = [x for x in tl.get_trip_legs(db, t.id)["legs"] if x["mode"] == "train"][0]
    assert (leg["from"], leg["to"], leg["km"]) == ("Arlanda Airport", "Stockholm C", 39.0)
    assert leg["is_estimate"] and "direction from the trip" in leg["note"]


def test_round_trip_ticket_gives_two_legs_and_duplicate_receipt_is_excluded(db):  # noqa: F811
    t = _trip(db, date(2026, 4, 30), date(2026, 4, 30), [
        _flight("AY 803", "HEL", "ARN", "2026-04-30T04:15:00+00:00", "2026-04-30T05:15:00+00:00"),
        _flight("AY 814", "ARN", "HEL", "2026-04-30T14:40:00+00:00", "2026-04-30T15:40:00+00:00"),
    ], dest="Stockholm")
    _r(db, date(2026, 4, 30), "Arlanda Express", 640.0, "SEK",
       "20260430 Arlanda Express Tur och retur biljett.pdf", "Arlanda express biljett")
    _r(db, date(2026, 4, 30), "Arlanda Express", 640.0, "SEK",
       "20260430 Arlanda Express Tur och retur.pdf", "20260430 Arlanda Express Tur och retur.pdf",
       bezala_transaction_id="5040564")
    view = tl.get_trip_legs(db, t.id)
    trains = [x for x in view["legs"] if x["mode"] == "train"]
    active = [x for x in trains if not x["excluded"]]
    assert len(trains) == 4 and len(active) == 2
    assert [(x["from"], x["to"], x["km"]) for x in active] == [
        ("Arlanda Airport", "Stockholm C", 39.0), ("Stockholm C", "Arlanda Airport", 39.0)]
    assert all(x["receipt"]["bezala_transaction_id"] == "5040564" for x in active)
    assert view["admin"]["train_km"] == 78


def test_flight_origin_inferred_from_round_trip(db):  # noqa: F811
    t = _trip(db, date(2026, 1, 20), date(2026, 1, 22), [
        _flight("AY 959", None, "CPH", "2026-01-20T15:50:00+00:00", "2026-01-20T17:25:00+00:00"),
        _flight("AY 960", None, "HEL", "2026-01-22T17:30:00+00:00", "2026-01-22T19:55:00+00:00"),
    ], dest="Angelholm")
    flights = [x for x in tl.get_trip_legs(db, t.id)["legs"] if x["mode"] == "flight"]
    assert [(x["from"], x["to"], x["km"]) for x in flights] == [
        ("HEL", "CPH", 892.0), ("CPH", "HEL", 892.0)]
    assert all(x["is_estimate"] and x["source"] == "assumption" for x in flights)
    assert all(x["flight_category"] == "medium" for x in flights)


# ---------------------------------------------------------------- Del 0 Admin


def test_flight_categories_and_env_thresholds(monkeypatch):
    assert tl.flight_category(399) == "short"
    assert tl.flight_category(699.9) == "short"
    assert tl.flight_category(700) == "medium"
    assert tl.flight_category(763) == "medium"
    assert tl.flight_category(3699) == "medium"
    assert tl.flight_category(3700) == "long"
    monkeypatch.setenv("FLIGHT_SHORT_MAX_KM", "800")
    assert tl.flight_category(763) == "short"


def test_taxi_is_non_company_car_and_company_car_is_info_only(db):  # noqa: F811
    t = _trip(db, date(2026, 11, 18), date(2026, 11, 18), [
        _flight("AY 965", "HEL", "GOT", "2026-11-18T05:15:00+00:00", "2026-11-18T06:00:00+00:00"),
        _flight("AY 966", "GOT", "HEL", "2026-11-18T15:00:00+00:00", "2026-11-18T17:55:00+00:00"),
    ], dest="Gothenburg")
    _receipt(db, date(2026, 11, 18), "Taxi Göteborg", 49.97, waiting=False, category="Taxi",
             ai_description_en="Taxi ride in Gothenburg, 24,6 km")
    _receipt(db, date(2026, 11, 18), "Moovy", 20.0, waiting=False, category="Parkering")
    a = tl.get_trip_legs(db, t.id)["admin"]
    assert a["non_company_car_km"] == 24.6
    assert a["medium_flights_km"] == 1566 and a["short_flights_km"] == 0
    car = a["company_car_info_only"]
    assert car["km"] == 96 and car["legs"] == 2 and car["kwh"] == 7.92
    total = sum(a[k] for k in tl.ADMIN_KEYS if k != "hotel_nights")
    assert total == 1566 + 24.6  # bilen ingår inte
    assert a["hotel_nights"] == 0


def test_hotel_nights_per_trip(db):  # noqa: F811
    t = _trip(db, date(2026, 1, 20), date(2026, 1, 22), [
        _flight("AY 959", "HEL", "CPH", "2026-01-20T15:50:00+00:00", "2026-01-20T17:25:00+00:00"),
        _flight("AY 960", "CPH", "HEL", "2026-01-22T17:30:00+00:00", "2026-01-22T19:55:00+00:00"),
    ], dest="Angelholm")
    _receipt(db, date(2026, 1, 21), "Hotel Erikslust", 1200.0, "SEK", waiting=False,
             category="Hotell")
    nights = tl.hotel_nights(db, t)
    assert nights["nights"] == 2
    assert nights["with_receipt"] >= 1
    assert tl.get_trip_legs(db, t.id)["admin"]["hotel_nights"] == 2
    s = tl.get_emissions_summary(db, 2026)
    assert s["admin"]["hotel_nights"] == 2


# ---------------------------------------------------------------- samma siffror


@pytest.fixture()
def client(db):  # noqa: F811
    from fastapi.testclient import TestClient

    from app import main
    from app.routers import emissions

    def _get_db():
        yield db

    main.app.dependency_overrides[emissions.get_db] = _get_db
    main.app.dependency_overrides[emissions.require_auth] = lambda: None
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def test_trips_view_mcp_and_export_give_same_numbers_for_trip_140(client, db):  # noqa: F811
    t, *_ = _oslo_trip_140(db)
    mcp = [p for p in tl.get_emissions_summary(db, 2026)["per_trip"] if p["trip_id"] == t.id][0]
    view_summary = client.get("/api/emissions/summary?year=2026").json()
    view = [p for p in view_summary["per_trip"] if p["trip_id"] == t.id][0]
    detail = client.get(f"/api/emissions/trips/{t.id}/legs").json()
    export = [r for r in tl.admin_export_rows(db, 2026) if r["trip_id"] == t.id][0]
    for k in tl.ADMIN_KEYS:
        assert mcp["admin"][k] == view["admin"][k] == detail["admin"][k] \
            == export[tl.ADMIN_LABELS[k]], k
    assert mcp["admin"]["train_km"] == 104.8
    assert mcp["admin"]["medium_flights_km"] == 1526
    # detaljexporten summerar till samma tågkm
    rows = tl.export_rows(db, 2026)
    assert round(sum(float(r["km"]) for r in rows
                     if r["trip_id"] == t.id and r["admin_category"] == "train_km"), 1) == 104.8
    csv_admin = client.get("/api/emissions/export.csv?year=2026&mode=admin").text
    assert csv_admin.splitlines()[0] == ",".join(tl.ADMIN_EXPORT_COLUMNS)
    csv_admin.encode("ascii")


def test_admin_export_excludes_company_car_and_detail_is_ascii(db):  # noqa: F811
    _oslo_trip_140(db)
    rows = tl.admin_export_rows(db, 2026)
    assert rows[-1]["trip_id"] == "Total"
    assert set(rows[0]) == set(tl.ADMIN_EXPORT_COLUMNS)
    assert not any("car" in c.lower() and "non-company" not in c.lower()
                   for c in tl.ADMIN_EXPORT_COLUMNS)
    detail = tl.export_csv(db, 2026)
    detail.encode("ascii")
    assert "company_car_info_only" in detail
    routes = tl.export_csv(db, 2026, "route_table")
    assert "Oslo Airport,Skoyen,52.4" in routes
    routes.encode("ascii")


def test_patch_leg_from_trips_view(client, db):  # noqa: F811
    t, *_ = _oslo_trip_140(db)
    legs = client.get(f"/api/emissions/trips/{t.id}/legs").json()["legs"]
    train = [x for x in legs if x["mode"] == "train" and not x["excluded"]][0]
    r = client.patch(f"/api/emissions/legs/{train['leg_id']}",
                     json={"km": 52.0, "from_place": "Oslo Airport", "to_place": "Oslo S"})
    assert r.status_code == 200 and r.json()["source"] == "manual"
    r = client.patch(f"/api/emissions/legs/{train['leg_id']}", json={"excluded": True})
    assert r.json()["excluded"] is True
    a = client.get(f"/api/emissions/trips/{t.id}/legs").json()["admin"]
    assert a["train_km"] == 52.4


def test_summary_counts_rejected_trips(db):  # noqa: F811
    _oslo_trip_140(db)
    _trip(db, date(2026, 11, 19), date(2026, 11, 20), [], status="rejected", dest="Helsinki")
    s = tl.get_emissions_summary(db, 2026)
    assert s["trips"] == 1 and s["trips_rejected_not_counted"] == 1
    assert s["legs_with_km"] + s["legs_missing_km"] == s["legs"]


def test_netvisor_payload_has_no_km_or_emissions(db):  # noqa: F811
    from app.services.netvisor_payload import build_payload

    t, *_ = _oslo_trip_140(db)
    t.status = "approved"
    t.purpose = "Internal audit and management meeting"
    db.commit()
    p = build_payload(db, t.id)
    text = str(p["payload"]).lower()
    assert " km" not in text and "co2" not in text and "emission" not in text
    assert "approver" not in text and "accept" not in text
