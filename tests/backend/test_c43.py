"""C43 — fel som attesten hittat 2026 + kortradsstatus.

2a  Yalla Go 750 SEK ↔ kortrad 69,22 EUR 2026-08-31 (kortavgift > 2 %).
2b  Finnair-ombokning HEL–GOT 13 okt 2026, Grand Total 0,02 EUR.
2c  Privat utlägg (HEL-parkering via Moovy 8,49 EUR 2026-08-01).
"""

from __future__ import annotations

import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services import bill_line_status as bls  # noqa: E402
from app.services.receipt_kind import (  # noqa: E402
    KIND_CHANGE_FEE,
    classify_existing_row,
    detect_change_fee,
)
from app.services.receipt_matcher import (  # noqa: E402
    MAX_TOTAL_SCORE,
    card_country,
    find_matches,
    score_match,
)
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


# ECB (frankfurter) 2026-08-31: 1 SEK = 0.090009 EUR (verifierat).
def _rates(date_str, frm, to):
    table = {("SEK", "EUR"): 0.090009, ("EUR", "SEK"): 1 / 0.090009}
    return table.get((frm, to))


YALLA_ROW = {
    "id": "2364577",
    "description": "MIKKO KEINONEN: SUMUP  *YALLA GO AB, ANGERWD, SE 69.22 EUR",
    "amount": 69.22,
    "currency": "EUR",
    "date": "2026-08-31",
}

YALLA_RECEIPT = {
    "id": 1, "vendor": "Yalla Go", "amount": 750.0, "currency": "SEK",
    "receipt_date": "2026-08-31", "received_at": "2026-08-31T20:00:00",
    "sender": "manual-upload", "summary": "Taxiresa Göteborg",
}
TAXI_RECEIPT = {
    "id": 2, "vendor": "Taxi Göteborg", "amount": 562.0, "currency": "SEK",
    "receipt_date": "2026-09-01", "received_at": "2026-09-01T10:00:00",
    "sender": "manual-upload", "summary": "Taxi Göteborg",
}


# ---------- 2a: matchning med kortavgift ----------


def test_yalla_case_card_fee_within_window_wins():
    # Gammalt beteende: 750 SEK → 67.51 EUR, kortrad 69.22 (+2.5 %) → 0 p.
    res = score_match(YALLA_ROW, YALLA_RECEIPT, rate_provider=_rates)
    assert res["breakdown"]["amount"] == 40
    assert res["conversion"]["to_amount"] == pytest.approx(67.51, abs=0.01)
    assert res["breakdown"]["location"] == 10  # SEK-kvitto ↔ kortrad i SE
    matches = find_matches(YALLA_ROW, [TAXI_RECEIPT, YALLA_RECEIPT], rate_provider=_rates)
    assert matches[0]["message"]["id"] == 1
    assert matches[0]["score"] == MAX_TOTAL_SCORE
    # Taxi Göteborg 562 SEK = 50.6 EUR passar inte 69.22 → ingen beloppspoäng.
    taxi = score_match(YALLA_ROW, TAXI_RECEIPT, rate_provider=_rates)
    assert taxi["breakdown"]["amount"] == 0
    assert all(m["message"]["id"] != 2 for m in matches)


def test_converted_amount_outside_card_fee_window_gets_no_amount_points():
    # 700 SEK = 63.0 EUR (kortet 9.8 % över), 790 SEK = 71.1 EUR (kortet 2.6 % under)
    for amount in (700.0, 790.0):
        cand = dict(YALLA_RECEIPT, amount=amount)
        assert score_match(YALLA_ROW, cand, rate_provider=_rates)["breakdown"]["amount"] == 0


def test_card_fee_window_is_asymmetric():
    from app.services.receipt_matcher import _within_card_fee_window

    assert _within_card_fee_window(69.22, 67.51)      # +2.5 % avgift
    assert _within_card_fee_window(69.50, 67.51)      # +2.9 %
    assert not _within_card_fee_window(69.70, 67.51)  # +3.2 %
    assert _within_card_fee_window(66.90, 67.51)      # -0.9 % kursbrus
    assert not _within_card_fee_window(66.50, 67.51)  # -1.5 %


def test_original_currency_row_with_eur_amount():
    raw = {"bill_line_id": 2364577, "amount": "750.00", "currency": "SEK",
           "description": YALLA_ROW["description"], "date": "2026-08-31"}
    row = bls.normalize_missing_receipt(raw)
    assert row["amount"] == 750.0 and row["currency"] == "SEK"
    assert row["amount_eur"] == 69.22
    # SEK-kvitto: samma valuta → exakt.
    assert score_match(row, YALLA_RECEIPT)["breakdown"]["amount"] == 50
    # EUR-kvitto jämförs mot EUR-beloppet.
    eur = dict(YALLA_RECEIPT, amount=69.22, currency="EUR")
    assert score_match(row, eur, rate_provider=_rates)["breakdown"]["amount"] == 50


def test_card_country_parsing():
    assert card_country(YALLA_ROW["description"]) == "SE"
    assert card_country("MIKKO KEINONEN: FINNAIR, VANTAA, FI -134.48 EUR") == "FI"
    assert card_country("no amount here") is None


def test_trip_city_weighs_up_receipt_from_work_city():
    row = {"id": "x", "description": "MIKKO KEINONEN: TAXI, UNKNOWN 40.00 EUR",
           "amount": 40.0, "currency": "EUR", "date": "2026-10-13",
           "trip": {"trip_id": 7, "countries": ["SE"], "cities": ["Gothenburg"]}}
    base = {"vendor": "Taxi", "amount": 40.0, "currency": "EUR",
            "receipt_date": "2026-10-13"}
    gbg = dict(base, id=10, summary="Taxi i Göteborg")
    hel = dict(base, id=11, summary="Taxi i Helsingfors")
    matches = find_matches(row, [hel, gbg])
    assert matches[0]["message"]["id"] == 10
    assert matches[0]["score_breakdown"]["location_reason"] == "trip_city:gothenburg"


def test_location_bonus_never_lifts_unknown_vendor_over_threshold():
    row = dict(YALLA_ROW)
    stranger = {"id": 3, "vendor": "Clas Ohlson", "amount": 750.0, "currency": "SEK",
                "receipt_date": "2026-08-31"}
    res = score_match(row, stranger, rate_provider=_rates)
    assert res["total"] <= 49


def test_trip_context_from_calendar(db):
    from app.models import CalendarTrip, CalendarTripDay

    t = CalendarTrip(start_date=date(2026, 10, 13), end_date=date(2026, 10, 13),
                     destination="Gothenburg", country="SE", status="approved",
                     source_event_ids=[], flights=[], hotels=[])
    db.add(t)
    db.commit()
    db.add(CalendarTripDay(trip_id=t.id, day_date=date(2026, 10, 13),
                           destination="Gothenburg", country="SE", meetings=[],
                           source_event_ids=[]))
    db.commit()
    ctx = bls.trip_context(db, "2026-10-14")
    assert ctx == {"trip_id": t.id, "countries": ["SE"], "cities": ["Gothenburg"]}
    assert bls.trip_context(db, "2026-10-20") is None


# ---------- 2b: Finnair-ombokning ----------

# Utdrag ur det riktiga mailet (eticket@amadeus.com 2026-10-07).
AMADEUS_REISSUE = """
| YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 13OCT2026 |
| Booking Reference: 8X3V65 | | Office |
| HELSINKI HELSINKI VANTAA | GOTEBORG LANDVETTER | AY0861 | Y | 13Oct | 07:15 | 07:45 | Ok |
| | | | Fare Basis | YNN0S9CE/XZ |
| Receipt |
| Form of payment | : EXT : 0.02 : EUR |
| Form of payment | : EXT Paid |
| Fare | : EUR 595.00 |
| : EUR 0.02YA | EUR PD 6.27 DQ | EUR PD 21.10XT | EUR PD 12.31FI |
| Issuing Airline and date | : FINNAIR 07OCT26 IATA : 19491205 |
| Fare Calculation | : HEL AY GOT395.54AY HEL295.60NUC691.14END ROE0.860558 |
| Grand Total | : EUR 0.02 |
"""

FINNAIR_CHANGE_CONFIRMATION = """
| # Tack. Din bokningsändring har bekräftats. |
| ## Utresa 2026-10-13 Helsingfors Helsinki Vantaa - Goteborg Landvetter |
| 07:15 HEL | | 07:45 GOT |
| Prisskillnad | 0.02 EUR |
| Ändringsavgift | 0 EUR |
| Totalpris | 0.02 EUR |
"""

AMADEUS_NORMAL = """
| YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 13OCT2026 |
| Booking Reference: 8X3V65 |
| Form of payment | : CC VI XXXXXXXXXXXX4094 : 646.57 : EUR |
| Fare | : EUR 595.00 |
| : EUR 7.89YA | EUR 6.27DQ | EUR 21.10XT | EUR 12.31FI |
| Fare Calculation | : HEL AY GOT395.54AY HEL295.60NUC691.14END ROE0.860558 |
| Grand Total | : EUR 646.57 |
"""


def test_amadeus_reissue_detected_as_change_fee():
    info = detect_change_fee(
        sender='"Finnair " <eticket@amadeus.com>',
        subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 13OCT2026",
        text=AMADEUS_REISSUE, amount=595.0, currency="EUR",
    )
    assert info is not None
    assert info.reason == "grand_total_below_fare"
    assert info.amount == 0.02 and info.currency == "EUR"
    assert (info.origin, info.destination, info.flight_date) == ("HEL", "GOT", "2026-10-13")
    assert info.description_en() == "Finnair change fee – flight HEL–GOT 13 Oct 2026"


def test_finnair_change_confirmation_detected():
    info = detect_change_fee(
        sender="Finnair <noreply@finnair.com>",
        subject="Bekräftelse på din bokningsändring",
        text=FINNAIR_CHANGE_CONFIRMATION, amount=0.02, currency="EUR",
    )
    assert info is not None
    assert info.description_en() == "Finnair change fee – flight HEL–GOT 13 Oct 2026"


def test_normal_eticket_is_not_change_fee():
    assert detect_change_fee(
        sender="eticket@amadeus.com",
        subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 13OCT2026",
        text=AMADEUS_NORMAL, amount=646.57, currency="EUR",
    ) is None
    # Annan avsändare påverkas aldrig.
    assert detect_change_fee(
        sender="noreply@moovy.fi", subject="Muutosmaksu", text="", amount=0.02,
    ) is None


def test_classify_existing_rows_for_backfill():
    assert classify_existing_row(
        sender="eticket@amadeus.com", subject="YOUR ETICKET RECEIPT: X DEP: 13OCT2026",
        summary="Finnair-biljett HEL-GOT", amount=0.02,
    ) == KIND_CHANGE_FEE
    assert classify_existing_row(
        sender="noreply@finnair.com", subject="Bekräftelse på din bokningsändring",
        summary=None, amount=None,
    ) == KIND_CHANGE_FEE
    assert classify_existing_row(
        sender="eticket@amadeus.com", subject="YOUR ETICKET RECEIPT: X DEP: 13OCT2026",
        summary="Finnair-biljett HEL-GOT", amount=646.57,
    ) is None


def test_backfill_marks_rows_and_rewrites_uncoupled_description(db):
    from app.models import ProcessedMessage
    from app.services.c43_migrations import backfill_change_fee_kind

    fee = ProcessedMessage(message_id="m1", sender="eticket@amadeus.com",
                           subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 13OCT2026",
                           amount=0.02, currency="EUR", status="saved",
                           ai_description_en="Finnair flight HEL-GOT")
    ticket = ProcessedMessage(message_id="m2", sender="eticket@amadeus.com",
                              subject="YOUR ETICKET RECEIPT: KEINONEN/MIKKO MR DEP: 13OCT2026",
                              amount=646.57, currency="EUR", status="saved")
    db.add_all([fee, ticket])
    db.commit()
    hits = backfill_change_fee_kind(db)
    assert hits == [fee.id]
    db.refresh(fee)
    db.refresh(ticket)
    assert fee.kind == KIND_CHANGE_FEE and ticket.kind is None
    assert fee.ai_description_en == "Finnair change fee – flight 13 Oct 2026"
    assert backfill_change_fee_kind(db) == []  # idempotent


def test_change_fee_is_not_a_flight_anchor():
    from app.services.trip_grouper import _is_flight

    base = dict(category="Flyg", vendor="Finnair", sender="eticket@amadeus.com",
                subject="YOUR ETICKET RECEIPT")
    assert _is_flight(SimpleNamespace(kind=None, **base))
    assert not _is_flight(SimpleNamespace(kind="change_fee", **base))


# ---------- 2c: privat utlägg (C42 card_lines) ----------

MOOVY_PRIVATE = {"bill_line_id": 2316962,
                 "description": "MIKKO KEINONEN: MOOVY, TAMPERE, FI 8.49 EUR",
                 "date": "2026-08-01"}
MOOVY_TRIP = {"bill_line_id": 2355919,
              "description": "MIKKO KEINONEN: MOOVY, TAMPERE, FI 73.49 EUR",
              "date": "2026-08-27"}
CURSOR = {"bill_line_id": 2352152,
          "description": "MIKKO KEINONEN: CURSOR, AI POWERED IDE, SAN FRANCISCO, US 22.03 EUR",
          "date": "2026-08-24"}


def _trip_aug(db):
    from app.models import CalendarTrip

    db.add(CalendarTrip(start_date=date(2026, 8, 25), end_date=date(2026, 8, 26),
                        destination="Helsingborg", country="SE", status="approved",
                        source_event_ids=[], flights=[], hotels=[]))
    db.commit()


def _sync(db, rows=(MOOVY_PRIVATE, MOOVY_TRIP, CURSOR)):
    from app.services.trip_completeness import sync_card_lines

    sync_card_lines(db, list(rows))


def test_list_missing_flags_parking_outside_trip(db):
    from app.services.trip_completeness import list_missing

    _trip_aug(db)
    _sync(db)
    out = list_missing(db, older_than_days=0, sync=False)
    by_id = {c["bill_line_id"]: c for c in out["card_lines_without_receipt"]}
    assert by_id["2316962"]["flags"] == ["possible_private"]
    assert "private" in by_id["2316962"]["question"]
    assert by_id["2355919"]["flags"] == []  # inom resan (+3 d marginal)
    assert by_id["2352152"]["flags"] == []  # ingen parkering
    assert out["possible_private_count"] == 1


def test_private_expense_is_handled_in_list_missing(db):
    from app.services.trip_completeness import list_missing, sync_card_lines

    _sync(db)
    row = bls.mark_private_expense(db, "2316962", "Private errand")
    assert row.status == "private_expense" and row.amount == 8.49
    out = list_missing(db, older_than_days=0, sync=False)
    ids = [c["bill_line_id"] for c in out["card_lines_without_receipt"]]
    assert "2316962" not in ids and "2352152" in ids
    # Bezala-synken behåller Mikkos markering.
    sync_card_lines(db, [MOOVY_PRIVATE, CURSOR])
    assert bls.get_card_line(db, "2316962").status == "private_expense"


def test_mark_private_expense_syncs_unknown_row_first(db):
    called = []

    def fake_sync(s):
        called.append(True)
        _sync(s, [MOOVY_PRIVATE])

    row = bls.mark_private_expense(db, "2316962", sync=fake_sync)
    assert called and row.status == "private_expense"
    with pytest.raises(bls.BillLineStatusError):
        bls.mark_private_expense(db, "999", sync=lambda s: None)


def test_private_expense_description():
    assert bls.private_expense_description() == (
        "Private expense – to be deducted from salary."
    )
    assert bls.private_expense_description(
        "Weekend parking", base="Parking at Helsinki-Vantaa Airport",
    ) == ("Private expense – to be deducted from salary. "
          "Parking at Helsinki-Vantaa Airport Weekend parking")


def test_private_expense_draft_only_creates_unapproved_draft(db):
    _sync(db)
    bezala = MagicMock()
    bezala.attach_file.return_value = SimpleNamespace(attachment_id="5001")
    with pytest.raises(bls.BillLineStatusError):
        bls.create_private_expense_draft(db, bezala, "2316962")  # inte markerad
    bls.mark_private_expense(db, "2316962")
    with patch("app.services.bill_line_status._note_pdf", return_value=b"%PDF-1.4 x"):
        tx = bls.create_private_expense_draft(db, bezala, "2316962")
    assert tx == "5001"
    args, kwargs = bezala.attach_file.call_args
    assert args[0] == "2316962"
    assert kwargs["description"] == "Private expense – to be deducted from salary."
    # Bara attach_file (POST /attachments med state=unapproved) — inget
    # submit/attest-anrop.
    assert {c[0] for c in bezala.method_calls} == {"attach_file"}
    assert bls.create_private_expense_draft(db, bezala, "2316962") == "5001"
    assert bezala.attach_file.call_count == 1


def test_mcp_mark_private_expense_and_list_missing(env):
    c, scope, _ = env
    at = _full_flow(c)["access_token"]
    fake = MagicMock()
    fake.list_missing_receipts.return_value = [MOOVY_PRIVATE, CURSOR]
    with patch("app.services.bezala_client.BezalaClient", return_value=fake):
        res = _call(c, at, "list_missing", older_than_days=0)
        assert res["data"]["counts"]["card_lines"] == 2
        assert res["data"]["possible_private_count"] == 1
        res = _call(c, at, "mark_private_expense", bill_line_id="2316962",
                    note="Private errand")
        assert res["data"]["status"] == "private_expense"
        assert res["data"]["amount"] == 8.49
        assert res["data"]["bezala_comment"] == (
            "Private expense – to be deducted from salary. Private errand"
        )
        res = _call(c, at, "list_missing", older_than_days=0)
        assert [x["bill_line_id"] for x in res["data"]["card_lines_without_receipt"]] == ["2352152"]
        res = _call(c, at, "set_card_line_status", bill_line_id="2316962", status="missing")
        assert res["data"]["status"] == "missing"
    fake.attach_file.assert_not_called()


def test_change_fee_not_counted_as_trip_flight(db):
    from app.models import CalendarTrip, ProcessedMessage
    from app.services.trip_completeness import _match_flight, load_receipts

    trip = CalendarTrip(start_date=date(2026, 10, 13), end_date=date(2026, 10, 13),
                        destination="Gothenburg", country="SE", status="approved",
                        source_event_ids=[], hotels=[],
                        flights=[{"flight_no": "AY 861", "origin": "HEL"}])
    db.add(trip)
    db.add(ProcessedMessage(message_id="fee", sender="eticket@amadeus.com",
                            subject="YOUR ETICKET RECEIPT DEP: 13OCT2026",
                            summary="AY0861 HEL-GOT", category="Flyg", amount=0.02,
                            currency="EUR", receipt_date="2026-10-07",
                            status="saved", kind="change_fee"))
    db.commit()
    assert _match_flight(trip, load_receipts(db)) is None
    db.add(ProcessedMessage(message_id="ticket", sender="eticket@amadeus.com",
                            subject="YOUR ETICKET RECEIPT DEP: 13OCT2026",
                            summary="AY0861 HEL-GOT", category="Flyg", amount=646.57,
                            currency="EUR", receipt_date="2026-09-20", status="saved"))
    db.commit()
    assert _match_flight(trip, load_receipts(db)).message_id == "ticket"
