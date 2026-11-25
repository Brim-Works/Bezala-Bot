"""C44 — måltider, Moovy-tider, kopplingsförslag och detektorfynd.

Ett test per punkt, med de verkliga fallen från granskningen 2026.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services import receipt_details as rd  # noqa: E402
from app.services.trip_detector import detect_trips  # noqa: E402
from tests.backend.test_c42 import _cl, _items, _receipt, _trip, db  # noqa: E402,F401
from tests.backend.test_trip_detector import HOMES, ev  # noqa: E402

HEL_TZ = ZoneInfo("Europe/Helsinki")
CET = ZoneInfo("Europe/Stockholm")


def gflight(eid, dest_text, no, start, end, origin_loc):
    """Gmail-flyg ("Flight to … (AY 957)")."""
    return ev(eid, f"Flight to {dest_text} ({no})", start, end,
              location=origin_loc, event_type="fromGmail", others=0)


# =====================================================================
# Del 1 — fria måltider
# =====================================================================


def _bastad_events():
    return [
        gflight("f1", "Copenhagen", "AY 951", datetime(2026, 5, 7, 6, 20, tzinfo=HEL_TZ),
                datetime(2026, 5, 7, 7, 0, tzinfo=CET), "Helsinki HEL"),
        # AY 960 avgår 17:00 lokal tid (18:00 i Helsingfors) — före kvällen.
        gflight("f2", "HELSINKI HELSINKI VANTAA", "AY 960",
                datetime(2026, 5, 8, 17, 0, tzinfo=CET),
                datetime(2026, 5, 8, 19, 35, tzinfo=HEL_TZ), "Copenhagen CPH"),
        ev("k1", "HRM SE kick-off Båstad", date(2026, 5, 7), date(2026, 5, 9), others=12),
        ev("h1", "Stay at Hotel Skansen", date(2026, 5, 7), date(2026, 5, 8), others=0),
    ]


def test_kickoff_dinner_bastad():
    trip = detect_trips(_bastad_events(), HOMES)["trips"][0]
    days = {d["date"]: d for d in trip["days"]}
    assert days["2026-05-07"]["free_meals"] == 1
    assert days["2026-05-07"]["free_meal_sources"] == ["dinner_kickoff"]
    # 8 maj: frukost loggas (hotell, inrikes-regeln) men räknas inte, och
    # hemresan går före kvällen → ingen kick-off-middag.
    assert days["2026-05-08"]["free_meal_sources"] == ["hotel_breakfast"]
    assert days["2026-05-08"]["free_meals"] == 0


def test_board_dinner_13_okt_and_customer_day_lunch():
    events = [
        gflight("f1", "Goteborg", "AY 861", datetime(2026, 10, 13, 7, 15, tzinfo=HEL_TZ),
                datetime(2026, 10, 13, 7, 45, tzinfo=CET), "Helsinki HEL"),
        gflight("f2", "HELSINKI HELSINKI VANTAA", "AY 868",
                datetime(2026, 10, 13, 20, 55, tzinfo=CET),
                datetime(2026, 10, 13, 23, 15, tzinfo=HEL_TZ), "Goteborg GOT"),
        ev("b4", "Placeholder Board #4 Strategy & Budget (on site with dinner)",
           datetime(2026, 10, 13, 13, 45, tzinfo=HEL_TZ),
           datetime(2026, 10, 13, 22, 0, tzinfo=HEL_TZ), location="GOT-LIN-18-Tempus"),
        ev("c1", "Kunddag Agda PS", datetime(2026, 10, 13, 9, 0, tzinfo=HEL_TZ),
           datetime(2026, 10, 13, 12, 0, tzinfo=HEL_TZ)),
    ]
    day = detect_trips(events, HOMES)["trips"][0]["days"][0]
    assert sorted(day["free_meal_sources"]) == ["dinner_calendar", "lunch_customer_day"]
    assert day["free_meals"] == 2


def test_breakfast_setting_and_scandic_go(monkeypatch):
    from app.services.trip_meals import count_meals, hotel_has_breakfast, meals_for_day

    assert not hotel_has_breakfast("Scandic Go, Gasverksgatan 11")
    assert hotel_has_breakfast("Clarion Hotel Sea U")
    # Scandic Go: ingen frukost ens i källorna.
    assert meals_for_day([], "Scandic Go, Gasverksgatan 11", "SE") == (0, [])
    # C46: frukost räknas aldrig, inte ens med den gamla inställningen.
    assert count_meals(["hotel_breakfast"], "SE") == 0
    monkeypatch.setenv("TRIP_BREAKFAST_COUNTS_AS_MEAL_ABROAD", "true")
    assert count_meals(["hotel_breakfast"], "SE") == 0
    # Inrikes räknas hotellfrukost aldrig.
    assert count_meals(["hotel_breakfast"], "FI") == 0


def test_update_trip_day_meals_on_edited_trip_and_netvisor_line(db):
    from app.services import calendar_trip_edit as edit

    t = _trip(db, date(2026, 5, 7), date(2026, 5, 8), status="edited",
              destination="Båstad")
    t.purpose = "HRM SE kick-off, Båstad"
    db.commit()
    out = edit.update_trip_day(db, t.id, "2026-05-07", free_meal_sources=["dinner_kickoff"])
    day = out["days"][0]
    assert (day["free_meals"], day["free_meal_sources"], day["meals_source"]) == (
        1, ["dinner_kickoff"], "manual")
    assert out["status"] == "edited"
    # C55 - måltider aldrig i Netvisor-texten, bara i botens meals_note.
    assert out["netvisor_line_description"] == "Travel for HRM SE kick-off in Bastad"
    assert out["meals_note"] == "(1 free meal: dinner)"
    with pytest.raises(edit.TripEditError):
        edit.update_trip_day(db, t.id, "2026-05-07", free_meals=4)
    with pytest.raises(edit.TripEditError):
        edit.update_trip_day(db, t.id, "2026-05-07", free_meal_sources=["brunch"])


# =====================================================================
# Del 2 — Moovy-tider
# =====================================================================


@pytest.mark.parametrize("text,start,end", [
    ("Finavia P1 Premium 05.03.2026 05:51, 16h 49min 10s", "2026-03-05 05:11", "2026-03-05 23:20"),
    ("Finavia P1 Premium 07.05.2026 05:19, 1 dygn 15h 00min 0s", "2026-05-07 04:39", "2026-05-08 20:59"),
    ("Finavia P2 13.10.2026 05:49, 17h 48min 5s", "2026-10-13 05:09", "2026-10-14 00:17"),
])
def test_moovy_verified_travel_times(text, start, end):
    from app.services.trip_parking import ParkingReceipt, parking_travel_times

    entry, exit_ = rd.parse_parking(text)
    p = ParkingReceipt.__new__(ParkingReceipt)
    p.entry, p.exit = entry, exit_
    s, e = parking_travel_times(p)
    assert (s.strftime("%Y-%m-%d %H:%M"), e.strftime("%Y-%m-%d %H:%M")) == (start, end)


def _moovy(db, entry_text, day, amount=52.49):
    m = _receipt(db, day, category="Parkering", vendor="Moovy", amount=amount,
                 sender="noreply@moovy.fi", subject="Moovy: kvitto")
    rd.apply_details(m, rd.extract_details("", entry_text, ""))
    db.commit()
    return m


def _with_flights(db, t, dep, arr):
    t.flights = [
        {"id": "f0", "flight_no": "AY 957", "origin": "HEL", "destination_iata": "CPH",
         "start": dep.isoformat(), "end": (dep + timedelta(hours=1)).isoformat()},
        {"id": "f1", "flight_no": "AY 962", "origin": "CPH", "destination_iata": "HEL",
         "start": (arr - timedelta(hours=2)).isoformat(), "end": arr.isoformat()},
    ]
    t.start_at, t.end_at = dep.isoformat(), arr.isoformat()
    db.commit()


def test_moovy_sets_suggested_trip_times_and_parking_item(db):
    from app.services.trip_parking import apply_parking_times

    t = _trip(db, date(2026, 3, 5), date(2026, 3, 5), destination="Helsingborg")
    _with_flights(db, t, datetime(2026, 3, 5, 7, 10, tzinfo=HEL_TZ),
                  datetime(2026, 3, 5, 21, 55, tzinfo=HEL_TZ))
    m = _moovy(db, "Finavia P1 Premium 05.03.2026 05:51, 16h 49min 10s", date(2026, 3, 6))
    assert apply_parking_times(db)["applied"] == 1
    db.refresh(t)
    assert t.time_source == "parking"
    assert t.travel_start_at.startswith("2026-03-05T05:11")
    assert t.travel_end_at.startswith("2026-03-05T23:20")
    park = _items(_cl(db, t), "parking")[0]
    assert park["status"] == "found" and park["receipt"]["message_id"] == m.message_id


def test_moovy_on_edited_trip_is_only_a_suggestion(db):
    from app.services import calendar_trip_edit as edit
    from app.services.trip_parking import apply_parking_times

    t = _trip(db, date(2026, 10, 13), date(2026, 10, 14), status="edited",
              destination="Gothenburg")
    _with_flights(db, t, datetime(2026, 10, 13, 7, 15, tzinfo=HEL_TZ),
                  datetime(2026, 10, 13, 23, 15, tzinfo=HEL_TZ))
    t.travel_start_at = "2026-10-13T06:00:00+03:00"
    db.commit()
    _moovy(db, "Finavia P2 13.10.2026 05:49, 17h 48min 5s", date(2026, 10, 14))
    assert apply_parking_times(db)["suggested_on_locked"] == 1
    out = edit.get_trip(db, t.id)
    assert out["travel_start_helsinki"] == "2026-10-13 06:00"  # oförändrat
    sugg = out["suggestions"]["travel_times"]
    assert sugg["start"].startswith("2026-10-13T05:09")
    assert sugg["end"].startswith("2026-10-14T00:17")


# =====================================================================
# Del 3 — kopplingsförslag, couple_receipt, get_card_line
# =====================================================================


def _card(db, bid, d, desc, amount, currency="EUR", status="missing"):
    from app.models import CardLine

    c = CardLine(bill_line_id=bid, txn_date=d, description=desc, amount=amount,
                 currency=currency, status=status, vendor=desc.split(":")[-1].split(",")[0].strip())
    db.add(c)
    db.commit()
    return c


def test_suggested_receipt_hertz_and_finnair_change_fee(db):
    from app.services.card_line_coupling import suggest_receipts

    hertz = _receipt(db, date(2026, 4, 26), category="Bilhyra", vendor="Hertz",
                     amount=108.92, sender="noreply@hertz.com", drive_file_id="d1",
                     file_name="hertz.pdf")
    fee = _receipt(db, date(2026, 10, 13), category="Flyg", vendor="Finnair",
                   amount=45.0, sender="noreply@finnair.com", kind="change_fee",
                   drive_file_id="d2", file_name="change.pdf")
    c1 = _card(db, "2100001", date(2026, 4, 26), "MIKKO KEINONEN: HERTZ SVERIGE, MALMO, SE 108.92 EUR", 108.92)
    c2 = _card(db, "2100002", date(2026, 10, 13), "MIKKO KEINONEN: FINNAIR, VANTAA, FI 45.00 EUR", 45.0)
    c3 = _card(db, "2100003", date(2026, 10, 13), "MIKKO KEINONEN: FINNAIR, VANTAA, FI 45.00 EUR", 45.0)
    out = suggest_receipts(db, [c1, c2, c3])
    assert out["2100001"]["message_id"] == hertz.message_id
    assert out["2100001"]["score"] >= 75
    # Ombokningsavgiften matchas mot en separat Finnair-kortrad — men ett
    # kvitto föreslås bara för en rad.
    assert [out["2100002"], out["2100003"]].count(None) == 1
    got = out["2100002"] or out["2100003"]
    assert got["message_id"] == fee.message_id and got["kind"] == "change_fee"


def test_list_missing_shows_suggested_receipt_counts(db):
    from app.services.trip_completeness import list_missing

    _receipt(db, date(2026, 4, 26), category="Bilhyra", vendor="Hertz", amount=108.92,
             sender="noreply@hertz.com")
    _card(db, "2100001", date(2026, 4, 26), "MIKKO KEINONEN: HERTZ SVERIGE, MALMO, SE 108.92 EUR", 108.92)
    _card(db, "2100009", date(2026, 4, 20), "MIKKO KEINONEN: UNKNOWN SHOP, MALMO, SE 9.99 EUR", 9.99)
    out = list_missing(db, older_than_days=5, sync=False)
    assert out["counts"]["card_lines_with_suggested_receipt"] == 1
    assert out["counts"]["card_lines_without_candidate"] == 1
    by = {c["bill_line_id"]: c for c in out["card_lines_without_receipt"]}
    assert by["2100001"]["suggested_receipt"]["vendor"] == "Hertz"
    assert by["2100009"]["suggested_receipt"] is None


def test_couple_receipt_runs_match_to_bezala_flow(db):
    from app.services import card_line_coupling as clc

    m = _receipt(db, date(2026, 4, 26), category="Bilhyra", vendor="Hertz", amount=108.92,
                 drive_file_id="d1", file_name="hertz.pdf")
    _card(db, "2100001", date(2026, 4, 26), "MIKKO KEINONEN: HERTZ SVERIGE 108.92 EUR", 108.92)

    def fake_match(*, row, bill_line_id, db, bezala):
        row.bezala_transaction_id = "5550001"
        db.commit()

    with patch("app.main._do_match_to_bezala", side_effect=fake_match) as flow, \
            patch("app.services.bezala_client.BezalaClient", return_value=MagicMock()):
        out = clc.couple_receipt(db, "2100001", m.message_id)
    assert flow.call_args.kwargs["bill_line_id"] == "2100001"
    assert out["bezala_transaction_id"] == "5550001"
    assert "draft" in out["bezala_state"]
    from app.models import CardLine
    # C60 — inte löst förrän kopplingen verifierats i Bezala.
    assert db.query(CardLine).filter_by(bill_line_id="2100001").one().status == \
        "coupling_unverified"
    assert out["status"] == "utkast skapat, koppling inte verifierad än"


def test_get_card_line_yalla_go(db):
    from app.services.card_line_coupling import get_card_line

    _card(db, "2364577", date(2026, 8, 31),
          "MIKKO KEINONEN: SUMUP  *YALLA GO AB, ANGERWD, SE 69.22 EUR", 69.22, status="resolved")
    m = _receipt(db, date(2026, 8, 31), category="Taxi", vendor="Yalla Go", amount=750.0,
                 bezala_transaction_id="5500123", bezala_payment_date="2026-08-31",
                 bezala_payment_amount=69.22, bezala_payment_merchant="SUMUP *YALLA GO AB",
                 matched_at=datetime(2026, 9, 2, 8, 0), upload_source="manual_upload")
    m.currency = "SEK"
    db.commit()
    out = get_card_line(db, "2364577", scan_bezala=False)
    assert out["card_line"]["status"] == "resolved"
    r = out["coupled_receipts"][0]
    assert r["message_id"] == m.message_id and r["vendor"] == "Yalla Go"
    assert r["matched_at"] == "2026-09-02T08:00:00"
    assert "manual upload" in r["how"]
    assert "score" in r


# --- Prio (granskningen): flygkvittot måste matcha resans flyg ---------


AMADEUS_25AUG = """| YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 25AUG2026 |
| Booking Reference: XYZ789 | | Office |
| HELSINKI HELSINKI VANTAA | COPENHAGEN KASTRUP | AY0953 | Y | 25Aug | 07:25 | 08:05 | Ok |
| COPENHAGEN KASTRUP | HELSINKI HELSINKI VANTAA | AY0962 | B | 26Aug | 19:25 | 22:00 | Ok |
| Ticket number | : 105-9999990002 |"""


def test_trip_107_does_not_take_the_25_aug_flight_receipt(db):
    t = _trip(db, date(2026, 11, 10), date(2026, 11, 11), flight_nos=("AY 957", "AY 962"),
              destination="Helsingborg")
    t.flights[0]["start"] = "2026-11-10T11:35:00+02:00"
    t.flights[1]["start"] = "2026-11-11T19:25:00+01:00"
    db.commit()
    aug = _receipt(db, date(2026, 8, 25), category="Flyg", vendor="Finnair", amount=674.48,
                   subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 25AUG2026",
                   summary="Finnair HEL-CPH AY953 / AY962")
    rd.apply_details(aug, rd.extract_details(aug.subject, AMADEUS_25AUG, ""))
    db.commit()
    assert _items(_cl(db, t), "flight")[0]["status"] == "missing"
    nov = _receipt(db, date(2026, 11, 8), category="Flyg", vendor="Finnair", amount=574.49,
                   subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 10NOV2026")
    db.commit()
    item = _items(_cl(db, t), "flight")[0]
    assert item["status"] == "found" and item["receipt"]["message_id"] == nov.message_id


def test_flight_receipt_vendor_only_is_not_found(db):
    t = _trip(db, date(2026, 11, 10), date(2026, 11, 11))
    _receipt(db, date(2026, 10, 20), category="Flyg", vendor="Finnair")
    assert _items(_cl(db, t), "flight")[0]["status"] == "missing"


# =====================================================================
# Del 4 — detektorfynd
# =====================================================================


def test_rebooked_flight_13_okt_replaces_calendar_flight():
    html = open(os.path.join(os.path.dirname(__file__), "fixtures_c44_change.html")).read()
    segs = rd.extract_details("Bekräftelse på din bokningsändring", "", html)["flight_segments"]
    events = [
        gflight("f1", "Goteborg", "AY 861", datetime(2026, 10, 13, 7, 15, tzinfo=HEL_TZ),
                datetime(2026, 10, 13, 7, 45, tzinfo=CET), "Helsinki HEL"),
        gflight("f2", "HELSINKI HELSINKI VANTAA", "AY 864",
                datetime(2026, 10, 14, 13, 0, tzinfo=CET),
                datetime(2026, 10, 14, 15, 20, tzinfo=HEL_TZ), "Goteborg GOT"),
    ]
    res = detect_trips(events, HOMES, rebookings=segs)
    trip = res["trips"][0]
    nos = [f["flight_no"] for f in trip["flights"]]
    assert nos == ["AY 861", "AY 868"]
    assert trip["flights"][1]["rebooked_from"]["flight_no"] == "AY 864"
    assert trip["end_at"].startswith("2026-10-13T23:15")
    assert res["stats"]["rebooked_flights"] == 1


def test_meetings_before_departure_and_after_return_not_listed():
    events = [
        gflight("f1", "Copenhagen", "AY 951", datetime(2026, 11, 10, 11, 35, tzinfo=HEL_TZ),
                datetime(2026, 11, 10, 12, 15, tzinfo=CET), "Helsinki HEL"),
        gflight("f2", "HELSINKI HELSINKI VANTAA", "AY 962",
                datetime(2026, 11, 11, 19, 25, tzinfo=CET),
                datetime(2026, 11, 11, 21, 55, tzinfo=HEL_TZ), "Copenhagen CPH"),
        ev("m0", "Early sync", datetime(2026, 11, 10, 8, 0, tzinfo=HEL_TZ),
           datetime(2026, 11, 10, 8, 30, tzinfo=HEL_TZ)),
        ev("m1", "Team sync Helsingborg", datetime(2026, 11, 10, 15, 0, tzinfo=HEL_TZ),
           datetime(2026, 11, 10, 16, 0, tzinfo=HEL_TZ)),
        ev("m2", "Late call", datetime(2026, 11, 11, 21, 0, tzinfo=HEL_TZ),
           datetime(2026, 11, 11, 21, 30, tzinfo=HEL_TZ)),
    ]
    trip = detect_trips(events, HOMES)["trips"][0]
    listed = [m["id"] for d in trip["days"] for m in d["meetings"]]
    assert listed == ["m1"]


def test_duplicate_finnair_receipts_25_aug(db):
    def _fin(amount, ticket, coupled=None):
        m = _receipt(db, date(2026, 8, 25), category="Flyg", vendor="Finnair", amount=amount,
                     subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 25AUG2026",
                     ticket_number=ticket, bezala_transaction_id=coupled)
        return m
    a1 = _fin(674.48, "1059999990002", coupled="5334818")
    a2 = _fin(674.48, "1059999990002")
    b1 = _fin(514.48, "1059999990003")
    b2 = _fin(514.48, "1059999990003")
    _fin(514.48, "1059999990004")  # annan biljett, samma belopp → inte dubblett
    assert rd.mark_duplicates(db) == {"groups": 2, "newly_marked": 2, "cleared": 0}
    db.refresh(a2), db.refresh(b2), db.refresh(a1)
    assert a2.duplicate_of == a1.message_id  # det kopplade behålls
    assert b2.duplicate_of == b1.message_id
    rep = rd.duplicate_report(db, 2026)
    assert rep["duplicate_receipts"] == 2
    assert rep["duplicates_with_bezala_draft"] == 0


def test_hotel_without_flight_is_domestic_unconfirmed(db):
    from app.services.calendar_trip_store import persist_detected
    from app.services.trip_completeness import list_missing

    events = [
        ev("h1", "Stay at NH Helsinki Grand Hansa", date(2026, 11, 19), date(2026, 11, 20),
           location="Yliopistonkatu 26, Helsinki", others=0),
        ev("h2", "Stay at Scandic Go Saaristonkatu", date(2026, 12, 21), date(2026, 12, 22),
           location="Saaristonkatu 4, Oulu", others=0),
    ]
    res = detect_trips(events, HOMES)
    assert [(t["start_date"], t["destination"]) for t in res["trips"]] == [
        ("2026-11-19", "Helsinki"), ("2026-12-21", "Oulu")]
    assert all("domestic_unconfirmed" in t["flags"] for t in res["trips"])
    persist_detected(db, res)
    with patch("app.services.trip_completeness._today", return_value=date(2027, 1, 10)):
        out = list_missing(db, older_than_days=5, sync=False)
    assert out["counts"]["unconfirmed_trips"] == 2
    items = {i["kind"]: i for i in out["trip_items_missing"]}
    assert "flight" not in items and "parking" not in items


def test_update_trip_destination_updates_non_manual_days(db):
    from app.services import calendar_trip_edit as edit

    t = _trip(db, date(2026, 1, 20), date(2026, 1, 22), destination="Ljungby")
    edit.update_trip_day(db, t.id, "2026-01-22", destination="Ljungby", purpose="Leadership meeting")
    # Mikko ändrar dag 3 manuellt till Växjö, sedan hela resan till Ängelholm.
    edit.update_trip_day(db, t.id, "2026-01-22", destination="Växjö")
    out = edit.update_trip(db, t.id, destination="Ängelholm", purpose="Customer meetings")
    by = {d["date"]: d for d in out["days"]}
    assert by["2026-01-20"]["destination"] == "Ängelholm"
    assert by["2026-01-21"]["destination"] == "Ängelholm"
    assert by["2026-01-22"]["destination"] == "Växjö"  # manuell — orörd
    assert by["2026-01-20"]["purpose"] == "Customer meetings"
    assert by["2026-01-22"]["purpose"] == "Leadership meeting"  # manuell


def test_remove_last_day_keeps_end_time_after_midnight(db):
    from app.services import calendar_trip_edit as edit

    t = _trip(db, date(2026, 10, 13), date(2026, 10, 14), status="edited",
              destination="Gothenburg")
    t.travel_start_at = "2026-10-13T05:09:00+03:00"
    t.travel_end_at = "2026-10-14T00:17:00+03:00"
    t.end_at = "2026-10-13T23:15:00+03:00"
    db.commit()
    out = edit.remove_trip_day(db, t.id, "2026-10-14")
    assert out["end_date"] == "2026-10-13"
    assert out["travel_end_helsinki"] == "2026-10-14 00:17"
    assert out["flight_arrival_helsinki"] == "2026-10-13 23:15"


def test_remove_last_day_daytime_return_still_clears(db):
    from app.services import calendar_trip_edit as edit

    t = _trip(db, date(2026, 3, 10), date(2026, 3, 11))
    t.travel_end_at = "2026-03-11T20:35:00+02:00"
    db.commit()
    out = edit.remove_trip_day(db, t.id, "2026-03-11")
    assert out["travel_end_helsinki"] is None


# =====================================================================
# Parsning + hotellavsändare
# =====================================================================


def test_parse_amadeus_receipt_segments_pnr_ticket():
    d = rd.extract_details("", AMADEUS_25AUG, "")
    assert [(s["flight_no"], s["date"], s["origin"], s["dest"]) for s in d["flight_segments"]] == [
        ("AY 953", "2026-08-25", "HEL", "CPH"), ("AY 962", "2026-08-26", "CPH", "HEL")]
    assert d["booking_ref"] == "XYZ789"
    assert d["ticket_number"] == "1059999990002"


def test_enrich_receipts_reads_gmail_once(db):
    m = _receipt(db, date(2026, 3, 6), category="Parkering", vendor="Moovy",
                 sender="noreply@moovy.fi", subject="Moovy: kvitto #34 793 894",
                 received_at=datetime(2026, 3, 6, 7, 50))
    gmail = MagicMock()
    gmail.fetch_body.return_value = (
        "Moovy: kvitto", "| Finavia P1 Premium 05.03.2026 05:51, 16h 49min 10s | MPV862 |", "")
    assert rd.enrich_receipts(db, gmail=gmail)["read"] == 1
    db.refresh(m)
    assert m.parking_entry_at.startswith("2026-03-05T05:51")
    assert m.details_status == "ok"
    assert rd.enrich_receipts(db, gmail=gmail)["read"] == 0  # bara en gång
    assert gmail.fetch_body.call_count == 1


def test_new_hotel_senders_in_gmail_filter():
    from app.services.settings_service import BUILTIN_INCLUDES

    assert "bokning@riversidehotel.se" in BUILTIN_INCLUDES
    assert "info@storyhotel-studiomalmo.com" in BUILTIN_INCLUDES


def test_same_amount_and_day_without_ticket_is_not_duplicate(db):
    # Två Skånetrafiken-biljetter à 150 SEK samma dag (5 nov) = två resor.
    for _ in range(2):
        _receipt(db, date(2026, 11, 5), category="Kollektivtrafik", vendor="Skånetrafiken",
                 amount=150.0, subject="Ditt kvitto")
    assert rd.mark_duplicates(db)["groups"] == 0


def test_parking_matches_when_first_flight_has_no_origin(db):
    from app.services.trip_parking import apply_parking_times

    t = _trip(db, date(2026, 5, 7), date(2026, 5, 8), status="edited", destination="Båstad")
    _with_flights(db, t, datetime(2026, 5, 7, 6, 20, tzinfo=HEL_TZ),
                  datetime(2026, 5, 8, 19, 35, tzinfo=HEL_TZ))
    t.flights = [dict(t.flights[0], origin=None), dict(t.flights[1], origin=None)]
    db.commit()
    _moovy(db, "Finavia P2 07.05.2026 05:19, 1 dygn 15h 0min 2s", date(2026, 5, 9))
    assert apply_parking_times(db)["suggested_on_locked"] == 1
    db.refresh(t)
    tt = t.suggestions["travel_times"]
    assert tt["start"].startswith("2026-05-07T04:39") and tt["end"].startswith("2026-05-08T20:59")
