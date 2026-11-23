"""C53 — km och energi per resa, Del 0 (state skickas aldrig) och Del 0b
(rätt kvitto föreslås: e-ticket före bokningsbekräftelse)."""

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
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _line, _receipt  # noqa: E402
from tests.backend.test_c50 import bound_db  # noqa: E402,F401

PDF = b"%PDF-1.4\nfake"


def _trip(s, start, end, flights, *, time_source="parking", status="suggested", dest="Oslo"):
    from app.models import CalendarTrip

    t = CalendarTrip(start_date=start, end_date=end, destination=dest, status=status,
                     flights=flights, hotels=[], source_event_ids=[], flags=[],
                     time_source=time_source)
    s.add(t)
    s.commit()
    return t


def _flight(no, origin, dest, start, end):
    return {"id": no, "flight_no": no, "origin": origin, "destination_iata": dest,
            "start": start, "end": end}


OSLO_FLIGHTS = [
    _flight("AY 911", "HEL", "OSL", "2026-10-21T04:25:00+00:00", "2026-10-21T05:50:00+00:00"),
    _flight("AY 918", "OSL", "HEL", "2026-10-21T18:30:00+00:00", "2026-10-21T19:55:00+00:00"),
]


@pytest.fixture(autouse=True)
def _car_env(monkeypatch):
    for k in ("TRIP_CAR_HOME_KM_ONE_WAY", "TRIP_CAR_OUT_ELECTRIC_SHARE",
              "TRIP_CAR_BACK_ELECTRIC_SHARE", "TRIP_CAR_ELECTRIC_KWH_PER_100KM",
              "TRIP_CAR_PETROL_L_PER_100KM_DISCHARGED", "C51_GUARD_LIVE_AT_UTC"):
        monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------- Del 1 flyg


def test_reference_distances():
    assert tl.flight_km("HEL", "ARN")[0] == 399
    assert tl.flight_km("OSL", "HEL")[0] == 763
    assert tl.flight_km("HEL", "GOT")[0] == 783
    assert tl.flight_km("CPH", "HEL")[0] == 892


def test_great_circle_matches_reference_roughly_and_unknown_is_missing():
    hel_arn = tl.great_circle_km(tl.AIRPORT_COORDS["HEL"], tl.AIRPORT_COORDS["ARN"])
    assert abs(hel_arn - 399) < 5
    km, note = tl.flight_km("HEL", "MMX")
    assert km and 800 < km < 900 and "Great circle" in note
    assert tl.flight_km("HEL", "XYZ")[0] is None


def test_hel_osl_round_trip_is_two_legs_of_763(db):  # noqa: F811
    t = _trip(db, date(2026, 4, 13), date(2026, 4, 14), [
        _flight("AY 911", "HEL", "OSL", "2026-04-13T04:25:00+00:00", "2026-04-13T05:50:00+00:00"),
        _flight("AY 918", "OSL", "HEL", "2026-04-14T16:30:00+00:00", "2026-04-14T17:55:00+00:00"),
    ])
    legs, _ = tl.build_auto_legs(db, t)
    flights = [leg for leg in legs if leg["mode"] == "flight"]
    assert [f["km"] for f in flights] == [763, 763]
    assert [f["flight_no"] for f in flights] == ["AY 911", "AY 918"]
    assert all(f["source"] == "airport_table" and f["flight_category"] == "medium"
               for f in flights)
    assert flights[1]["leg_date"] == date(2026, 4, 14)


# ---------------------------------------------------------------- resa 16 (Oslo 21 okt)


def test_trip_16_oslo_gives_flights_flytoget_and_car_out_and_home(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS)
    _receipt(db, date(2026, 10, 21), "Flytoget", 276.0, "NOK", waiting=False,
             category="Tåg", subject="Flytoget kvittering")
    _receipt(db, date(2026, 10, 21), "Moovy", 37.49, waiting=False, category="Parkering")
    view = tl.get_trip_legs(db, t.id)
    modes = [leg["mode"] for leg in view["legs"]]
    assert modes == ["car_phev", "flight", "flight", "train", "car_phev"]
    out, back = view["legs"][0], view["legs"][-1]
    assert out["km"] == 48 and out["energy_type"] == "electricity"
    assert out["energy_amount"] == 7.92 and out["energy_unit"] == "kWh"
    assert back["km"] == 48 and back["energy_type"] == "petrol"
    assert back["energy_amount"] == 3.36 and back["energy_unit"] == "l"
    assert out["is_estimate"] and back["is_estimate"]
    assert out["source"] == back["source"] == "assumption"
    train = view["legs"][3]
    assert train["km"] is None and train["km_missing"] and train["source"] == "receipt"
    assert "km_missing" in view["legs_flags"]
    assert view["legs_totals"]["by_mode"]["flight"]["km"] == 1526


def test_no_parking_means_home_transport_unknown(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS,
              time_source="flight_default")
    legs, flags = tl.build_auto_legs(db, t)
    assert "home_transport_unknown" in flags
    assert not any(leg["mode"] == "car_phev" for leg in legs)


def test_missing_return_flight_is_km_missing_not_guessed(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 13), date(2026, 10, 14), [OSLO_FLIGHTS[0]])
    rows, flags = tl.sync_trip_legs(db, t)
    assert "return_flight_missing" in flags and "km_missing" in flags
    assert "home_transport_unknown" in flags
    assert any(r.km is None and r.mode == "flight" for r in rows)


def test_car_assumptions_from_env(db, monkeypatch):  # noqa: F811
    monkeypatch.setenv("TRIP_CAR_HOME_KM_ONE_WAY", "50")
    monkeypatch.setenv("TRIP_CAR_BACK_ELECTRIC_SHARE", "0.5")
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS)
    legs, _ = tl.build_auto_legs(db, t)
    car = [leg for leg in legs if leg["mode"] == "car_phev"]
    assert [(c["leg_key"], c["km"]) for c in car] == [
        ("car:out", 50), ("car:back:el", 25), ("car:back:petrol", 25)]


# ---------------------------------------------------------------- Del 2 tåg/buss/taxi


@pytest.mark.parametrize("text,km", [
    ("Taxi Helsinki 24,3 km fixed price", 24.3),
    ("Skånetrafiken Malmö C - Ljungby 98,5 km", 98.5),
    ("Taxi Göteborg, trip in Gothenburg (23.76 km)", 23.76),
    ("Taxi ride, no distance", None),
])
def test_parse_km(text, km):
    assert tl.parse_km(text) == km


def test_receipt_mode():
    assert tl.receipt_mode("Taxi", "Taxi Göteborg") == "taxi"
    assert tl.receipt_mode(None, "flytoget kvittering") == "train"
    assert tl.receipt_mode(None, "Skånetrafiken biljett") == "train"
    assert tl.receipt_mode(None, "Flygbussarna Landvetter") == "bus"
    assert tl.receipt_mode(None, "HSL mobiililippu") is None


def test_taxi_km_read_from_description(db):  # noqa: F811
    t = _trip(db, date(2026, 11, 18), date(2026, 11, 18), [
        _flight("AY 965", "HEL", "GOT", "2026-11-18T05:15:00+00:00", "2026-11-18T06:00:00+00:00"),
        _flight("AY 966", "GOT", "HEL", "2026-11-18T15:00:00+00:00", "2026-11-18T17:55:00+00:00"),
    ], dest="Gothenburg")
    _receipt(db, date(2026, 11, 18), "Taxi Göteborg", 49.97, waiting=False, category="Taxi",
             ai_description_en="Taxi ride in Gothenburg, 24,3 km")
    legs, _ = tl.build_auto_legs(db, t)
    taxi = [leg for leg in legs if leg["mode"] == "taxi"]
    assert len(taxi) == 1 and taxi[0]["km"] == 24.3 and not taxi[0]["is_estimate"]


def test_append_km_last_in_description():
    assert tl.append_km("Taxi Göteborg, 18 November 2026", 24.3) == \
        "Taxi Göteborg, 18 November 2026, 24.3 km"
    assert tl.append_km("Taxi, 24.3 km", 24.3) == "Taxi, 24.3 km"
    assert tl.append_km("Taxi", None) == "Taxi"


def test_swipe_card_ground_km():
    from app.models import ProcessedMessage
    from app.services.decision_queue import _ground_km

    m = ProcessedMessage(category="Taxi", vendor="Taxi Göteborg",
                         ai_description_en="Taxi in Gothenburg 24,3 km")
    assert _ground_km(m) == {"mode": "taxi", "km": 24.3}
    m2 = ProcessedMessage(category="Tåg", vendor="Flytoget")
    assert _ground_km(m2) == {"mode": "train", "km": None}
    assert _ground_km(ProcessedMessage(category="Hotell", vendor="Scandic")) is None


# ---------------------------------------------------------------- redigering


def test_update_trip_leg_is_kept_on_resync_and_recalculates_car_energy(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS)
    rows, _ = tl.sync_trip_legs(db, t)
    back = [r for r in rows if r.leg_key == "car:back"][0]
    out = tl.update_trip_leg(db, back.id, energy_type="electricity", note="Charged in Oslo")
    assert out["energy_unit"] == "kWh" and out["energy_amount"] == 7.92
    assert out["set_by"] == "user"
    rows, _ = tl.sync_trip_legs(db, t)
    back = [r for r in rows if r.leg_key == "car:back"][0]
    assert back.energy_type == "electricity" and back.note == "Charged in Oslo"
    tl.update_trip_leg(db, back.id, excluded=True)
    summary = tl.get_emissions_summary(db, 2026)
    assert summary["by_mode"]["car_phev"]["legs"] == 1


def test_update_trip_leg_validates():
    from app.services.calendar_trip_edit import TripEditError

    with pytest.raises(TripEditError):
        tl.update_trip_leg(_FakeDb(), 1, mode="rocket")


class _FakeDb:
    class _Q:
        def filter(self, *a):
            return self

        def first(self):
            from app.models import TripLeg

            return TripLeg(id=1, mode="taxi", energy_type="none")

    def query(self, *_):
        return self._Q()


def test_add_trip_leg_manual(db):  # noqa: F811
    t = _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS)
    leg = tl.add_trip_leg(db, t.id, mode="train", date_="2026-10-21", km=48,
                          from_place="OSL", to_place="Oslo S", note="Flytoget")
    assert leg["source"] == "manual" and leg["km"] == 48
    rows, _ = tl.sync_trip_legs(db, t)
    assert any(r.id == leg["leg_id"] for r in rows)


# ---------------------------------------------------------------- Del 4 sammanställning


def test_emissions_summary_and_export(db):  # noqa: F811
    _trip(db, date(2026, 10, 21), date(2026, 10, 22), OSLO_FLIGHTS)
    _trip(db, date(2026, 3, 5), date(2026, 3, 5), [], status="rejected")
    _trip(db, date(2025, 3, 5), date(2025, 3, 6), [OSLO_FLIGHTS[0]])
    s = tl.get_emissions_summary(db, 2026)
    assert s["trips"] == 1
    assert s["by_mode"]["flight"]["km"] == 1526
    assert s["by_mode"]["car_phev"]["kwh"] == 7.92 and s["by_mode"]["car_phev"]["litres"] == 3.36
    # C54: tjänstebilen ingår inte i Admins summa (bara info).
    assert s["total_km"] == 1526
    assert s["company_car_info_only"]["km"] == 96
    assert s["per_trip"][0]["by_mode"]["car_phev"] == 96
    assert "CO2e" in s["note"]
    rows = tl.export_rows(db, 2026)
    assert len(rows) == 4
    assert set(rows[0]) == set(tl.EXPORT_COLUMNS)
    assert rows[0]["is_estimate"] == "yes" and rows[1]["is_estimate"] == "no"
    csv_text = tl.export_csv(db, 2026)
    assert csv_text.splitlines()[0].startswith("trip_id,date,mode,from,to,km")
    assert "–" not in csv_text and "—" not in csv_text and "→" not in csv_text
    csv_text.encode("ascii")  # bara engelska/ASCII i exporten


# ---------------------------------------------------------------- Del 0


def test_written_after_guard():
    from app.services.bezala_writes_report import guard_live_at, written_after_guard

    assert guard_live_at() == datetime(2026, 11, 22, 10, 4, 19)
    for ts in ("2026-11-22T08:36:45.239316", "2026-11-22T02:33:33.661562",
               "2026-11-20T11:53:50.881108"):
        assert written_after_guard(ts) is False
    assert written_after_guard("2026-11-22T10:30:00") is True
    assert written_after_guard(None) is None


class _Resp:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status
        self.text = str(body)
        self.content = b""

    def json(self):
        return self._body


def _recording_client(monkeypatch):
    from app.services.bezala_client import BezalaClient

    c = BezalaClient.__new__(BezalaClient)
    calls = []

    def _request(method, path, *, json=None, files=None, data=None):
        calls.append((method, path, json, data))
        if method == "POST":
            return _Resp({"id": 11, "transaction_id": 22})
        return _Resp({"ok": True})

    c._request = _request
    c.get_transaction = lambda tx: {"id": tx, "state": "draft"}
    return c, calls


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _keys(v)


def test_no_write_path_sends_state(bound_db, monkeypatch):  # noqa: F811
    """Del 0 punkt 2 — alla skrivvägar: attachments (draft), coupling
    (attach_file + metadata-PUT), update_transaction, upload_receipt,
    raw_put_transaction. Ingen skickar `state`."""
    from app.services.bezala_client import BezalaError

    monkeypatch.setenv("BEZALA_METADATA_PUT_ENABLED", "true")  # bara i testet: täck PUT-vägen
    c, calls = _recording_client(monkeypatch)
    c.upload_file_as_draft(PDF, "a.pdf")
    c.attach_file("2513379", "b.pdf", PDF, description="x", date="2026-11-16",
                  credit_account_id=82320,
                  extra_fields={"state": "unapproved", "bill_line_id": 1, "date": "x"})
    c.update_transaction("22", description="y", date="2026-11-16",
                         extra_fields={"state": "unapproved", "status": "submitted"})
    c.upload_receipt(filename="c.pdf", pdf_bytes=PDF, description="z", date="2026-11-16",
                     extra_fields={"state": "unapproved"})
    with pytest.raises(BezalaError):
        c.raw_put_transaction("22", {"description": "q", "state": "unapproved"})
    writes = [x for x in calls if x[0] in ("POST", "PUT", "PATCH", "DELETE")]
    assert any(x[0] == "PUT" for x in writes) and any(x[0] == "POST" for x in writes)
    for method, path, body, form in writes:
        keys = set(_keys(body)) | set(_keys(form))
        assert "state" not in keys and "status" not in keys, (method, path, body, form)


def test_write_guard_unchanged():
    from app.services import bezala_write_guard as guard

    assert guard.DRAFT_STATES == frozenset({"draft"})
    assert "state" in guard.FORBIDDEN_WRITE_FIELDS
    assert guard.ALLOWED_ATTACHMENT_FORM_FIELDS == frozenset({"draft", "bill_line_id",
                                                              "transaction_id"})
    assert not guard.env_auto_couple_enabled() or os.environ.get("AUTO_COUPLE_ENABLED")


# ---------------------------------------------------------------- Del 0b


def _finnair(db, *, coupled_refund=True):
    eticket = _receipt(
        db, date(2026, 11, 26), "Finnair", 134.48, waiting=False, message_id="1a1e47fcf0f086b3",
        category="Flyg", subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 26NOV2026",
        sender='"Finnair" <eticket@amadeus.com>', booking_ref="8NLAI4",
        flight_segments=[{"flight_no": "AY 957", "origin": "HEL", "dest": "CPH",
                          "date": "2026-11-26"}],
        upload_source="gmail",
    )
    if coupled_refund:
        eticket.bezala_transaction_id = "5575527"
        eticket.bezala_upload_status = "success"
        eticket.matched_at = datetime(2026, 11, 20, 11, 52, 14)
        eticket.bezala_payment_merchant = "MIKKO KEINONEN: FINNAIR, VANTAA, FI -134.48 EUR"
        eticket.bezala_payment_amount = 134.48
        eticket.bezala_payment_date = "2026-11-18"
        db.commit()
    conf = _receipt(
        db, date(2026, 11, 26), "Finnair", 134.48, waiting=False, message_id="1a1e47fc363f2a8b",
        category="Flyg", subject="Bokningsbekräftelse och information för 8NLAI4",
        sender="Finnair <noreply@finnair.com>", booking_ref="8NLAI4",
        duplicate_of="1a1e47fcf0f086b3", upload_source="gmail",
    )
    line = _line(db, 2513379, date(2026, 11, 16), 134.48,
                 desc="MIKKO KEINONEN: FINNAIR, VANTAA, FI 134.48 EUR")
    return eticket, conf, line


def test_duplicate_is_never_suggested(db):  # noqa: F811
    from app.services import c53_receipts as r

    cands = [{"message_id": "a", "duplicate_of": "b"}, {"message_id": "b"}]
    assert [c["message_id"] for c in r.filter_candidates(db, cands)] == ["b"]


def test_eticket_beats_booking_confirmation_same_booking_ref(db):  # noqa: F811
    from app.services import c53_receipts as r

    cands = [
        {"message_id": "conf", "subject": "Booking confirmation ABC123", "booking_ref": "ABC123",
         "sender": "noreply@sas.se"},
        {"message_id": "et", "subject": "Your e-ticket receipt", "booking_ref": "abc123",
         "sender": "sas@amadeus.com"},
    ]
    assert [c["message_id"] for c in r.filter_candidates(db, cands)] == ["et"]
    # Även när e-ticket-kvittot redan är kopplat (finns inte bland kandidaterna).
    _receipt(db, date(2026, 5, 1), "SAS", 99.0, waiting=False, message_id="et2",
             subject="YOUR ETICKET RECEIPT", booking_ref="XYZ999", bezala_transaction_id="1")
    left = r.filter_candidates(db, [{"message_id": "c2", "subject": "Bokningsbekräftelse",
                                     "booking_ref": "XYZ999"}])
    assert left == []


def test_already_coupled_original_gives_text(db):  # noqa: F811
    from app.services import c53_receipts as r

    _, conf, _ = _finnair(db)
    note = r.already_coupled_note(db, conf)
    assert note["text"] == "redan kopplat till 5575527"
    assert note["message_id"] == "1a1e47fcf0f086b3"


def test_purchase_line_gets_original_marked_as_shared_with_refund(db):  # noqa: F811
    from app.services.card_line_coupling import suggest_receipts

    _, _, line = _finnair(db)
    out = suggest_receipts(db, [line], exclude_pairs=set())
    s = out["2513379"]
    assert s["message_id"] == "1a1e47fcf0f086b3"
    assert s["match"] == "refund_shared_receipt"
    assert s["note_sv"] == "samma kvitto används även för återbetalningen 2026-11-18"
    assert s["manual_upload"] and s["drive_link"]


def test_couple_refuses_receipt_already_coupled(db, monkeypatch):  # noqa: F811
    from app.services.card_line_coupling import CouplingError, couple_receipt

    _finnair(db)
    with pytest.raises(CouplingError, match="already coupled to Bezala 5575527"):
        couple_receipt(db, "2513379", "1a1e47fcf0f086b3")
    with pytest.raises(CouplingError, match="duplicate"):
        couple_receipt(db, "2513379", "1a1e47fc363f2a8b")


def test_confirmation_dry_run(db):  # noqa: F811
    from app.services.c53_receipts import confirmation_dry_run

    _finnair(db)
    out = confirmation_dry_run(db)
    assert out["dry_run"] and out["count"] == 1
    item = out["by_vendor"]["Finnair"]["receipts"][0]
    assert item["message_id"] == "1a1e47fc363f2a8b"
    assert item["original_coupled_to"] == "5575527"


def test_shared_refund_beats_weak_suggestion_with_other_amount(db):  # noqa: F811
    from app.services.card_line_coupling import suggest_receipts

    _, _, line = _finnair(db)
    _receipt(db, date(2026, 11, 18), "Finnair", 732.57, waiting=False, message_id="744",
             category="Flyg", subject="Finnair receipt", upload_source="gmail")
    out = suggest_receipts(db, [line], exclude_pairs=set())
    assert out["2513379"]["message_id"] == "1a1e47fcf0f086b3"
