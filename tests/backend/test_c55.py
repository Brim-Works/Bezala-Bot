"""C55 - Netvisor-beskrivning enligt Riikka: 'Travel for <purpose> in <city>'."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import date

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

import app.models_netvisor  # noqa: E402,F401
from app.services.trip_english import (  # noqa: E402
    NETVISOR_DESCRIPTION_RE,
    netvisor_description_warnings,
    netvisor_line_description,
)
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c49 import _mk, trip_79, trip_93, trip_130  # noqa: E402


def test_purpose_and_city_exact():
    assert netvisor_line_description(["Helsingborg"], "Management meeting") == (
        "Travel for management meeting in Helsingborg")
    assert netvisor_line_description(
        ["Helsingborg"], "Management meeting, Helsingborg office.") == (
        "Travel for management meeting in Helsingborg")


def test_multiple_cities():
    assert netvisor_line_description(["Malmö → Helsingborg"], "Customer meeting") == (
        "Travel for customer meeting in Malmo and Helsingborg")
    assert netvisor_line_description(["Malmö", "Helsingborg"], "Customer meeting") == (
        "Travel for customer meeting in Malmo and Helsingborg")


def test_missing_purpose():
    text = netvisor_line_description(["Oslo"], None)
    assert text == "Travel in Oslo"
    assert NETVISOR_DESCRIPTION_RE.match(text)
    assert netvisor_line_description(["Oslo"], "  ") == "Travel in Oslo"


def test_swedish_city_names_become_english():
    assert netvisor_line_description(["Göteborg"], "Workshop") == (
        "Travel for workshop in Gothenburg")
    assert netvisor_line_description(["Köpenhamn"], "Workshop") == (
        "Travel for workshop in Copenhagen")
    assert netvisor_line_description(["Malmö"], "Workshop") == "Travel for workshop in Malmo"


def test_long_dash_replaced_and_acronym_kept():
    text = netvisor_line_description(["Båstad"], "HRM SE kick–off, Båstad")
    assert text == "Travel for HRM SE kick-off in Bastad"
    assert "–" not in text


def test_warnings():
    assert netvisor_description_warnings("Travel for management meeting in Helsingborg") == []
    assert netvisor_description_warnings("Travel in Oslo") == []
    w = " | ".join(netvisor_description_warnings(
        "Business trip Oslo, Norway - Audit (1 free meal: lunch)"))
    assert "does not match" in w and "free meal" in w
    assert any("long dash" in x for x in netvisor_description_warnings(
        "Travel for audit – meeting in Oslo"))
    assert any("numbers" in x for x in netvisor_description_warnings(
        "Travel for meeting 120 km in Oslo"))


# Syftena för alla 22 aktiva resor 2026 i prod (2026-11-23).
PROD = [
    (93, ["Helsingborg", "Helsingborg"], "Team syncs and management meeting, Helsingborg office",
     "Travel for team syncs and management meeting in Helsingborg"),
    (130, ["Ängelholm", "Ljungby", "Ljungby"],
     "HRM SE leadership meeting and office meetings, Ljungby",
     "Travel for HRM SE leadership meeting and office meetings in Angelholm and Ljungby"),
    (72, ["Helsingborg"], "Meetings, Helsingborg office", "Travel for meetings in Helsingborg"),
    (79, ["Båstad", "Båstad"], "HRM SE kick-off, Båstad", "Travel for HRM SE kick-off in Bastad"),
    (137, ["Malmö", "Malmö"],
     "Meetings, Malmö office; Management meeting and team syncs, Malmö office",
     "Travel for management meeting and team syncs in Malmo"),
    (82, ["Malmö", "Helsingborg"],
     "Steering group and management meetings, Malmö and Helsingborg offices",
     "Travel for steering group and management meetings in Malmo and Helsingborg"),
    (139, ["Gothenburg", "Gothenburg"], "Meetings, Gothenburg; Team syncs, Gothenburg",
     "Travel for team syncs in Gothenburg"),
    (140, ["Oslo", "Oslo"], "Internal audit and management meeting, Oslo office",
     "Travel for internal audit and management meeting in Oslo"),
    (142, ["Malmö"], "Customer day Agda PS and team syncs, Malmö",
     "Travel for customer day Agda PS and team syncs in Malmo"),
    (147, ["Oslo", "Oslo"], "Team syncs, Oslo; Meetings, Oslo", "Travel for team syncs in Oslo"),
]


@pytest.mark.parametrize("trip_id,cities,purpose,expected", PROD)
def test_prod_purposes(trip_id, cities, purpose, expected):
    text = netvisor_line_description(cities, purpose)
    assert text == expected, trip_id
    assert netvisor_description_warnings(text) == []


def test_entry_description_equals_every_linedescription(db):
    from app.services.netvisor_entry import build_entry
    from app.services.netvisor_payload import preview

    trip_79(db)  # två rader (Inklusive kost dag 1), 3 fria måltider
    e = build_entry(db, 79)
    assert e["description"] == "Travel for HRM SE kick-off in Bastad"
    assert len(e["rows"]) == 2
    assert all(r["description"] == e["description"] for r in e["rows"])
    assert e["rows"][1]["meals_note"] == "(1 free meal)"  # bara internt
    out = preview(db, 79)
    te = ET.fromstring(out["xml"]).find("tripexpense")
    assert te.findtext("description") == e["description"]
    lines = [ln.findtext("linedescription")
             for ln in te.findall("dailycompensationlines/dailycompensationline")]
    assert lines == [e["description"]] * 2
    assert "free meal" not in out["xml"] and "free meal" not in str(out["json"])
    assert "Business trip Båstad" in te.findtext("header")  # rubriken oförändrad


def test_entry_purpose_missing_flag(db):
    from app.services.netvisor_entry import build_entry

    _mk(db, 400, date(2026, 6, 1), date(2026, 6, 1), [{"destination": "Oslo"}],
        destination="Oslo", country="NO", purpose=None,
        travel_start="2026-06-01T06:00:00+03:00", travel_end="2026-06-01T21:00:00+03:00")
    e = build_entry(db, 400)
    assert e["description"] == "Travel in Oslo"
    assert e["flags"] == ["purpose_missing"]
    assert any("Purpose is missing" in w for w in e["warnings"])
    assert all(r["description"] == "Travel in Oslo" for r in e["rows"])


def test_purpose_from_calendar_when_trip_has_none(db):
    from app.models import CalendarTripDay
    from app.services.netvisor_entry import build_entry

    _mk(db, 401, date(2026, 6, 3), date(2026, 6, 3), [{"destination": "Malmö"}],
        destination="Malmö", purpose=None,
        travel_start="2026-06-03T06:00:00+03:00", travel_end="2026-06-03T21:00:00+03:00")
    day = db.query(CalendarTripDay).filter_by(trip_id=401).one()
    day.meetings = [{"summary": "Ledningsgrupp Malmö"}]
    db.commit()
    e = build_entry(db, 401)
    assert e["description"] == "Travel for management meeting in Malmo"
    assert e["purpose_source"] == "calendar" and e["flags"] == []

    # Bara generiska möten -> gissa inte.
    day.meetings = [{"summary": "Lunch"}]
    db.commit()
    e = build_entry(db, 401)
    assert e["description"] == "Travel in Malmo" and e["flags"] == ["purpose_missing"]


def test_trip_detail_and_preview_list(db):
    from app.models import CalendarTrip
    from app.services.calendar_trip_edit import trip_detail
    from app.services.netvisor_status import list_status, preview_descriptions

    trip_93(db)
    trip_130(db)
    _mk(db, 402, date(2026, 7, 1), date(2026, 7, 1), [{}], destination="Oslo",
        country="NO", purpose=None, status="suggested")
    _mk(db, 403, date(2026, 7, 2), date(2026, 7, 2), [{}], destination="Oslo",
        country="NO", purpose="x", status="rejected")
    d = trip_detail(db, db.get(CalendarTrip, 93))
    assert d["netvisor_line_description"] == d["netvisor_description"] == (
        "Travel for team syncs and management meeting in Helsingborg")
    out = preview_descriptions(db, 2026)
    assert [r["trip_id"] for r in out["trips"]] == [93, 130, 402]
    assert out["purpose_missing"] == [402]
    assert out["with_warnings"] == []
    st = {r["trip_id"]: r for r in list_status(db, 2026)}
    assert st[93]["netvisor_description"].startswith("Travel for team syncs")
