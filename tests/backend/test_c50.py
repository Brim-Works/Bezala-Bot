"""C50 Del 0 — boten får aldrig skicka utlägg för attest.

* skyddsfunktionen nekar statusfält (state/status/approver/submit ...)
* statusändring efter en skrivning → CRITICAL + kill switch i databasen
* AUTO_COUPLE_ENABLED=false → ingen automatisk Bezala-skrivning
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import FakeCouple, _line, _receipt  # noqa: E402

PDF = b"%PDF-1.4\nfake"


@pytest.fixture()
def bound_db(db, monkeypatch):  # noqa: F811
    """Låt skyddsfunktionen (som öppnar egna sessioner) använda testdatabasen."""
    from contextlib import contextmanager

    from app.services import bezala_write_guard as guard

    @contextmanager
    def _session(_db=None):
        yield db

    monkeypatch.setattr(guard, "_session", _session)
    return db


class FakeBezala:
    """Minimal Bezala-klient: GET ger states i tur och ordning."""

    def __init__(self, states):
        self.states = list(states)
        self.puts = []

    def get_transaction(self, tx_id):
        state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"id": tx_id, "state": state}

    def _put_transaction_unchecked(self, tx_id, fields):
        self.puts.append((tx_id, dict(fields)))
        return {"status_code": 200}


# ---------------------------------------------------------------- fält


@pytest.mark.parametrize("field", [
    "state", "status", "approver_id", "submit", "send_for_approval",
    "submitted", "approval_status", "date", "bill_id", "connected_to_bill_line",
])
def test_guard_refuses_status_and_other_fields(bound_db, field):
    from app.services import bezala_write_guard as guard

    client = FakeBezala(["unapproved"])
    with pytest.raises(guard.BezalaWriteBlocked):
        guard.guarded_put(client, "5579031", {"description": "x", field: "anything"})
    assert client.puts == []


def test_guard_allows_description_account_vat(bound_db):
    from app.services import bezala_write_guard as guard

    client = FakeBezala(["unapproved", "unapproved"])
    guard.guarded_put(client, "1", {"description": "x", "credit_account_id": 1,
                                    "vat_lines_attributes": [{}], "cost_center_id": 2})
    assert len(client.puts) == 1
    assert not guard.writes_halted(bound_db)


def test_update_transaction_never_sends_state(bound_db, monkeypatch):
    from app.services.bezala_client import BezalaClient

    monkeypatch.setenv("BEZALA_METADATA_PUT_ENABLED", "true")
    client = BezalaClient.__new__(BezalaClient)
    sent = []
    client.get_transaction = lambda tx: {"id": tx, "state": "unapproved"}
    client._put_transaction_unchecked = lambda tx, f: sent.append(f) or {}
    client.update_transaction("9", description="D", date="2026-11-01", credit_account_id=5,
                              extra_fields={"state": "reviewing", "bill_id": 1,
                                            "submit": True, "cost_center_id": 3})
    assert sent == [{"description": "D", "credit_account_id": 5, "cost_center_id": 3}]


def test_metadata_put_off_by_default_no_write(bound_db, monkeypatch):
    from app.services.bezala_client import BezalaClient

    monkeypatch.delenv("BEZALA_METADATA_PUT_ENABLED", raising=False)
    client = BezalaClient.__new__(BezalaClient)
    client._request = MagicMock(side_effect=AssertionError("no HTTP call allowed"))
    client.update_transaction("9", description="Anthropic", date="2026-11-01",
                              credit_account_id=5)
    client._request.assert_not_called()


# ---------------------------------------------------------------- kill switch


def test_status_change_after_write_trips_kill_switch(bound_db, monkeypatch):
    from app.services import bezala_write_guard as guard

    monkeypatch.setenv("AUTO_COUPLE_ENABLED", "true")
    assert guard.auto_couple_enabled(bound_db)
    client = FakeBezala(["unapproved", "reviewing"])
    with pytest.raises(guard.BezalaWriteBlocked):
        guard.guarded_put(client, "5579319", {"description": "Arlanda Express"})
    assert guard.writes_halted(bound_db)
    # Överlever omstart: ligger i databasen, env är fortfarande true.
    assert not guard.auto_couple_enabled(bound_db)
    with pytest.raises(guard.BezalaWriteBlocked):
        guard.guarded_put(FakeBezala(["unapproved"]), "2", {"description": "y"})
    from app.models import BezalaWriteLog

    log = bound_db.query(BezalaWriteLog).filter(BezalaWriteLog.outcome == "status_changed").one()
    assert log.transaction_id == "5579319"
    assert log.state_after == {"state": "reviewing"}


def test_any_status_field_change_trips(bound_db):
    from app.services import bezala_write_guard as guard

    class C(FakeBezala):
        n = 0

        def get_transaction(self, tx_id):
            self.n += 1
            return {"state": "unapproved", "approver_id": None if self.n == 1 else 77}

    with pytest.raises(guard.BezalaWriteBlocked):
        guard.guarded_put(C([]), "1", {"description": "x"})
    assert guard.writes_halted(bound_db)


def test_attach_file_not_draft_after_post_trips_kill_switch(bound_db):
    from app.services import bezala_write_guard as guard
    from app.services.bezala_client import BezalaClient, BezalaError

    client = BezalaClient.__new__(BezalaClient)
    forms = []

    def fake_request(method, path, **kw):
        r = MagicMock()
        r.status_code = 200
        r.headers = {}
        if method == "POST":
            forms.append(kw.get("data"))
            r.json = MagicMock(return_value={"id": 1, "transaction_id": 5579032})
        else:
            r.json = MagicMock(return_value={"id": 5579032, "state": "reviewing"})
        r.text = ""
        return r

    client._request = fake_request
    with pytest.raises(BezalaError):
        client.attach_file(2500000, "k.pdf", PDF, description="Scandic Go")
    assert guard.writes_halted(bound_db)
    # Beskrivningen skickas inte längre i formuläret.
    assert forms == [{"draft": "1", "bill_line_id": "2500000"}]  # C51 — aldrig state
    # Nästa skrivning stoppas innan något HTTP-anrop.
    with pytest.raises(BezalaError):
        client.attach_file(2500001, "k.pdf", PDF)
    assert len(forms) == 1


def test_attach_file_with_metadata_makes_no_put(bound_db, monkeypatch):
    from app.services.bezala_client import BezalaClient

    monkeypatch.delenv("BEZALA_METADATA_PUT_ENABLED", raising=False)
    client = BezalaClient.__new__(BezalaClient)
    methods = []

    def fake_request(method, path, **kw):
        methods.append(method)
        r = MagicMock()
        r.status_code = 200
        r.headers = {}
        r.text = ""
        r.json = MagicMock(return_value={"id": 1, "transaction_id": 7, "state": "unapproved"})
        return r

    client._request = fake_request
    client.attach_file(42, "k.pdf", PDF, description="Anthropic", date="2026-11-01",
                       credit_account_id=67100, vat_lines_attributes=[{"taxable": "1"}])
    assert methods == ["POST", "GET"]


# ---------------------------------------------------------------- AUTO_COUPLE_ENABLED


def _safe_pair(db):  # noqa: F811
    today = date.today()
    r = _receipt(db, today - timedelta(days=2), "Taxi Helsinki", 42.50)
    _line(db, 2600001, today - timedelta(days=1), 42.50)
    return r


def test_auto_couple_disabled_no_automatic_write(db, monkeypatch):  # noqa: F811
    from app.services import card_line_coupling
    from app.services.c47_sync import after_card_sync

    monkeypatch.delenv("AUTO_COUPLE_ENABLED", raising=False)
    r = _safe_pair(db)
    fake = FakeCouple()
    monkeypatch.setattr(card_line_coupling, "couple_receipt", fake)
    out = after_card_sync(db)
    assert fake.calls == []
    assert out["waiting_receipts"]["auto_coupled"] == []
    assert out["waiting_receipts"]["auto_couple_enabled"] is False
    db.refresh(r)
    assert r.bezala_transaction_id is None


def test_auto_couple_enabled_couples(db, monkeypatch):  # noqa: F811
    from app.services import card_line_coupling
    from app.services.c47_sync import after_card_sync

    monkeypatch.setenv("AUTO_COUPLE_ENABLED", "true")
    _safe_pair(db)
    fake = FakeCouple()
    monkeypatch.setattr(card_line_coupling, "couple_receipt", fake)
    after_card_sync(db)
    assert fake.calls == [("2600001", fake.calls[0][1])]


def test_subscriptions_no_autocouple_when_disabled(db, monkeypatch):  # noqa: F811
    from app.services.subscriptions import process_lines

    monkeypatch.delenv("AUTO_COUPLE_ENABLED", raising=False)
    assert process_lines(db)["coupled"] == []


def test_pipeline_auto_upload_off_without_flag(monkeypatch):
    from app.services import pipeline

    monkeypatch.delenv("AUTO_COUPLE_ENABLED", raising=False)
    bezala = MagicMock()
    status, tx, err = pipeline._attempt_bezala_upload(
        bezala, MagicMock(confidence=99, amount=1.0, date="2026-11-01"), MagicMock(),
        PDF, "k.pdf", auto_upload=True, confidence_threshold=0,
    )
    assert (status, tx) == ("pending", None)
    bezala.upload_receipt.assert_not_called()


# ---------------------------------------------------------------- rapport


def test_list_bot_bezala_writes(bound_db):  # noqa: F811
    from app.models import ProcessedMessage
    from app.services.bezala_writes_report import list_bot_bezala_writes

    now = datetime.utcnow()
    bound_db.add(ProcessedMessage(
        message_id="m-anthropic", status="saved", vendor="Anthropic", amount=112.95,
        currency="EUR", bezala_transaction_id="5579031", matched_at=now,
        ai_description_en="Anthropic API usage", bezala_upload_status="success"))
    bound_db.add(ProcessedMessage(
        message_id="m-old", status="saved", vendor="Old", amount=1.0, currency="EUR",
        bezala_transaction_id="1", matched_at=now - timedelta(days=30),
        processed_at=now - timedelta(days=30)))
    bound_db.commit()

    client = MagicMock()
    client.get_transaction = MagicMock(return_value={"id": 5579031, "state": "reviewing",
                                                     "description": "Anthropic API usage"})
    out = list_bot_bezala_writes(bound_db, 14, client=client)
    assert [i["transaction_id"] for i in out["items"]] == ["5579031"]
    item = out["items"][0]
    assert item["vendor"] == "Anthropic" and item["amount"] == 112.95
    assert item["bezala_now"]["status"] == "reviewing"
    assert out["left_draft"] == ["5579031"]
    client.put.assert_not_called()
    client.delete_transaction.assert_not_called()


# ================================================================ Del 1


def test_ai_tool_rule():
    from app.services.c50_accounts import is_ai_tool, suggest_account

    for v in ("Cursor", "Lovable", "Anthropic", "Claude.ai", "Bolt.new Pro", "StackBlitz",
              "OpenAI", "ChatGPT Plus", "Midjourney"):
        assert is_ai_tool(v), v
    assert not is_ai_tool("Bolt", "BOLT.EU/O/2611, TALLINN")  # Bolt-taxi
    s = suggest_account("Bolt.new", accounts=[{"id": 166648, "name": "AI työkalut"}],
                        transactions=[])
    assert s["name"] == "AI työkalut" and s["account_id"] == 166648 and not s["missing"]


def test_account_from_latest_own_expense_or_missing():
    from app.services.c50_accounts import suggest_account

    accounts = [{"id": 67101, "name": "Taksikulut"}, {"id": 67110, "name": "Muut matkakulut"}]
    txs = [
        {"id": 1, "date": "2026-09-01", "description": "Taxi Helsinki airport",
         "vat_lines": [{"expense_account_id": 67110, "vat_code_id": 1}]},
        {"id": 2, "date": "2026-10-20", "description": "Taxi Helsinki to office",
         "vat_lines": [{"expense_account_id": 67101, "vat_code_id": 864,
                        "cost_center_ids": [927151]}]},
    ]
    s = suggest_account("Taxi Helsinki", accounts=accounts, transactions=txs)
    assert (s["account_id"], s["name"], s["vat_code_id"], s["cost_center_ids"]) == \
        (67101, "Taksikulut", 864, [927151])
    m = suggest_account("Unknown Shop Oy", accounts=accounts, transactions=txs)
    assert m["missing"] and m["flag"] == "Account missing" and m["account_id"] is None


def test_mapper_uses_ai_account_for_bolt_new():
    from app.services.bezala_field_mapper import build_receipt_params

    accounts = [{"id": 166648, "name": "AI työkalut", "default_vat_id": 864},
                {"id": 67110, "name": "Muut matkakulut", "default_vat_id": 864}]
    p = build_receipt_params(file_name="bolt.pdf", sender="billing@stripe.com",
                             vendor="Bolt.new", category="annat", amount=23.30,
                             currency="EUR", receipt_date="2026-11-05", accounts=accounts,
                             cost_centers=[{"id": 927151, "name": "VIS128"}], vat_rates=[])
    assert p["vat_lines_attributes"][0]["expense_account_id"] == 166648


def test_description_date_from_corrected_date():
    from app.services.c50_accounts import fix_description_date

    assert fix_description_date("Taxi ride, Helsinki - 26 November 2018", "2026-11-03") == \
        "Taxi ride, Helsinki - 3 November 2026"
    ok = "Arlanda Express round trip ticket, Stockholm Arlanda Airport, 3 November 2026"
    assert fix_description_date(ok, "2026-11-03") == ok
    assert fix_description_date("Bolt.new Pro subscription - November 2018", "2026-11-05") == \
        "Bolt.new Pro subscription - November 2026"


def test_set_subscription_account_by_name(db):  # noqa: F811
    from app.models import Subscription
    from app.services.subscriptions import set_account

    s = Subscription(name="Bolt", vendor_pattern="bolt.new")
    db.add(s)
    db.commit()
    out = set_account(db, s.id, "AI työkalut",
                      accounts=[{"id": 166648, "name": "AI työkalut"}])
    db.refresh(s)
    assert s.account == 166648 and out is not None


# ================================================================ Del 2 + 3


def _apple(db, day, amount, desc="ChatGPT Plus", private=True, **kw):  # noqa: F811
    return _receipt(db, day, "Apple", amount, upload_source="gmail",
                    source_account="private" if private else "visma",
                    sender="Apple <no_reply@email.apple.com>", summary=desc, **kw)


def test_apple_receipt_matches_closed_apple_line(db):  # noqa: F811
    from app.models import Subscription
    from app.services.c50_handled import rerun

    d = date(2026, 10, 10)
    r = _apple(db, d, 22.99, waiting=False)
    r.waiting_status = "ignored_private_source"
    db.commit()
    _line(db, 2550000, d + timedelta(days=1), 22.99, desc="APPLE.COM/BILL, ITUNES.COM, IE",
          status="resolved")
    out = rerun(db, force=True, today=date(2026, 11, 22))
    db.refresh(r)
    assert r.waiting_status == "already_handled_in_bezala"
    assert r.ai_description_en == "ChatGPT Plus subscription - Oct 2026"
    assert out["before"]["ignored_private_source"] == 1
    assert out["counts"]["already_handled_in_bezala"] == 1
    sub = db.query(Subscription).filter(Subscription.vendor_pattern.contains("chatgpt")).one()
    assert sub.review_status == "suggested" and sub.active is False and sub.account is None


def test_apple_receipt_open_line_back_to_queue(db):  # noqa: F811
    from app.services import decision_queue as q
    from app.services.c50_handled import rerun

    d = date.today() - timedelta(days=20)
    r = _apple(db, d, 22.99, waiting=False)
    r.waiting_status = "ignored_private_source"
    db.commit()
    _line(db, 2550001, d + timedelta(days=2), 22.99, desc="APPLE.COM/BILL, CORK, IE")
    out = rerun(db, force=True)
    db.refresh(r)
    assert r.waiting_status == "waiting_for_transaction"
    assert out["counts"]["back_to_waiting"] == 1
    ids = [i["id"] for i in q.list_pending(db)["items"]]
    assert f"receipt:2550001:{r.message_id}" in ids


def test_private_apple_without_company_line_stays_ignored(db):  # noqa: F811
    from app.services.c50_handled import rerun

    r = _apple(db, date(2026, 9, 1), 6.99, desc="Crunchyroll", waiting=False)
    r.waiting_status = "ignored_private_source"
    db.commit()
    rerun(db, force=True)
    db.refresh(r)
    assert r.waiting_status == "ignored_private_source"


def test_arlanda_reread_receipt_already_handled(db):  # noqa: F811
    from app.services.c50_handled import rerun

    d = date(2026, 4, 30)
    manual = _receipt(db, d, "Arlanda Express", 640.0, "SEK", waiting=False,
                      upload_source="manual")
    manual.bezala_transaction_id = "5000001"
    manual.matched_at = datetime(2026, 5, 2)
    reread = _receipt(db, d, "Arlanda Express", 640.0, "SEK", upload_source="gmail",
                      since=datetime(2026, 11, 1))
    db.commit()
    out = rerun(db, force=True, today=date(2026, 11, 22))
    db.refresh(reread)
    assert reread.waiting_status == "already_handled_in_bezala"
    assert out["counts"]["already_handled_in_bezala"] == 1


def test_waiting_receipt_rules(db):  # noqa: F811
    from app.services.c50_handled import list_probably_handled
    from app.services.waiting_receipts import match_waiting_receipts

    today = date.today()
    old = _receipt(db, today - timedelta(days=20), "Kiosk", 7.90,
                   since=datetime.utcnow() - timedelta(days=20))
    young = _receipt(db, today - timedelta(days=3), "Kiosk", 4.50)
    handled = _receipt(db, today - timedelta(days=3), "Taxi Helsinki", 31.20)
    _line(db, 2550100, today - timedelta(days=2), 31.20, status="resolved")
    out = match_waiting_receipts(db, couple=FakeCouple())
    for r in (old, young, handled):
        db.refresh(r)
    assert old.waiting_status == "probably_handled_elsewhere"
    assert young.waiting_status == "waiting_for_transaction"
    assert handled.waiting_status == "already_handled_in_bezala"
    assert out["already_handled_in_bezala"] == 1 and out["probably_handled_elsewhere"] == 1
    assert [x["message_id"] for x in list_probably_handled(db)] == [old.message_id]
