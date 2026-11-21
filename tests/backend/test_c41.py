"""C41 — restidsmarginal, engelska Netvisor-fält, CPH = Sverige, MCP-inloggning."""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services.trip_detector import detect_trips, flight_destination  # noqa: E402
from app.services.trip_english import (  # noqa: E402
    destination_to_english,
    generate_purpose,
    netvisor_line_description,
    non_english_warnings,
)
from tests.backend.test_mcp import (  # noqa: E402,F401
    _authorize,
    _call,
    _pkce,
    _register,
    env,
)
from tests.backend.test_trip_detector import HOMES, ev, flight  # noqa: E402

# Helsingfors: vintertid +02:00 (5 nov, 18 nov, 10 mar), sommartid +03:00 (aug).
W = timezone(timedelta(hours=2))
S = timezone(timedelta(hours=3))
CET = timezone(timedelta(hours=1))


def _gbg_day_trip():
    """18 nov: AY 861 07:15 → GOT, AY 866 landar 19:55."""
    return [
        flight("o", "Goteborg", datetime(2026, 11, 18, 7, 15, tzinfo=W),
               datetime(2026, 11, 18, 8, 45, tzinfo=W), no="AY 861", origin="Helsinki HEL"),
        ev("m1", "Management Meeting - Governance and decisions",
           datetime(2026, 11, 18, 11, 0, tzinfo=W), datetime(2026, 11, 18, 11, 40, tzinfo=W),
           others=6),
        ev("m2", "Mikko & Måns Synk", datetime(2026, 11, 18, 14, 30, tzinfo=W),
           datetime(2026, 11, 18, 14, 55, tzinfo=W),
           location="HEL-RAU-6-Pixel (1), GOT-LIN-19-193 (2)"),
        ev("m3", "Mikko / Alex", datetime(2026, 11, 18, 11, 45, tzinfo=W),
           datetime(2026, 11, 18, 12, 10, tzinfo=W),
           location="Citygate, GOT-LIN-19-Valkyria (8) [CFM]"),
        flight("r", "Helsinki", datetime(2026, 11, 18, 18, 30, tzinfo=W),
               datetime(2026, 11, 18, 19, 55, tzinfo=W), no="AY 866",
               origin="Gothenburg GOT"),
    ]


# ---------- Del 1: restidsmarginal ----------


def test_day_trip_travel_times_gothenburg():
    t = detect_trips(_gbg_day_trip(), HOMES)["trips"][0]
    # Flygtider oförändrade.
    assert t["start_at"].startswith("2026-11-18T07:15")
    assert t["end_at"].startswith("2026-11-18T19:55")
    # Resetider: 07:15 − 60 − 40 = 05:35, 19:55 + 20 + 40 = 20:55.
    assert t["travel_start_at"].startswith("2026-11-18T05:35")
    assert t["travel_end_at"].startswith("2026-11-18T20:55")
    day = t["days"][0]
    assert (day["start_time"], day["end_time"]) == ("05:35", "20:55")
    assert (t["destination"], t["country"]) == ("Gothenburg", "SE")
    assert day["airport"] == "GOT"
    assert t["purpose"] == "Management meeting and team syncs, Gothenburg office"


def test_overnight_trip_no_margin_abroad():
    events = [
        flight("o", "Oslo", datetime(2026, 11, 27, 7, 20, tzinfo=W),
               datetime(2026, 11, 27, 8, 55, tzinfo=W), no="AY 911", origin="Helsinki HEL"),
        flight("r", "Helsinki", datetime(2026, 11, 28, 13, 30, tzinfo=W),
               datetime(2026, 11, 28, 16, 55, tzinfo=W), no="AY 914", origin="Oslo OSL"),
    ]
    t = detect_trips(events, HOMES)["trips"][0]
    d1, d2 = t["days"]
    assert (d1["start_time"], d1["end_time"]) == ("05:40", None)
    assert (d2["start_time"], d2["end_time"]) == (None, "17:55")


def test_missing_return_has_no_end_time():
    events = [
        flight("o", "Oslo", datetime(2026, 11, 27, 7, 20, tzinfo=W),
               datetime(2026, 11, 27, 8, 55, tzinfo=W), origin="Helsinki HEL"),
    ]
    t = detect_trips(events, HOMES)["trips"][0]
    assert "missing_return" in t["flags"]
    assert t["travel_end_at"] is None
    assert all(d["end_time"] is None for d in t["days"])
    assert t["days"][0]["start_time"] == "05:40"


def test_margins_from_env():
    env_ = {"TRIP_HOME_AIRPORT_BEFORE_DEPARTURE_MIN": "30", "TRIP_HOME_DRIVE_MIN": "0",
            "TRIP_HOME_AIRPORT_AFTER_LANDING_MIN": "10"}
    with patch.dict(os.environ, env_):
        t = detect_trips(_gbg_day_trip(), HOMES)["trips"][0]
    assert (t["days"][0]["start_time"], t["days"][0]["end_time"]) == ("06:45", "20:05")


# ---------- Del 3: CPH → arbetsort ----------


def _cph(day: int, month: int = 11, out=(7, 30), back=(21, 55), tz=W, nights=0, extra=()):
    d0 = datetime(2026, month, day, *out, tzinfo=tz)
    d1 = datetime(2026, month, day, *back, tzinfo=tz) + timedelta(days=nights)
    return [
        flight("o", "Copenhagen", d0, d0 + timedelta(minutes=100), no="AY 953",
               origin="Helsinki HEL"),
        *extra,
        flight("r", "Helsinki", d1 - timedelta(minutes=95), d1, no="AY 962",
               origin="Copenhagen CPH"),
    ]


def test_cph_customer_day_malmo():
    """5 nov: 'Agda PS Kunddag Malmö' → Malmö, SE, 05:50–22:55."""
    meet = [
        ev("k", "Agda PS Kunddag Malmö", datetime(2026, 11, 5, 8, 30, tzinfo=CET),
           datetime(2026, 11, 5, 17, 0, tzinfo=CET), response="needsAction", others=18),
        ev("s", "Therese / Mikko", datetime(2026, 11, 5, 10, 15, tzinfo=CET),
           datetime(2026, 11, 5, 10, 55, tzinfo=CET)),
    ]
    t = detect_trips(_cph(5, extra=meet), HOMES)["trips"][0]
    day = t["days"][0]
    assert (day["destination"], day["country"], day["airport"]) == ("Malmö", "SE", "CPH")
    assert day["destination_source"] == "meeting_title"
    assert (day["start_time"], day["end_time"]) == ("05:50", "22:55")
    assert t["flags"] == []
    assert t["purpose"].startswith("Customer day Agda PS")
    assert t["purpose"].endswith(", Malmö")


def test_cph_story_hotel_malmo():
    """10–11 mar: hotell 'Story Hotel Malmö' → Malmö, SE båda dagarna."""
    hotel = ev("h", "Story Hotel Malmö", date(2026, 3, 10), date(2026, 3, 11), others=0)
    events = _cph(10, month=3, out=(11, 35), back=(19, 35), nights=1, extra=[hotel])
    t = detect_trips(events, HOMES)["trips"][0]
    assert [(d["destination"], d["country"]) for d in t["days"]] == [
        ("Malmö", "SE"), ("Malmö", "SE"),
    ]
    assert t["days"][0]["destination_source"] == "hotel"
    assert t["flags"] == []


def test_cph_room_code_prefix_plus_office():
    """HEL-FAB = Helsingborg (inte Helsingfors), MAL-OAS = Malmö."""
    room = ev("m", "Workshop", datetime(2026, 1, 13, 13, 0, tzinfo=W),
              datetime(2026, 1, 13, 15, 0, tzinfo=W), location="HEL-FAB-6-Najaden (12)")
    t = detect_trips(_cph(13, month=1, extra=[room]), HOMES)["trips"][0]
    assert (t["days"][0]["destination"], t["days"][0]["country"]) == ("Helsingborg", "SE")
    assert t["days"][0]["destination_source"] == "meeting_room"
    assert t["purpose"] == "Workshop, Helsingborg office"
    # HEL-RAU (Helsingfors-kontoret) i ett distansmöte ger ingen ort.
    remote = ev("m", "Weekly", datetime(2026, 1, 13, 13, 0, tzinfo=W),
                datetime(2026, 1, 13, 15, 0, tzinfo=W), location="HEL-RAU-6-Pixel (1)")
    t = detect_trips(_cph(13, month=1, extra=[remote]), HOMES)["trips"][0]
    assert t["days"][0]["destination"] is None
    assert "destination_unconfirmed" in t["flags"]


def test_cph_all_day_city_marker_wins():
    """25 aug = Malmö, 26 aug = Helsingborg (Mikkos egna heldagsevent)."""
    m1 = ev("c1", "Malmö", date(2026, 8, 25), date(2026, 8, 26), others=0,
            transparency="transparent")
    m2 = ev("c2", "Helsingborg", date(2026, 8, 26), date(2026, 8, 27), others=0,
            transparency="transparent")
    # En mötestitel med annan ort ska inte vinna över markören.
    other = ev("k", "Agda Kunddagar Malmö", datetime(2026, 8, 26, 9, 0, tzinfo=S),
               datetime(2026, 8, 26, 17, 0, tzinfo=S))
    events = _cph(25, month=8, out=(7, 25), back=(22, 0), tz=S, nights=1,
                  extra=[m1, m2, other])
    t = detect_trips(events, HOMES)["trips"][0]
    assert [(d["date"], d["destination"], d["country"]) for d in t["days"]] == [
        ("2026-08-25", "Malmö", "SE"), ("2026-08-26", "Helsingborg", "SE"),
    ]
    assert {d["destination_source"] for d in t["days"]} == {"calendar_marker"}
    assert t["destination"] == "Malmö → Helsingborg"
    assert t["country"] == "SE"


def test_cph_without_clues_is_sweden_unconfirmed():
    t = detect_trips(_cph(26), HOMES)["trips"][0]
    day = t["days"][0]
    assert (day["destination"], day["country"], day["airport"]) == (None, "SE", "CPH")
    assert t["flags"] == ["destination_unconfirmed"]
    assert t["destination"] is None and t["country"] == "SE"


def test_cph_receipt_and_danish_city():
    rec = {"2026-11-26": ["Clarion Hotel Malmö Live receipt"]}
    t = detect_trips(_cph(26), HOMES, receipts_by_date=rec)["trips"][0]
    assert (t["days"][0]["destination"], t["days"][0]["destination_source"]) == (
        "Malmö", "receipt")
    # En annan dansk ort → Denmark.
    m = ev("a", "Kundmöte Aarhus", datetime(2026, 11, 26, 13, 0, tzinfo=W),
           datetime(2026, 11, 26, 14, 0, tzinfo=W))
    t = detect_trips(_cph(26, extra=[m]), HOMES)["trips"][0]
    assert (t["days"][0]["destination"], t["days"][0]["country"]) == ("Aarhus", "DK")


def test_flight_receipts_and_remote_meetings_ignored():
    """Prod 7 maj / 26 nov: Finnair-kvittot nämner Copenhagen → inte DK.
    Prod 22 apr: möten i Växjö-rum innan flyget 18:00 → räknas inte."""
    rec = {"2026-11-26": ["Finnair Helsinki - Copenhagen AY 957", "Skånetrafiken"]}
    t = detect_trips(_cph(26), HOMES, receipts_by_date=rec)["trips"][0]
    assert (t["days"][0]["destination"], t["days"][0]["country"]) == (None, "SE")
    assert t["flags"] == ["destination_unconfirmed"]

    before = ev("v", "TSLG", datetime(2026, 4, 22, 9, 15, tzinfo=S),
                datetime(2026, 4, 22, 10, 0, tzinfo=S), location="VXO-SAM-2-GPS (6)")
    ws = ev("w", "Workshop Priser & BPO - Ljungby", datetime(2026, 4, 23, 11, 0, tzinfo=S),
            datetime(2026, 4, 23, 15, 50, tzinfo=S), location="LBY-LHQ-1-Vindruvan (10)")
    t = detect_trips(_cph(22, month=4, out=(18, 0), back=(22, 0), tz=S, nights=1,
                          extra=[before, ws]), HOMES)["trips"][0]
    assert [(d["destination"], d["destination_source"]) for d in t["days"]] == [
        ("Ljungby", "carried"), ("Ljungby", "meeting_title"),
    ]
    assert t["purpose"] == "Workshop, Ljungby office"


def test_multi_city_per_day_and_carry_forward():
    """Malmö dag 1, Växjö dag 2, dag 3 utan ledtråd ärver Växjö."""
    a = ev("a", "Halvdag LG Malmö", datetime(2026, 3, 10, 13, 0, tzinfo=CET),
           datetime(2026, 3, 10, 18, 0, tzinfo=CET))
    b = ev("b", "Weekly", datetime(2026, 3, 11, 13, 0, tzinfo=CET),
           datetime(2026, 3, 11, 14, 0, tzinfo=CET), location="VXO-SAM-4-Pikachu (30)")
    t = detect_trips(_cph(10, month=3, nights=2, extra=[a, b]), HOMES)["trips"][0]
    assert [(d["destination"], d["destination_source"]) for d in t["days"]] == [
        ("Malmö", "meeting_title"), ("Växjö", "meeting_room"), ("Växjö", "carried"),
    ]
    assert t["destination"] == "Malmö → Växjö"


def test_arn_got_osl_unchanged():
    for dest, iata, city, cc in (("Stockholm", "ARN", "Stockholm", "SE"),
                                 ("Goteborg", "GOT", "Gothenburg", "SE"),
                                 ("Oslo", "OSL", "Oslo", "NO")):
        m = ev("m", "Kunddag Malmö", datetime(2026, 4, 1, 10, 0, tzinfo=S),
               datetime(2026, 4, 1, 11, 0, tzinfo=S))
        events = [
            flight("o", dest, datetime(2026, 4, 1, 7, 0, tzinfo=S),
                   datetime(2026, 4, 1, 8, 0, tzinfo=S), origin="Helsinki HEL"),
            m,
            flight("r", "Helsinki", datetime(2026, 4, 1, 18, 0, tzinfo=S),
                   datetime(2026, 4, 1, 20, 0, tzinfo=S), origin=f"{dest} {iata}"),
        ]
        t = detect_trips(events, HOMES)["trips"][0]
        day = t["days"][0]
        assert (day["destination"], day["country"], day["airport"]) == (city, cc, iata)
        assert day["destination_source"] == "airport"
        assert t["flags"] == []


# ---------- Del 2: engelska ----------


def test_iata_to_english_city():
    d = datetime(2026, 3, 2, 7, 0, tzinfo=W)
    g = lambda s: ev("a", s, d, d, event_type="fromGmail")  # noqa: E731
    assert flight_destination(g("Flight to CPH (AY 959)")) == "Copenhagen"
    assert flight_destination(g("Flight to Goteborg (AY 861)")) == "Gothenburg"
    assert flight_destination(g("Flight to HEL (AY 960)")) == "Helsinki"


def test_destination_migration_to_english():
    assert destination_to_english("Köpenhamn") == "Copenhagen"
    assert destination_to_english("Köpenhamn → Oslo") == "Copenhagen → Oslo"
    assert destination_to_english("Göteborg") == "Gothenburg"
    assert destination_to_english("Malmö") == "Malmö"
    assert destination_to_english("Oslo (manuell)") == "Oslo (manuell)"


def test_purpose_generated_in_english_without_names():
    p = generate_purpose(
        [{"summary": "Agda PS Kunddag Malmö"}, {"summary": "Anita / Mikko"}], "Malmö")
    assert p == "Customer day Agda PS and team syncs, Malmö"
    assert "Anita" not in p and "Mikko" not in p
    assert not non_english_warnings("purpose", p)


def test_non_english_warnings():
    assert non_english_warnings("purpose", "Kunddag och möte med Agda")
    assert non_english_warnings("purpose", "Asiakaspäivä ja kokous")
    assert not non_english_warnings("purpose", "Customer day Agda PS, Malmö")
    assert not non_english_warnings("purpose", "Management meeting, Växjö office")
    assert non_english_warnings("destination", "Köpenhamn")
    assert not non_english_warnings("destination", "Malmö")


def test_netvisor_line_description():
    assert netvisor_line_description(["Malmö"], "SE", "Customer day Agda PS, Malmö") == (
        "Business trip Malmö, Sweden – Customer day Agda PS")
    assert netvisor_line_description(["Malmö", "Växjö"], "SE", None) == (
        "Business trip Malmö and Växjö, Sweden")


def test_ui_translates_to_swedish():
    """UI (sv) översätter de engelska värdena; databasen har engelska."""
    sv = open("frontend/src/i18n/sv.js", encoding="utf-8").read()
    assert "Copenhagen: 'Köpenhamn'" in sv and "Gothenburg: 'Göteborg'" in sv
    assert "SE: 'Sverige'" in sv
    card = open("frontend/src/components/trips/CalendarTripCard.jsx", encoding="utf-8").read()
    assert "cityLabel(c, day.destination)" in card and "flight_departure" in card


# ---------- Del 1+2+3 i databasen: migrering + refresh ----------


def test_migration_and_locked_untouched(env):
    _, scope, Session = env
    from app.models import CalendarTrip, CalendarTripDay
    from app.services.calendar_trip_store import migrate_c41

    with scope() as s:
        sug = CalendarTrip(
            start_date=date(2026, 11, 18), end_date=date(2026, 11, 18),
            start_at="2026-11-18T07:15:00+02:00", end_at="2026-11-18T19:55:00+02:00",
            destination="Göteborg", status="suggested",
            flights=[{"id": "o", "origin": "HEL"}], hotels=[], source_event_ids=[],
        )
        locked = CalendarTrip(
            start_date=date(2026, 10, 13), end_date=date(2026, 10, 13),
            start_at="2026-10-13T07:15:00+03:00", end_at="2026-10-13T19:55:00+03:00",
            destination="Köpenhamn", status="approved", purpose="Kunddag och möte",
            flights=[], hotels=[], source_event_ids=[],
        )
        s.add_all([sug, locked])
        s.flush()
        s.add(CalendarTripDay(trip_id=sug.id, day_date=date(2026, 11, 18),
                              start_time="07:15", end_time="19:55",
                              destination="Göteborg", meetings=[], source_event_ids=[]))
        s.add(CalendarTripDay(trip_id=locked.id, day_date=date(2026, 10, 13),
                              start_time="07:15", end_time="19:55",
                              destination="Köpenhamn", meetings=[], source_event_ids=[]))
    s = Session()
    report = migrate_c41(s)
    assert report["recalculated"] == 1
    assert [t["id"] for t in report["locked_non_english_purpose"]] == [2]
    trips = {t.status: t for t in s.query(CalendarTrip).all()}
    days = {d.trip_id: d for d in s.query(CalendarTripDay).all()}
    assert trips["suggested"].destination == "Gothenburg"
    assert trips["suggested"].travel_start_at.startswith("2026-11-18T05:35")
    assert (days[1].start_time, days[1].end_time) == ("05:35", "20:55")
    # approved: namn till engelska, men inga tider/syfte ändras.
    assert trips["approved"].destination == "Copenhagen"
    assert trips["approved"].travel_start_at is None
    assert trips["approved"].purpose == "Kunddag och möte"
    assert (days[2].start_time, days[2].end_time) == ("07:15", "19:55")
    # Idempotent.
    assert migrate_c41(s)["recalculated"] == 0 and migrate_c41(s)["renamed"] == 0
    s.close()


def test_mcp_shows_travel_and_flight_times_and_warns(env):
    c, scope, Session = env
    from app.services.calendar_trip_store import persist_detected
    from tests.backend.test_mcp import _full_flow

    s = Session()
    persist_detected(s, detect_trips(_gbg_day_trip() + _cph(26), HOMES))
    s.close()
    at = _full_flow(c)["access_token"]

    trips = _call(c, at, "list_trips")["data"]["trips"]
    gbg = next(t for t in trips if t["start_date"] == "2026-11-18")
    assert gbg["travel_start_helsinki"] == "2026-11-18 05:35"
    assert gbg["travel_end_helsinki"] == "2026-11-18 20:55"
    assert gbg["flight_departure_helsinki"] == "2026-11-18 07:15"
    assert gbg["flight_arrival_helsinki"] == "2026-11-18 19:55"
    assert gbg["country_name"] == "Sweden"

    flagged = _call(c, at, "list_trips", flagged_only=True)["data"]["trips"]
    assert [t["flags"] for t in flagged] == [["destination_unconfirmed"]]
    cph_id = flagged[0]["id"]

    detail = _call(c, at, "get_trip", trip_id=gbg["id"])["data"]
    assert detail["travel_start"]["helsinki"] == "2026-11-18 05:35"
    assert detail["flight_departure"]["helsinki"] == "2026-11-18 07:15"
    assert detail["days"][0]["flight_departure_helsinki"] == "07:15"
    assert detail["days"][0]["start_time_helsinki"] == "05:35"
    assert detail["netvisor_line_description"] == (
        "Business trip Gothenburg, Sweden – Management meeting and team syncs")

    # Manuell överstyrning av resetiden (Mikko åker från kontoret).
    out = _call(c, at, "update_trip", trip_id=gbg["id"], start="2026-11-18T06:00",
                purpose="Kunddag och möte")["data"]
    assert out["travel_start"]["helsinki"] == "2026-11-18 06:00"
    assert out["flight_departure"]["helsinki"] == "2026-11-18 07:15"
    assert out["days"][0]["start_time_helsinki"] == "06:00"
    assert out["purpose"] == "Kunddag och möte"  # sparas ändå
    assert any("English" in w for w in out["warnings"])

    # Mikko anger orten → flaggan försvinner.
    out = _call(c, at, "update_trip_day", trip_id=cph_id, date="2026-11-26",
                destination="Ljungby")["data"]
    assert out["flags"] == []
    assert out["destination"] == "Ljungby"
    assert out["days"][0]["destination_source"] == "manual"
    assert "warnings" not in out


# ---------- Del 4: MCP-inloggning ----------


def _consent_fields(page):
    rid = re.search(r'name="request_id" value="([^"]+)"', page).group(1)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    return rid, csrf


def test_authorize_without_session_then_login_then_allow(env):
    c, _, _ = env
    client_id = _register(c)
    verifier, challenge = _pkce()
    r = _authorize(c, client_id, challenge)
    assert r.status_code == 303
    nxt = parse_qs(urlsplit(r.headers["location"]).query)["next"][0]
    # Bara ett kort id i next (inget som kan tappas eller kodas fel).
    assert nxt.startswith("/oauth/authorize?request_id=") and "client_id" not in nxt
    r = c.post("/login", data={"password": "test-password", "next": nxt})
    assert r.headers["location"] == nxt
    r = c.get(nxt)
    assert r.status_code == 200 and "Tillåt" in r.text
    rid, csrf = _consent_fields(r.text)
    r = c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "allow"})
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["state"] == ["st-1"] and q["code"]
    tok = c.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"][0],
        "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "client_id": client_id,
        "code_verifier": verifier, "resource": "https://testserver/mcp",
    })
    assert tok.status_code == 200 and tok.json()["access_token"]

    # Det som hände i prod: formuläret skickades en gång till. Nu en
    # vänlig sida (200), ingen ny kod och inget fel.
    r = c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "allow"})
    assert r.status_code == 200 and "Redan klart" in r.text


def test_reloaded_consent_page_does_not_invalidate_first(env):
    c, _, _ = env
    client_id = _register(c)
    _, challenge = _pkce()
    c.post("/login", data={"password": "test-password", "next": "/"})
    first = _authorize(c, client_id, challenge)
    _authorize(c, client_id, challenge)  # omladdning / andra flik
    rid, csrf = _consent_fields(first.text)
    r = c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "allow"})
    assert r.status_code == 302 and "code=" in r.headers["location"]


def test_expired_pending_request_still_fails(env):
    c, scope, _ = env
    from app.models import McpOAuthPending

    client_id = _register(c)
    _, challenge = _pkce()
    r = _authorize(c, client_id, challenge)
    nxt = parse_qs(urlsplit(r.headers["location"]).query)["next"][0]
    c.post("/login", data={"password": "test-password", "next": nxt})
    page = c.get(nxt).text
    rid, csrf = _consent_fields(page)
    with scope() as s:
        s.query(McpOAuthPending).update(
            {McpOAuthPending.expires_at: datetime.utcnow() - timedelta(seconds=1)})
    r = c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": "allow"})
    assert r.status_code == 400
    assert "Ogiltig eller utgången begäran. Börja om från Claude." in r.text
    assert c.get(nxt).status_code == 400
    # Fel csrf → också fel.
    r2 = _authorize(c, client_id, challenge)
    rid2, _ = _consent_fields(r2.text)
    r = c.post("/oauth/authorize", data={"request_id": rid2, "csrf": "x", "decision": "allow"})
    assert r.status_code == 400


def test_pending_lifetime_at_least_10_minutes():
    from app.mcp_oauth import PENDING_TTL

    assert PENDING_TTL >= timedelta(minutes=10)


def test_deploy_check_client_removed(env):
    c, scope, Session = env
    from app.mcp_oauth import delete_clients_named
    from app.models import McpOAuthClient

    r = c.post("/oauth/register", json={"client_name": "deploy-check",
                                        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]})
    assert r.status_code == 201
    keep = _register(c)
    s = Session()
    assert delete_clients_named(s, "deploy-check") == 1
    assert [x.client_id for x in s.query(McpOAuthClient).all()] == [keep]
    assert delete_clients_named(s, "deploy-check") == 0
    s.close()
