"""C52 — kontoförslag bara från samma leverantör, Mikkos kontoregler,
Bolt-testet (hårt låst), momsrapport och "redan hanterat i Bezala"."""

from __future__ import annotations

import pytest

from app.services.c50_accounts import suggest_account

ACCOUNTS = [
    {"id": 67113, "name": "Paikoituskulut"},
    {"id": 67120, "name": "Matkaliput"},
    {"id": 67101, "name": "Taksikulut"},
    {"id": 67130, "name": "Hotelli- ym. majoitus"},
    {"id": 166648, "name": "AI työkalut"},
    {"id": 67140, "name": "Muut ajoneuvokulut"},
    {"id": 29000, "name": "Tiliöimättömät ostolaskut"},
]
# Prod 2026-11-22: Moovy-utlägget 5575526 föreslogs för 17 av 18 kort.
TXS = [
    {"id": 5575526, "date": "2026-11-19",
     "description": "Moovy parking at Helsinki Airport, 19 November 2026",
     "vat_lines": [{"expense_account_id": 67113, "vat_code_id": 1355,
                    "cost_center_ids": [927151]}]},
    {"id": 5500001, "date": "2026-11-01", "description": "Flight Helsinki - Oslo, airport express",
     "vat_lines": [{"expense_account_id": 67113}]},
    # Tiliöimättömät får aldrig föreslås, inte ens från samma leverantör.
    {"id": 5500002, "date": "2026-11-20", "description": "Flytoget train",
     "vat_lines": [{"expense_account_id": 29000}]},
]

# De 18 korten i svep-kön (prod 2026-11-22).
CARDS = (
    [("Flytoget", "MIKKO KEINONEN: FLYTOGET 1009001 MC, OSLO, NO 26.84 EUR",
      "Matkaliput")] * 2
    + [("Moovy", "MIKKO KEINONEN: MOOVY, TAMPERE, FI 37.49 EUR", "Paikoituskulut")] * 4
    + [("Finnair", "MIKKO KEINONEN: FINNAIR, VANTAA, FI 446.77 EUR", "Matkaliput")] * 7
    + [("Skånetrafiken", "MIKKO KEINONEN: SKANETRAFIKEN APP, KRISTIANSTAD, SE 14.01 EUR",
        "Matkaliput")] * 4
    + [("Lovable", "MIKKO KEINONEN: LOVABLE, DOVER, US 100.0 EUR", "AI työkalut")]
)


def test_eighteen_cards():
    assert len(CARDS) == 18


@pytest.mark.parametrize("vendor,card,expected", CARDS)
def test_swipe_cards_get_the_right_account(vendor, card, expected):
    s = suggest_account(vendor, None, None,
                        f"{vendor} receipt, parking at the airport" if vendor != "Moovy" else None,
                        accounts=ACCOUNTS, transactions=TXS, category=None,
                        card_description=card)
    assert s["name"] == expected, s
    assert not s["missing"]
    if vendor != "Moovy":
        assert "5575526" not in (s["source"] or "")


def test_moovy_uses_its_own_latest_expense():
    s = suggest_account("Moovy", accounts=ACCOUNTS, transactions=TXS,
                        card_description="MIKKO KEINONEN: MOOVY, TAMPERE, FI 37.49 EUR")
    assert s["account_id"] == 67113 and "5575526" in s["source"]
    assert s["vat_code_id"] == 1355 and s["cost_center_ids"] == [927151]


@pytest.mark.parametrize("vendor,expected", [
    ("Arlanda Express", "Matkaliput"), ("SAS", "Matkaliput"), ("Norwegian", "Matkaliput"),
    ("Taxi Göteborg", "Taksikulut"), ("Bolt", "Taksikulut"), ("Uber", "Taksikulut"),
    ("Scandic Oslo City", "Hotelli- ym. majoitus"), ("Clarion Hotel", "Hotelli- ym. majoitus"),
    ("Cursor", "AI työkalut"), ("Anthropic", "AI työkalut"), ("Bolt.new", "AI työkalut"),
    ("ChatGPT", "AI työkalut"), ("Europcar car wash", "Muut ajoneuvokulut"),
])
def test_mikkos_rules(vendor, expected):
    s = suggest_account(vendor, accounts=ACCOUNTS, transactions=[])
    assert s["name"] == expected


def test_unknown_vendor_is_missing_never_guessed():
    s = suggest_account("Unknown Shop Oy", accounts=ACCOUNTS, transactions=TXS)
    assert s["missing"] and s["account_id"] is None and s["flag"] == "Account missing"


def test_tiliomattomat_never_suggested():
    txs = [{"id": 1, "date": "2026-11-20", "description": "Acme Widgets order",
            "vat_lines": [{"expense_account_id": 29000}]}]
    s = suggest_account("Acme Widgets", accounts=ACCOUNTS, transactions=txs)
    assert s["missing"] and s["name"] is None


def test_other_vendor_further_in_description_does_not_count():
    txs = [{"id": 7, "date": "2026-11-21", "description": "Parking for the Finnair flight",
            "vat_lines": [{"expense_account_id": 67113}]}]
    s = suggest_account("Finnair", accounts=ACCOUNTS, transactions=txs)
    assert s["name"] == "Matkaliput"


# ================================================================ Del 1, 3, 4 (db)

import os
from datetime import date

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")

from tests.backend.test_c42 import db  # noqa: F401
from tests.backend.test_c47 import _receipt
from tests.backend.test_c50 import bound_db  # noqa: F401


class BoltBezala:
    def __init__(self, after_state="draft"):
        self.after_state = after_state
        self.puts = []
        self.desc = ""

    def get_transaction(self, tx_id):
        state = self.after_state if self.puts else "draft"
        return {"id": int(tx_id), "state": state, "description": self.desc,
                "approvers": None, "current_approvers_ids": [], "transaction_approvers": []}

    def _put_transaction_unchecked(self, tx_id, fields):
        self.puts.append((str(tx_id), dict(fields)))
        self.desc = fields.get("description", self.desc)
        return {"status_code": 200}

    def close(self):
        pass


def test_bolt_test_sends_only_description_once(bound_db):  # noqa: F811
    from app.services import bezala_write_guard as guard
    from app.services.c52 import BOLT_DESCRIPTION, run_bolt_test

    b = BoltBezala()
    out = run_bolt_test(bound_db, client=b)
    assert b.puts == [("5579365", {"description": BOLT_DESCRIPTION})]
    assert "state" not in b.puts[0][1]
    assert "-" in BOLT_DESCRIPTION or "," in BOLT_DESCRIPTION
    assert "–" not in BOLT_DESCRIPTION
    assert out["before"]["state"] == "draft" and out["after"]["state"] == "draft"
    assert out["after"]["description"] == BOLT_DESCRIPTION and out["still_draft"]
    assert not guard.writes_halted(bound_db)
    again = run_bolt_test(bound_db, client=b)
    assert again["ran"] is False and len(b.puts) == 1


def test_bolt_test_state_change_trips_kill_switch(bound_db):  # noqa: F811
    from app.services import bezala_write_guard as guard
    from app.services.c52 import run_bolt_test

    out = run_bolt_test(bound_db, client=BoltBezala(after_state="reviewing"))
    assert out["put"] == "blocked" and not out["still_draft"]
    assert guard.writes_halted(bound_db)


def test_approver_fields_are_watched():
    from app.services import bezala_write_guard as guard

    for f in ("current_approvers_ids", "transaction_approvers", "approved_by"):
        assert f in guard.STATUS_FIELDS


def _no_rate(*_a):
    return None


def _rate_nok(day, frm, to):
    return {("NOK", "EUR"): 0.0855, ("SEK", "EUR"): 0.0935}.get((frm, to))


def test_blind_spot_dry_run_matches_and_changes_nothing(db):  # noqa: F811
    from app.services.c52 import find_matches

    gpt = _receipt(db, date(2026, 3, 14), "OpenAI", 22.99, waiting=False,
                   subject="Your ChatGPT Plus receipt")
    scandic = _receipt(db, date(2026, 4, 13), "Scandic Oslo City", 1887.0, "NOK",
                       waiting=False)
    arlanda = _receipt(db, date(2026, 4, 30), "Arlanda Express", 320.0, "SEK", waiting=False)
    other = _receipt(db, date(2026, 5, 2), "Unknown Shop", 9.99, waiting=False)
    waiting = _receipt(db, date(2026, 5, 2), "Moovy", 12.0)  # har waiting_status → inte med
    txs = [
        {"id": 1, "date": "2026-03-15", "description": "ChatGPT Plus subscription - Mar 2026",
         "billed_amount": "22.99", "billed_amount_currency": "EUR", "state": "accounted"},
        {"id": 2, "date": "2026-04-13", "description": "Scandic Oslo City hotel 1 night",
         "billed_amount": "161.33", "billed_amount_currency": "EUR",
         "original_amount": "1887.00", "original_amount_currency": "NOK"},
        {"id": 3, "date": "2026-04-30", "description": "Airport train to Stockholm city",
         "billed_amount": "29.90", "billed_amount_currency": "EUR"},
    ]
    res = find_matches(db, transactions=txs, rate=_rate_nok)
    ids = {m["message_id"]: m for m in res["matches"]}
    assert set(ids) == {gpt.message_id, scandic.message_id}
    assert ids[scandic.message_id]["bezala"]["bezala_transaction_id"] == 2
    weak = {m["message_id"] for m in res["amount_and_date_only"]}
    assert weak == {arlanda.message_id}  # SEK→EUR inom tolerans, leverantör saknas
    assert other.message_id in {m["message_id"] for m in res["no_match"]}
    assert waiting.message_id not in {m["message_id"] for m in
                                      res["matches"] + res["amount_and_date_only"]
                                      + res["no_match"]}
    db.refresh(gpt)
    assert gpt.waiting_status is None  # torrkörning


def test_date_window_is_one_day(db):  # noqa: F811
    from app.services.c52 import find_matches

    _receipt(db, date(2026, 3, 14), "OpenAI ChatGPT", 22.99, waiting=False)
    txs = [{"id": 1, "date": "2026-03-16", "description": "ChatGPT Plus",
            "billed_amount": "22.99", "billed_amount_currency": "EUR"}]
    res = find_matches(db, transactions=txs, rate=_no_rate)
    assert not res["matches"]


def test_apply_only_confirmed_receipts(db):  # noqa: F811
    from app.services.c52 import apply_already_handled

    a = _receipt(db, date(2026, 3, 14), "OpenAI ChatGPT", 22.99, waiting=False)
    b = _receipt(db, date(2026, 4, 14), "OpenAI ChatGPT", 22.99, waiting=False)
    txs = [{"id": 1, "date": "2026-03-14", "description": "ChatGPT Plus",
            "billed_amount": "22.99", "billed_amount_currency": "EUR"},
           {"id": 2, "date": "2026-04-14", "description": "ChatGPT Plus",
            "billed_amount": "22.99", "billed_amount_currency": "EUR"}]
    out = apply_already_handled(db, [a.message_id, "not-a-match"], transactions=txs,
                                rate=_no_rate)
    assert [x["message_id"] for x in out["applied"]] == [a.message_id]
    assert out["skipped"][0]["message_id"] == "not-a-match"
    db.refresh(a)
    db.refresh(b)
    assert a.waiting_status == "already_handled_in_bezala" and b.waiting_status is None
    assert apply_already_handled(db, [])["applied"] == []


def test_vat_report_explains_unread(db):  # noqa: F811
    from datetime import datetime, timedelta

    from app.services.c52 import vat_report

    today = datetime.utcnow().date()
    r1 = _receipt(db, today - timedelta(days=30), "Moovy", 37.49, waiting=False)
    r2 = _receipt(db, today - timedelta(days=10), "Moovy", 37.49, waiting=False,
                  vat_info={"lines": [{"rate": 25.5}]})
    r3 = _receipt(db, today - timedelta(days=5), "Moovy", 10.0, waiting=False,
                  vat_info={"lines": [], "unread": True})
    rep = vat_report(db, [r1.message_id])
    assert rep["counts"] == {"not_read": 1, "read": 1, "read_no_vat_found": 1}
    assert rep["not_read"][0]["message_id"] == r1.message_id
    assert rep["selected"][0]["vat_state"] == "not_read"
    assert r2.message_id and r3.message_id
