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
    is_relevant_meeting,
)

HEL = timezone(timedelta(hours=2))
STO = timezone(timedelta(hours=1))
HOMES = ("helsinki", "helsingfors", "vantaa", "hel")


def ev(eid, summary, start, end, *, location="", event_type="default",
       visibility="default", transparency="opaque", response="accepted",
       others=1, others_accepted=None, is_stream=False):
    """Default: accepterat möte med en annan deltagare (relevant möte)."""
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
        "transparency": transparency,
        "visibility": visibility,
        "self_response": response,
        "attendees_other": others,
        "attendees_other_accepted": others if others_accepted is None else others_accepted,
        "has_room": False,
        "is_stream": is_stream,
    }


def flight(eid, dest, start, end, no="AY 801", origin=""):
    return ev(eid, f"Flight to {dest} ({no})", start, end, event_type="fromGmail",
              location=origin, others=0)


# ---------- Klassificering ----------


def test_classify_flight_hotel_meeting():
    d = datetime(2026, 3, 2, 7, 0, tzinfo=HEL)
    assert classify(flight("f", "Stockholm", d, d)) == "flight"
    assert classify(ev("x", "Finnair Helsinki Vantaa (HEL) - Copenhagen Kastrup (CPH)",
                       d, d)) == "flight"
    assert classify(ev("h", "Stay at Scandic Continental", d, d)) == "hotel"
    assert classify(ev("m", "Q2 review", d, d)) == "meeting"
    assert classify(ev("m2", "Finnair kundmöte", d, d)) == "meeting"
    assert classify(ev("b", "Födelsedag", d, d, event_type="birthday")) == "ignored"
    assert classify(ev("w", "Home", d, d, event_type="WORKING_LOCATION")) == "ignored"


def test_flight_destination_variants():
    d = datetime(2026, 3, 2, 7, 0, tzinfo=HEL)
    g = lambda s: ev("a", s, d, d, event_type="fromGmail")  # noqa: E731
    assert flight_destination(g("Flight to Stockholm (AY 801)")) == "Stockholm"
    assert flight_destination(g("Flight to CPH (AY 959)")) == "Copenhagen"
    assert flight_destination(g("Flight to HEL (AY 960)")) == "Helsinki"


# ---------- C39: fel från första prod-körningen ----------


def test_c39_other_peoples_flights_are_not_flights():
    """Fel 1: bara Gmail-'Flight to … (XX nnn)' och exakt Finnair-mönster."""
    d = datetime(2026, 8, 22, 1, 35, tzinfo=HEL)
    assert classify(ev("x", "Filippas flyg landar LH2466", d, d, others=0)) != "flight"
    assert classify(ev("y", "Italienarna landar AY1772", d, d, others=0)) != "flight"
    # "Flight to" utan Gmail-eventtyp är inte ett flyg.
    assert classify(ev("z", "Flight to Oslo (AY 911)", d, d)) != "flight"
    # Finnair-event som inte följer mönstret.
    assert classify(ev("f", "Finnair AY 123 HEL-ARN", d, d)) != "flight"
    events = [
        ev("ita", "Italienarna landar AY1772", datetime(2026, 7, 2, 13, 40, tzinfo=HEL),
           datetime(2026, 7, 2, 18, 10, tzinfo=HEL), visibility="private", others=0),
        ev("fil", "Filippas flyg landar LH2466", datetime(2026, 8, 22, 1, 35, tzinfo=HEL),
           datetime(2026, 8, 22, 2, 0, tzinfo=HEL), others=0),
        flight("o", "Copenhagen", datetime(2026, 8, 25, 6, 20, tzinfo=HEL),
               datetime(2026, 8, 25, 8, 0, tzinfo=HEL), no="AY 951", origin="Helsinki HEL"),
        flight("r", "Helsinki", datetime(2026, 8, 26, 20, 25, tzinfo=HEL),
               datetime(2026, 8, 26, 22, 0, tzinfo=HEL), no="AY 962",
               origin="Copenhagen CPH"),
    ]
    trips = detect_trips(events, HOMES)["trips"]
    # C41: CPH utan ledtråd → Sverige, orten obekräftad.
    assert [(t["start_date"], t["destination"], t["country"]) for t in trips] == [
        ("2026-08-25", None, "SE"),
    ]


def test_c39_manual_finnair_duplicate_is_merged():
    """Fel 2: manuellt Finnair-event med samma start/slut som Gmail-flyg."""
    s1, e1 = datetime(2026, 4, 22, 18, 0, tzinfo=HEL), datetime(2026, 4, 22, 19, 40, tzinfo=HEL)
    s2, e2 = datetime(2026, 4, 23, 20, 25, tzinfo=HEL), datetime(2026, 4, 23, 22, 0, tzinfo=HEL)
    events = [
        ev("m1", "Finnair Helsinki Vantaa (HEL) - Copenhagen Kastrup (CPH)", s1, e1,
           location="Helsinki Vantaa", others=0),
        flight("g1", "CPH", s1, e1, no="AY 961"),
        ev("m2", "Finnair Copenhagen Kastrup (CPH) - Helsinki Vantaa (HEL)", s2, e2,
           location="Copenhagen Kastrup", others=0),
        flight("g2", "HEL", s2, e2, no="AY 962"),
    ]
    res = detect_trips(events, HOMES)
    assert len(res["trips"]) == 1
    t = res["trips"][0]
    assert t["destination"] is None and t["country"] == "SE"
    assert t["flags"] == ["destination_unconfirmed"]
    assert [f["id"] for f in t["flights"]] == ["g1", "g2"]
    assert t["flights"][0]["duplicate_ids"] == ["m1"]
    assert {"m1", "m2"} <= set(t["source_event_ids"])
    assert res["stats"]["flights"] == 2


def test_c39_manual_finnair_alone_is_flight():
    s, e = datetime(2026, 6, 1, 7, 0, tzinfo=HEL), datetime(2026, 6, 1, 8, 0, tzinfo=HEL)
    events = [
        ev("m1", "Finnair Helsinki (HEL) - Oslo (OSL)", s, e, others=0),
        ev("m2", "Finnair Oslo (OSL) - Helsinki (HEL)", s + timedelta(hours=10),
           e + timedelta(hours=10), others=0),
    ]
    t = detect_trips(events, HOMES)["trips"][0]
    assert t["destination"] == "Oslo" and t["flags"] == []


def test_c39_outbound_from_home_closes_previous_trip():
    """Fel 3: utresa från HEL betyder att Mikko var hemma emellan."""
    events = [
        flight("cph", "Copenhagen Kastrup", datetime(2026, 11, 26, 11, 35, tzinfo=HEL),
               datetime(2026, 11, 26, 13, 15, tzinfo=HEL), no="AY 957",
               origin="Helsinki Vantaa HEL"),
        ev("hotel", "Stay at Scandic Sjølyst", date(2026, 11, 27), date(2026, 11, 29)),
        flight("osl", "Oslo", datetime(2026, 11, 27, 7, 20, tzinfo=HEL),
               datetime(2026, 11, 27, 8, 55, tzinfo=HEL), no="AY 911", origin="Helsinki HEL"),
        flight("ret", "Helsinki", datetime(2026, 11, 28, 13, 30, tzinfo=HEL),
               datetime(2026, 11, 28, 14, 55, tzinfo=HEL), no="AY 914", origin="Oslo OSL"),
    ]
    trips = detect_trips(events, HOMES)["trips"]
    assert [(t["start_date"], t["end_date"], t["destination"], t["flags"]) for t in trips] == [
        ("2026-11-26", "2026-11-26", None, ["missing_return", "destination_unconfirmed"]),
        ("2026-11-27", "2026-11-28", "Oslo", []),
    ]
    assert trips[0]["open_return"] is True
    assert trips[0]["hotels"] == []
    assert trips[1]["days"][0]["hotel"]["name"] == "Scandic Sjølyst"


def test_c39_connection_from_abroad_stays_one_trip():
    events = [
        flight("o1", "Copenhagen", datetime(2026, 9, 1, 7, 0, tzinfo=HEL),
               datetime(2026, 9, 1, 8, 30, tzinfo=HEL), origin="Helsinki HEL"),
        flight("o2", "Oslo", datetime(2026, 9, 2, 9, 0, tzinfo=HEL),
               datetime(2026, 9, 2, 10, 0, tzinfo=HEL), origin="Copenhagen CPH"),
        flight("r", "Helsinki", datetime(2026, 9, 3, 12, 0, tzinfo=HEL),
               datetime(2026, 9, 3, 15, 0, tzinfo=HEL), origin="Oslo OSL"),
    ]
    trips = detect_trips(events, HOMES)["trips"]
    assert len(trips) == 1
    # C41: dag 1 i CPH utan ledtråd ärver ingen ort från Oslo-dagarna
    # (annan flygplats) → obekräftad; Oslo-dagarna har Oslo.
    assert trips[0]["destination"] == "Oslo"
    assert trips[0]["country"] == "SE,NO"
    assert [d["airport"] for d in trips[0]["days"]] == ["CPH", "OSL", "OSL"]


def test_c39_two_outbounds_same_day_needs_review():
    """Fel 4: AY 951 06:20 + AY 953 07:25 → needs_review, båda sparas."""
    events = [
        flight("a", "Copenhagen", datetime(2026, 8, 25, 6, 20, tzinfo=HEL),
               datetime(2026, 8, 25, 8, 0, tzinfo=HEL), no="AY 951", origin="Helsinki HEL"),
        flight("b", "Copenhagen", datetime(2026, 8, 25, 7, 25, tzinfo=HEL),
               datetime(2026, 8, 25, 9, 5, tzinfo=HEL), no="AY 953", origin="Helsinki HEL"),
        flight("r", "Helsinki", datetime(2026, 8, 26, 20, 25, tzinfo=HEL),
               datetime(2026, 8, 26, 22, 0, tzinfo=HEL), no="AY 962",
               origin="Copenhagen CPH"),
    ]
    res = detect_trips(events, HOMES)
    assert len(res["trips"]) == 1
    t = res["trips"][0]
    assert t["flags"] == ["needs_review", "destination_unconfirmed"]
    assert [o["flight_no"] for o in t["flight_options"]] == ["AY 951", "AY 953"]
    # Fixturen har +02:00 i augusti (sommartid +03:00): landning 22:00+02
    # = 23:00 lokal + 60 min → resan slutar 00:00 den 27:e.
    assert (t["start_date"], t["end_date"]) == ("2026-08-25", "2026-08-27")
    assert t["open_return"] is False


def test_c39_city_names_normalized_via_iata():
    """Fel 5 (C41: engelska namn), inga Tukholma/Goteborg/Copenhagen Kastrup."""
    d = datetime(2026, 4, 30, 7, 15, tzinfo=HEL)
    g = lambda s: ev("a", s, d, d, event_type="fromGmail")  # noqa: E731
    assert flight_destination(g("Flight to Tukholma (AY 803)")) == "Stockholm"
    assert flight_destination(g("Flight to Goteborg (AY 861)")) == "Gothenburg"
    assert flight_destination(g("Flight to Gothenburg (AY 861)")) == "Gothenburg"
    assert flight_destination(g("Flight to Copenhagen Kastrup (AY 957)")) == "Copenhagen"
    assert flight_destination(g("Flight to OSL (AY 911)")) == "Oslo"
    assert flight_destination(
        g("Flight to HELSINKI HELSINKI VANTAA (AY 864)")) == "Helsinki"
    assert flight_destination(g("Flight to ARN (AY 801)")) == "Stockholm"


def test_c39_meeting_filter():
    """Fel 6: mötesfilter per resdag."""
    d0 = datetime(2026, 11, 18, 9, 0, tzinfo=HEL)
    d1 = d0 + timedelta(hours=1)
    keep = [
        ev("k1", "Management Meeting", d0, d1, visibility="private", others=5),
        ev("k2", "Mikko & Måns Synk", d0, d1, others=1,
           location="HEL-RAU-6-Pixel (1), GOT-LIN-19-193 (2)"),
        ev("k3", "Agda PS Kunddag Malmö ", d0, d1, response="needsAction", others=18),
        ev("k4", "Agda Kunddagar Malmö", d0, d1, response=None, others=0),
        ev("k5", "Rum", d0, d1, response=None, others=0, location="OSL-SKO-3-Stockholm (6)"),
    ]
    drop = [
        ev("d1", "Läxläsning Stella", d0, d1, visibility="private", response=None, others=0),
        ev("d2", "WOD", d0, d1, transparency="transparent", response=None, others=0),
        ev("d3", "WOOD / Remppa", d0, d1, transparency="transparent", others=0),
        ev("d4", "Transport", d0, d1, visibility="private", response=None, others=0),
        ev("d5", "Product steering group", d0, d1, response="needsAction", others=10),
        ev("d6", "TooEasy", d0, d1, response=None, others=0),
        ev("d7", "[Live stream] Visma AI Conference Keynote", d0, d1, others=0),
        ev("d8", "Auntie Webinar (ENG): prioritise", d0, d1, response="needsAction", others=0),
        ev("d9", "Handbollsträning", d0, d1, visibility="private", others=1,
           others_accepted=0),
        ev("d10", "Deadline MD rapport", d0, d1, transparency="transparent", others=10),
        ev("d11", "Weekly", d0, d1, response="declined", others=2),
        ev("d12", "Visma Talks", d0, d1, others=0, is_stream=True),
    ]
    for e in keep:
        assert is_relevant_meeting(e), e["summary"]
    for e in drop:
        assert not is_relevant_meeting(e), e["summary"]

    events = [
        flight("o", "Goteborg", datetime(2026, 11, 18, 7, 15, tzinfo=HEL),
               datetime(2026, 11, 18, 8, 45, tzinfo=HEL), no="AY 861", origin="Helsinki HEL"),
        *keep, *drop,
        flight("r", "Helsinki", datetime(2026, 11, 18, 18, 30, tzinfo=HEL),
               datetime(2026, 11, 18, 19, 55, tzinfo=HEL), no="AY 866",
               origin="Gothenburg GOT"),
    ]
    t = detect_trips(events, HOMES)["trips"][0]
    assert t["destination"] == "Gothenburg"
    assert sorted(m["id"] for m in t["days"][0]["meetings"]) == sorted(e["id"] for e in keep)


def test_c39_normalize_event_attendees():
    raw = {
        "id": "m", "summary": "Möte", "visibility": "private",
        "start": {"dateTime": "2026-11-18T09:30:00+02:00"},
        "end": {"dateTime": "2026-11-18T10:30:00+02:00"},
        "attendees": [
            {"email": "me@x", "self": True, "responseStatus": "accepted"},
            {"email": "a@x", "responseStatus": "accepted"},
            {"email": "b@x", "responseStatus": "declined"},
            {"email": "c@x", "responseStatus": "needsAction"},
            {"email": "room@resource.calendar.google.com", "resource": True,
             "responseStatus": "accepted"},
        ],
        "conferenceData": {"videoEntryPoint": {"uri": "https://stream.meet.google.com/x"}},
    }
    e = normalize_event(raw)
    assert e["self_response"] == "accepted"
    assert e["attendees_other"] == 2
    assert e["attendees_other_accepted"] == 1
    assert e["has_room"] is True
    assert e["is_stream"] is True
    assert e["visibility"] == "private"


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
    # C41: restid = 07:30 − 60 − 40 / 20:00 + 20 + 40.
    assert day["start_time"] == "05:50"
    assert day["end_time"] == "21:00"
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
    # Fixturen har +02:00 men april är sommartid (+03:00) i Helsingfors:
    # 16:00+02 = 17:00 lokal − 100 min = 15:20.
    assert d1["start_time"] == "15:20" and d1["end_time"] is None
    assert d1["hotel"] == {"id": "hotel", "name": "Scandic Klara"}
    assert d2["start_time"] is None and d2["end_time"] == "22:05"
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
    # C44: hotell utan flyg (inrikes/okänd ort) blir en resa som Mikko
    # bekräftar — flaggad domestic_unconfirmed.
    assert len(res["trips"]) == 1
    t = res["trips"][0]
    assert (t["start_date"], t["end_date"]) == ("2026-06-03", "2026-06-04")
    assert "domestic_unconfirmed" in t["flags"]
    assert t["flights"] == []
    assert res["ungrouped"] == []
    assert res["stats"]["meetings"] == 2


def test_hotel_abroad_without_flights_stays_ungrouped():
    events = [ev("h1", "Hotel Oslo Plaza", date(2026, 6, 3), date(2026, 6, 4))]
    res = detect_trips(events, HOMES)
    assert res["trips"] == []
    assert [(u["id"], u["reason"]) for u in res["ungrouped"]] == [
        ("h1", "hotel_without_flight"),
    ]


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
    # C41: arbetsort per dag. Byte i Stockholm samma dag → London hela dagen.
    assert trip["destination"] == "London"
    assert trip["days"][0]["airport"] == "LHR"
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


def test_c39_refresh_persists_flags_and_options(client):
    c, db_module = client
    from app.models import CalendarTrip

    events = [
        flight("a", "Copenhagen", datetime(2026, 8, 25, 6, 20, tzinfo=HEL),
               datetime(2026, 8, 25, 8, 0, tzinfo=HEL), no="AY 951", origin="Helsinki HEL"),
        flight("b", "Copenhagen", datetime(2026, 8, 25, 7, 25, tzinfo=HEL),
               datetime(2026, 8, 25, 9, 5, tzinfo=HEL), no="AY 953", origin="Helsinki HEL"),
        flight("r", "Helsinki", datetime(2026, 8, 26, 20, 25, tzinfo=HEL),
               datetime(2026, 8, 26, 22, 0, tzinfo=HEL), no="AY 962", origin="Copenhagen CPH"),
        flight("x", "Oslo", datetime(2026, 11, 27, 7, 20, tzinfo=HEL),
               datetime(2026, 11, 27, 8, 55, tzinfo=HEL), no="AY 911", origin="Helsinki HEL"),
    ]
    # En redigerad resa som överlappar Oslo-resan får inte skrivas över.
    with db_module.session_scope() as s:
        s.add(CalendarTrip(start_date=date(2026, 11, 27), end_date=date(2026, 11, 28),
                           status="edited", destination="Oslo (manuell)"))
    with patch("app.services.calendar_client.fetch_events", return_value=events):
        body = c.post("/api/trips/refresh").json()
    assert body["saved"]["skipped_locked"] == 1
    trips = c.get("/api/trips/suggestions?source=calendar").json()["trips"]
    by_status = {t["status"]: t for t in trips}
    assert by_status["edited"]["destination"] == "Oslo (manuell)"
    aug = by_status["suggested"]
    assert aug["flags"] == ["needs_review", "destination_unconfirmed"]
    assert aug["country"] == "SE"
    assert [o["flight_no"] for o in aug["flight_options"]] == ["AY 951", "AY 953"]
