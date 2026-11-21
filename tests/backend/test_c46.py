"""C46 Del 2, 3, 5, 6 — gratis måltider, avvisade resor, nya avsändare, textfixar."""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services.calendar_trip_edit import TripEditError  # noqa: E402
from tests.backend.test_c42 import _trip, db  # noqa: E402,F401


# ---------------------------------------------------------------- Del 2


def _day(detail, d):
    return next(x for x in detail["days"] if x["date"] == d)


def test_kickoff_day_two_meals_is_meals_included(db):  # noqa: F811
    from app.services import calendar_trip_edit as edit

    t = _trip(db, date(2026, 5, 7), date(2026, 5, 8), status="edited", destination="Båstad")
    out = edit.update_trip_day(db, t.id, "2026-05-07", free_meals=2,
                               free_meal_note="Kick-off lunch and dinner")
    d7 = _day(out, "2026-05-07")
    assert d7["free_meals"] == 2
    assert d7["meals_source"] == "manual"
    assert d7["free_meal_note"] == "Kick-off lunch and dinner"
    assert d7["netvisor"] == {"meals_included": True, "label": "Inklusive kost"}

    out = edit.update_trip_day(db, t.id, "2026-05-08", free_meals=1)
    d8 = _day(out, "2026-05-08")
    assert d8["free_meals"] == 1
    assert d8["netvisor"] == {"meals_included": False, "label": None}


def test_free_meals_max_two_and_breakfast_never_counts(db):  # noqa: F811
    from app.services import calendar_trip_edit as edit
    from app.services.trip_meals import count_meals

    t = _trip(db, date(2026, 5, 7), date(2026, 5, 7), status="edited")
    with pytest.raises(TripEditError):
        edit.update_trip_day(db, t.id, "2026-05-07", free_meals=3)
    assert count_meals(["hotel_breakfast", "lunch_calendar", "dinner_kickoff"], "SE") == 2
    assert count_meals(["hotel_breakfast"], "DK") == 0


def test_note_only_update(db):  # noqa: F811
    from app.services import calendar_trip_edit as edit

    t = _trip(db, date(2026, 5, 7), date(2026, 5, 7), status="edited")
    out = edit.update_trip_day(db, t.id, "2026-05-07", free_meal_note="Dinner by customer")
    assert _day(out, "2026-05-07")["free_meal_note"] == "Dinner by customer"
    assert _day(out, "2026-05-07")["meals_source"] == "manual"


# ---------------------------------------------------------------- Del 3


def _detected(start, end, flights, events=()):
    return {"trips": [{
        "start_date": start, "end_date": end, "destination": "Malmö",
        "flights": flights, "source_event_ids": list(events), "days": [],
    }]}


def test_reject_hides_and_refresh_never_recreates(db):  # noqa: F811
    from app.models import CalendarTrip
    from app.services import calendar_trip_edit as edit
    from app.services.calendar_trip_store import persist_detected

    t = _trip(db, date(2026, 3, 10), date(2026, 3, 11))  # suggested, flyg f0/f1
    out = edit.reject_trip(db, t.id, "Private trip")
    assert out["status"] == "rejected" and out["rejected_reason"] == "Private trip"

    assert [x["id"] for x in edit.list_trips(db)] == []
    assert [x["id"] for x in edit.list_trips(db, include_rejected=True)] == [t.id]
    assert [x["id"] for x in edit.list_trips(db, status="rejected")] == [t.id]

    same = _detected("2026-03-10", "2026-03-11", [{"id": "f0"}, {"id": "f1"}])
    res = persist_detected(db, same)
    assert res["created"] == 0 and res["skipped_rejected"] == 1
    assert db.query(CalendarTrip).count() == 1
    assert db.get(CalendarTrip, t.id).status == "rejected"

    # Andra flyg samma datum = en annan resa → skapas.
    other = _detected("2026-03-10", "2026-03-11", [{"id": "x9"}])
    assert persist_detected(db, other)["created"] == 1


def test_unreject(db):  # noqa: F811
    from app.services import calendar_trip_edit as edit

    t = _trip(db, date(2026, 3, 10), date(2026, 3, 11))
    edit.reject_trip(db, t.id, None)
    assert edit.unreject_trip(db, t.id)["status"] == "suggested"
    with pytest.raises(TripEditError):
        edit.unreject_trip(db, t.id)


def test_list_missing_ignores_rejected(db, monkeypatch):  # noqa: F811
    from app.services import calendar_trip_edit as edit
    from app.services import trip_completeness as tc

    monkeypatch.setattr(tc, "_today", lambda: date(2026, 11, 21))
    t = _trip(db, date(2026, 3, 10), date(2026, 3, 11))
    before = tc.list_missing(db, sync=False)
    assert any(i["trip_id"] == t.id for i in before["trip_items_missing"])
    edit.reject_trip(db, t.id, "Not a business trip")
    after = tc.list_missing(db, sync=False)
    assert not any(i["trip_id"] == t.id for i in after["trip_items_missing"])


# ---------------------------------------------------------------- Del 5


def test_new_html_only_senders_seeded(db):  # noqa: F811
    from app.models import HtmlOnlySender
    from app.services.c46_migrations import seed_c46_html_only_senders

    assert seed_c46_html_only_senders(db) == 2
    assert seed_c46_html_only_senders(db) == 0
    pats = {s.sender_pattern for s in db.query(HtmlOnlySender).all()}
    assert "invoice+statements@mail.anthropic.com" in pats
    assert "invoice+statements+acct_1epydaahwgq34jln@stripe.com" in pats


# ---------------------------------------------------------------- Del 6


def test_netvisor_line_no_duplicate_cities_and_plain_dash():
    from app.services.trip_english import netvisor_line_description

    text = netvisor_line_description(
        ["Ängelholm, Ljungby", "Ljungby", "Ljungby"], "SE", "Customer visits – Agda",
    )
    assert text == "Business trip Ängelholm and Ljungby, Sweden - Customer visits - Agda"
    assert "–" not in text and "—" not in text
    three = netvisor_line_description(["Malmö", "Lund → Malmö", "Växjö"], "SE", None)
    assert three == "Business trip Malmö, Lund and Växjö, Sweden"


def test_generated_texts_use_plain_dash():
    from datetime import datetime

    from app.services.apple_receipt import parse_apple_receipt
    from app.services.bill_line_status import private_expense_description
    from app.services.bezala_field_mapper import build_receipt_params
    from app.services.receipt_kind import ChangeFeeInfo

    r = parse_apple_receipt(text="iCloud+ monthly Total €2,99", html="",
                            received_at=datetime(2026, 2, 1))
    assert r.description_en() == "Apple iCloud+ subscription - Feb 2026"
    assert private_expense_description("Taxi — home") == (
        "Private expense - to be deducted from salary. Taxi - home"
    )
    fee = ChangeFeeInfo("HEL", "GOT", "2026-10-13", 0.02, "EUR", "x")
    assert fee.description_en() == "Finnair change fee - flight HEL-GOT 13 Oct 2026"
    params = build_receipt_params(
        file_name="x.pdf", sender="x@lovable.dev", accounts=[], cost_centers=[],
        vendor="Lovable", amount=25.0, currency="USD",
        receipt_date="2026-05-05", category="mjukvara",
        description_override="Lovable Pro — May 5–Jun 5, 2026",
    )
    assert params["description"] == "Lovable Pro - May 5-Jun 5, 2026"
