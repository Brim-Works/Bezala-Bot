"""C51 — momsregler, konton, svep-kort och utkast-skydd.

Exemplen är de som C7 + Mikko verifierat mot kvitton 2026-11-22.
"""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services import c51_vat as v  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _line, _receipt  # noqa: E402
from tests.backend.test_c50 import FakeBezala, bound_db  # noqa: E402,F401

PDF = b"%PDF-1.4\nfake"

ARLANDA = "MIKKO KEINONEN: ARLANDA EXPRESS, STOCKHOLM, SE 55.95 EUR"
SCANDIC = "MIKKO KEINONEN: SCANDIC GO GASVERKSGAT, HELSINGBORG, SE 83.66 EUR"
BOLT = "MIKKO KEINONEN: BOLT (BY STACKBLITZ), SAN FRANCISCO, US 23.3 EUR"
ANTHROPIC = "MIKKO KEINONEN: ANTHROPIC, SAN FRANCISCO, US 112.95 EUR"
CURSOR = "MIKKO KEINONEN: CURSOR, AI POWERED IDE, NEW YORK, US 20.0 EUR"
MOOVY = "MIKKO KEINONEN: MOOVY, HELSINKI, FI 37.49 EUR"
FINNAIR_REFUND = "MIKKO KEINONEN: FINNAIR, HELSINKI, FI -134.48 EUR"
TAXI_GBG = "MIKKO KEINONEN: TAXI GOTEBORG, GOTEBORG, SE 49.97 EUR"
RESTAURANT_FI = "MIKKO KEINONEN: RAVINTOLA SAVOY, HELSINKI, FI 64.00 EUR"


def vat(**kw):
    return v.suggest_vat(**kw)


# ---------------------------------------------------------------- regel 1


@pytest.mark.parametrize("category,desc,amount", [
    ("Kollektivtrafik", ARLANDA, 55.95),
    ("Hotell", SCANDIC, 83.66),
    ("Taxi", TAXI_GBG, 49.97),
    ("Parkering", "MIKKO KEINONEN: EASYPARK, OSLO, NO 12.0 EUR", 12.0),
    ("Mat", "MIKKO KEINONEN: RESTAURANG, KOBENHAVN, DK 40.0 EUR", 40.0),
])
def test_rule1_on_site_abroad_is_0(category, desc, amount):
    # Även när kvittot visar svensk moms (12 %) dras den inte av i Finland.
    r = vat(category=category, card_description=desc, card_amount=amount,
            vat_info={"lines": [{"rate": 12}], "document": "receipt"})
    assert r["vat"] == v.VAT_0
    assert r["rule"].startswith("1:")


def test_rule1_hotel_card_slip_only_flags_folio_missing():
    r = vat(category="Hotell", card_description=SCANDIC, card_amount=83.66,
            vat_info={"lines": [], "document": "card_slip"})
    assert r["vat"] == v.VAT_0
    assert v.FLAG_FOLIO_MISSING in r["flags"]


# ---------------------------------------------------------------- regel 2


def test_rule2_international_flight_is_0():
    r = vat(category="Flyg", texts=["Finnair flight Helsinki to Copenhagen (AY0957)"],
            card_description="MIKKO KEINONEN: FINNAIR, HELSINKI, FI 134.48 EUR",
            card_amount=134.48,
            vat_info={"lines": [], "document": "receipt"})
    assert r["vat"] == v.VAT_0 and r["rule"].startswith("2:")


def test_rule2_flight_segments_decide():
    segs = [{"from": "HEL", "to": "CPH"}]
    assert vat(category="Flyg", flight_segments=segs, card_amount=100,
               card_description="X, HELSINKI, FI 100 EUR")["vat"] == v.VAT_0
    dom = vat(category="Flyg", flight_segments=[{"from": "HEL", "to": "OUL"}], card_amount=100,
              card_description="X, HELSINKI, FI 100 EUR",
              vat_info={"lines": [{"rate": 13.5}]})
    assert dom["vat"] == v.VAT_13_5


# ---------------------------------------------------------------- regel 3/4


@pytest.mark.parametrize("vendor,desc", [
    ("Bolt.new (StackBlitz)", BOLT), ("Cursor", CURSOR), ("Lovable", "LOVABLE, US 25 EUR"),
])
def test_rule3_non_eu_service_without_vat(vendor, desc):
    r = vat(category="AI-verktyg", texts=[vendor], card_description=desc, card_amount=23.3,
            vat_info={"lines": [], "document": "invoice"})
    assert r["vat"] == v.VAT_NON_EU


def test_rule3_bolt_without_vat_info_still_non_eu():
    r = vat(category="AI-verktyg", texts=["Bolt.new (StackBlitz)"], card_description=BOLT,
            card_amount=23.3)
    assert r["vat"] == v.VAT_NON_EU


def test_rule4_eu_reverse_charge():
    r = vat(category="Mjukvara", texts=["Some EU SaaS"],
            card_description="MIKKO KEINONEN: SAAS OY, DUBLIN, IE 30.0 EUR", card_amount=30,
            vat_info={"lines": [], "reverse_charge": True, "seller_country": "IE"})
    assert r["vat"] == v.VAT_EU


# ---------------------------------------------------------------- regel 5


def test_rule5_anthropic_until_14_nov_is_0_with_flag():
    r = vat(category="AI-verktyg", texts=["Anthropic"], card_description=ANTHROPIC,
            card_amount=112.95, receipt_date="2026-11-14")
    assert r["vat"] == v.VAT_0
    assert v.FLAG_FI_VAT_FOREIGN in r["flags"]


def test_rule5_anthropic_from_14_dec_is_non_eu():
    r = vat(category="AI-verktyg", texts=["Anthropic"], card_description=ANTHROPIC,
            card_amount=112.95, receipt_date="2026-12-14")
    assert r["vat"] == v.VAT_NON_EU


def test_rule5_anthropic_receipt_wins_finnish_vat_after_dec():
    # Kvittot först: tar Anthropic fortfarande finsk moms → 0% + flagga.
    r = vat(category="AI-verktyg", texts=["Anthropic"], card_description=ANTHROPIC,
            card_amount=112.95, receipt_date="2026-12-14",
            vat_info={"lines": [{"rate": 25.5}], "finnish_vat_charged": True,
                      "seller_country": "US"})
    assert r["vat"] == v.VAT_0 and v.FLAG_FI_VAT_FOREIGN in r["flags"]


def test_rule5_anthropic_reverse_charge_on_receipt():
    r = vat(category="AI-verktyg", texts=["Anthropic"], card_description=ANTHROPIC,
            card_amount=112.95, receipt_date="2026-12-14",
            vat_info={"lines": [], "reverse_charge": True, "seller_country": "US"})
    assert r["vat"] == v.VAT_NON_EU


# ---------------------------------------------------------------- regel 6


def test_rule6_moovy_rate_from_receipt():
    r = vat(category="Parkering", texts=["Moovy"], card_description=MOOVY, card_amount=37.49,
            vat_info={"lines": [{"rate": 25.5, "gross": 37.49}], "document": "receipt"})
    assert r["vat"] == v.VAT_25_5
    assert r["lines"] == [{"vat": v.VAT_25_5, "amount": 37.49}]


def test_rule6_several_rates_give_several_lines():
    r = vat(category="Mat", card_description=RESTAURANT_FI, card_amount=64.0,
            vat_info={"lines": [{"rate": 13.5, "gross": 50.0}, {"rate": 25.5, "gross": 14.0}]})
    assert [ln["vat"] for ln in r["lines"]] == [v.VAT_13_5, v.VAT_25_5]
    assert [ln["amount"] for ln in r["lines"]] == [50.0, 14.0]


def test_rule6_unknown_rate_is_flagged_not_guessed():
    r = vat(category="Mat", card_description=RESTAURANT_FI, card_amount=64.0,
            vat_info={"lines": [{"rate": 24}]})
    assert r["vat"] is None and v.FLAG_RATE_UNKNOWN in r["flags"]


# ---------------------------------------------------------------- regel 7


def test_rule7_finnish_card_slip_account_default_and_flag():
    # C60: Moovy/Finavia är 25,5 % (Mikko); annan finsk parkering → kontots standard.
    r = vat(category="Parkering", card_description="X: EUROPARK, HELSINKI, FI 9.0 EUR",
            card_amount=9.0, vat_info={"lines": [], "document": "card_slip"})
    assert r["vat"] == v.VAT_ACCOUNT_DEFAULT
    assert v.FLAG_NO_BREAKDOWN in r["flags"]


def test_rule7_finnish_hotel_card_slip_flags_folio():
    r = vat(category="Hotell", card_description="X HOTEL, TAMPERE, FI 120 EUR",
            card_amount=120, vat_info={"lines": [], "document": "card_slip"})
    assert v.FLAG_FOLIO_MISSING in r["flags"]


def test_rule7_vat_not_read_yet_is_flagged_differently():
    r = vat(category="Parkering", card_description="X: EUROPARK, HELSINKI, FI 9.0 EUR",
            card_amount=9.0)
    assert r["vat"] == v.VAT_ACCOUNT_DEFAULT and v.FLAG_VAT_NOT_READ in r["flags"]


# ---------------------------------------------------------------- regel 8


def test_rule8_refund_same_vat_negative_amount_and_prefix():
    r = vat(category="Flyg", texts=["Finnair flight Helsinki to Copenhagen (AY0957)"],
            card_description=FINNAIR_REFUND, card_amount=-134.48)
    assert r["vat"] == v.VAT_0 and r["refund"] is True
    assert r["lines"][0]["amount"] == -134.48
    fill = v.bezala_fill(account={"name": "Matkaliput"}, category="Flyg",
                         texts=["Finnair flight Helsinki to Copenhagen (AY0957)"],
                         card_description=FINNAIR_REFUND, card_amount=-134.48,
                         receipt_date="2026-11-18", vat_info=None, flight_segments=None,
                         description="Finnair flight Helsinki to Copenhagen (AY0957), "
                                     "26 November 2026, trip cancelled")
    assert fill["copy"]["description"].startswith("Refund - Finnair flight")
    assert v.refund_description("Refund - X") == "Refund - X"


def test_rule8_purchase_receipt_never_coupled_to_negative_line():
    assert v.refund_coupling_error(-134.48, 134.48, "Finnair e-ticket receipt") is not None
    assert v.refund_coupling_error(-134.48, 134.48, "Finnair refund confirmation") is None
    assert v.refund_coupling_error(-134.48, -134.48, "Finnair") is None
    assert v.refund_coupling_error(134.48, 134.48, "Finnair") is None


def test_rule8_match_to_bezala_refuses_purchase_on_refund_line(db, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from app import main

    line = _line(db, 2511111, date(2026, 11, 18), -134.48, desc=FINNAIR_REFUND)
    m = _receipt(db, date(2026, 11, 18), "Finnair", 134.48, subject="Finnair e-ticket receipt")

    class B:
        def list_missing_receipts(self):
            return []

    monkeypatch.setattr(main, "_get_drive_or_401", lambda: object())
    with pytest.raises(HTTPException) as exc:
        main._do_match_to_bezala(row=m, bill_line_id=line.bill_line_id, db=db, bezala=B())
    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "refund_mismatch"


# ---------------------------------------------------------------- Del 3 konton


@pytest.mark.parametrize("category,texts,expected", [
    ("Bilhyra", ["Europcar Aviapolis, company car wash"], "Muut ajoneuvokulut"),
    ("Flyg", ["Finnair"], "Matkaliput"),
    ("Tåg", ["SJ"], "Matkaliput"),
    ("Kollektivtrafik", ["Arlanda Express"], "Matkaliput"),
    ("Hotell", ["Scandic"], "Hotelli- ym. majoitus"),
    ("Taxi", ["Taxi Göteborg"], "Taksikulut"),
    ("Parkering", ["Moovy"], "Paikoituskulut"),
    ("AI-verktyg", ["Some new AI"], "AI työkalut"),
    ("Annat", ["Something"], None),
])
def test_category_accounts(category, texts, expected):
    assert v.category_account(category, *texts) == expected


def test_c50_latest_expense_rule_comes_before_category_table():
    from app.services.c50_accounts import suggest_account

    accounts = [{"id": 1, "name": "Paikoituskulut"}, {"id": 2, "name": "Muut kulut"}]
    txs = [{"id": 9, "date": "2026-10-01", "description": "Moovy parking",
            "vat_lines": [{"expense_account_id": 2}]}]
    r = suggest_account("Moovy", accounts=accounts, transactions=txs, category="Parkering")
    assert r["account_id"] == 2 and "latest expense" in r["source"]
    r2 = suggest_account("Moovy", accounts=accounts, transactions=[], category="Parkering")
    assert r2["name"] == "Paikoituskulut" and r2["account_id"] == 1
    r3 = suggest_account("Unknown", accounts=accounts, transactions=[], category="Annat")
    assert r3["missing"] is True


# ---------------------------------------------------------------- svep-kortet


def test_swipe_card_has_fill_with_copy_fields(db):  # noqa: F811
    from app.services import c50_accounts
    from app.services.decision_queue import list_pending

    c50_accounts.set_reference([{"id": 7, "name": "Paikoituskulut"}], [])
    _line(db, 2512222, date(2026, 11, 19), 37.49, desc=MOOVY)
    _receipt(db, date(2026, 11, 19), "Moovy", 37.49, category="Parkering",
             ai_description_en="Parking at Helsinki-Vantaa Airport P2, 19 November 2026",
             vat_info={"lines": [{"rate": 25.5, "gross": 37.49}], "document": "receipt"})
    items = [i for i in list_pending(db)["items"] if i["kind"] == "receipt"]
    assert items, "receipt card expected"
    fill = items[0]["bezala_fill"]
    assert fill["copy"] == {
        "account": "Paikoituskulut", "vat": "25,5%",
        "cost_center": "VIS128 Visma HRM Sverige AB",
        "description": "Parking at Helsinki-Vantaa Airport P2, 19 November 2026",
    }


# ---------------------------------------------------------------- kvittoanalys


def test_analyzer_normalizes_vat_info():
    from app.services.receipt_analyzer import normalize_vat_info

    out = normalize_vat_info({"lines": [{"rate": "25,5%", "gross": "37.49"}, {"rate": 99}],
                              "reverse_charge": 0, "seller_country": "fi",
                              "document": "Card_Slip"})
    assert out["lines"] == [{"rate": 25.5, "net": None, "vat": None, "gross": 37.49}]
    assert out["seller_country"] == "FI" and out["document"] == "card_slip"
    assert normalize_vat_info(None) is None


# ---------------------------------------------------------------- Del 1 utkast-skydd


def test_put_refused_unless_state_is_draft(bound_db):  # noqa: F811
    from app.services import bezala_write_guard as guard

    # Bezala har bara draft/reviewing/accounted; "unapproved" är INTE utkast.
    for state in ("reviewing", None, "unapproved", "accounted", "approved"):
        b = FakeBezala([state])
        with pytest.raises(guard.BezalaWriteBlocked):
            guard.guarded_put(b, 5579319, {"description": "x"}, db=bound_db)
        assert b.puts == []
    assert not guard.writes_halted(bound_db)


def test_put_allowed_on_draft(bound_db):  # noqa: F811
    from app.services import bezala_write_guard as guard

    b = FakeBezala(["draft", "draft"])
    guard.guarded_put(b, 1, {"description": "x"}, db=bound_db)
    assert b.puts == [(1, {"description": "x"})]


def test_put_that_moves_draft_to_reviewing_trips_kill_switch(bound_db):  # noqa: F811
    from app.services import bezala_write_guard as guard

    b = FakeBezala(["draft", "reviewing"])
    with pytest.raises(guard.BezalaWriteBlocked):
        guard.guarded_put(b, 1, {"description": "x"}, db=bound_db)
    assert guard.writes_halted(bound_db)


def test_state_is_never_allowed_in_any_write():
    from app.services import bezala_write_guard as guard

    with pytest.raises(guard.BezalaWriteBlocked):
        guard.check_fields({"state": "unapproved"})
    with pytest.raises(guard.BezalaWriteBlocked):
        guard.check_fields({"draft": "1", "state": "unapproved"},
                           guard.ALLOWED_ATTACHMENT_FORM_FIELDS)


def test_inspect_is_read_only():
    from app.services.c51_bezala_inspect import inspect

    class C:
        calls = []

        def get_transaction(self, tid):
            self.calls.append(("GET", tid))
            return {"id": int(tid), "state": "reviewing", "approvers": ["X"],
                    "description": "Arlanda"}

        def list_transactions(self):
            self.calls.append(("GET", "list"))
            return [{"id": 5580000, "bill_line_id": 2513372, "state": "draft"}]

        def get_raw(self, path):  # C62: bara GET
            self.calls.append(("GET", path))
            raise RuntimeError("404")

        def __getattr__(self, name):  # any write method would blow up
            raise AssertionError(f"unexpected call {name}")

    c = C()
    out = inspect("5579319", client=c)
    t = out["transactions"][0]
    assert t["state"] == "reviewing" and t["is_draft"] is False
    out2 = inspect(bill_line_id="2513372", client=c)
    assert out2["transactions"][0]["transaction_id"] == "5580000"
    assert all(m == "GET" for m, _ in c.calls)


def test_inspect_falls_back_to_amount_and_date():
    from app.services.c51_bezala_inspect import inspect

    class C:
        def list_transactions(self):
            return [{"id": 1, "billed_amount": "23.30", "date": "2026-11-16", "state": "draft"},
                    {"id": 2, "amount": "23.30", "date": "2026-10-01", "state": "reviewing"}]

        def get_transaction(self, tid):
            return {"id": int(tid), "state": "draft"}

    out = inspect(bill_line_id="2513372", client=C(), amount=23.3, day="2026-11-16")
    assert [t["transaction_id"] for t in out["transactions"]] == ["1"]
    assert out["transactions"][0]["is_draft"] is True
    assert out["listing"]["count"] == 2


def test_attach_creating_draft_state_does_not_trip_kill_switch(bound_db):  # noqa: F811
    from app.services import bezala_write_guard as guard

    class C:
        def get_transaction(self, tid):
            return {"id": tid, "state": "draft"}

    guard.verify_new_draft(C(), 1, operation="attach_file", db=bound_db)
    assert not guard.writes_halted(bound_db)
