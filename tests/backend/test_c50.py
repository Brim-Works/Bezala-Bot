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
    assert forms == [{"draft": "1", "state": "unapproved", "bill_line_id": "2500000"}]
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
