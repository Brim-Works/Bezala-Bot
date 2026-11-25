"""C60 — verifierad koppling, ifyllning av Bezala-utkast, rätt belopp och flygdatum.

Exemplen är de riktiga från prod 2026-11-25 (inga nycklar, bara id och belopp).
"""

from __future__ import annotations

import os
from datetime import date, datetime

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services import c51_vat as v  # noqa: E402
from app.services import c60_draft as c60  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _line, _receipt  # noqa: E402
from tests.backend.test_c50 import bound_db  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401

FINNAIR = "MIKKO KEINONEN: FINNAIR, VANTAA, FI 446.77 EUR"
SKANE = "MIKKO KEINONEN: SKANETRAFIKEN APP, KRISTIANSTAD, SE 14.01 EUR"
MOOVY = "MIKKO KEINONEN: MOOVY, TAMPERE, FI 37.49 EUR"
LOVABLE = "MIKKO KEINONEN: LOVABLE, STOCKHOLM, SE 100.0 EUR"

SEG_8LXVP2 = [
    {"flight_no": "AY 803", "origin": "HEL", "dest": "ARN", "date": "2026-11-24",
     "dep": "2026-11-24T08:00:00+02:00", "arr": "2026-11-24T08:05:00+01:00"},
    {"flight_no": "AY 814", "origin": "ARN", "dest": "HEL", "date": "2026-11-25",
     "dep": "2026-11-25T16:55:00+01:00", "arr": "2026-11-25T18:55:00+02:00"},
]
ACCOUNTS = [
    {"id": 67100, "name": "Matkaliput"},
    {"id": 67113, "name": "Paikoituskulut"},
    {"id": 166648, "name": "AI työkalut"},
    {"id": 99999, "name": "Tiliöimättömät ostolaskut"},
]


@pytest.fixture(autouse=True)
def _no_bezala(monkeypatch):
    """Inga riktiga Bezala-anrop, ingen väntan, inga timers i testerna."""
    from app.services import c50_accounts

    monkeypatch.setattr(c50_accounts, "reference", lambda: (ACCOUNTS, []))
    monkeypatch.setattr(c60, "schedule_verification", lambda *a, **k: None)
    monkeypatch.setenv("C60_COUPLE_PAUSE_SECONDS", "0")
    monkeypatch.delenv("BEZALA_DRAFT_FILL_ENABLED", raising=False)


class FakeBezala:
    """Bezala med ett utkast per id. PUT uppdaterar utkastet (fält, momsrad)."""

    def __init__(self, txs: dict):
        self.txs = {str(k): dict(t) for k, t in txs.items()}
        self.puts: list = []
        self.closed = False
        self.fail_get = False

    def get_transaction(self, tx_id):
        if self.fail_get:
            from app.services.bezala_client import BezalaError

            raise BezalaError("Bezala get_transaction: 503", status_code=503)
        return dict(self.txs[str(tx_id)])

    def _put_transaction_unchecked(self, tx_id, fields):
        self.puts.append((str(tx_id), dict(fields)))
        t = self.txs[str(tx_id)]
        for k, val in fields.items():
            if k == "vat_lines_attributes":
                old = (t.get("vat_lines") or [{}])[0]
                t["vat_lines"] = [{**old, **{kk: vv for kk, vv in val[0].items() if kk != "id"}}]
            else:
                t[k] = val
        return {"status_code": 200}

    def close(self):
        self.closed = True


def _draft(tx_id, **kw):
    base = {"id": int(tx_id), "state": "draft", "description": "", "date": None,
            "original_amount": "0", "original_amount_currency": None,
            "billed_amount": None, "billed_amount_currency": None,
            "connected_to_bill_line": False, "bill_id": None, "vat_lines": [],
            "approvers": None, "current_approvers_ids": []}
    base.update(kw)
    return base


def _finnair(db, tx="5594776", bid="2478468"):
    line = _line(db, bid, date(2026, 10, 29), 446.77, desc=FINNAIR)
    m = _receipt(db, date(2026, 11, 3), "Finnair", 446.77, waiting=False, category="Flyg",
                 bezala_transaction_id=tx, matched_at=datetime(2026, 11, 25, 22, 49),
                 bezala_upload_status="success", booking_ref="8IZ5RW",
                 ai_description_en="Finnair flight Helsinki-Stockholm round trip, 3 November 2026",
                 flight_segments=[
                     {"flight_no": "AY 823", "origin": "HEL", "dest": "ARN", "date": "2026-11-03"},
                     {"flight_no": "AY 818", "origin": "ARN", "dest": "HEL", "date": "2026-11-03"}],
                 bezala_payment_date="2026-10-29", bezala_payment_amount=446.77)
    line.bezala_transaction_id = tx
    line.status = "resolved"
    db.commit()
    return line, m


# ================================================================ Del 1: verifiering


def test_verify_successful_coupling_resolves(bound_db):
    line, m = _finnair(bound_db)
    client = FakeBezala({"5594776": _draft(
        "5594776", connected_to_bill_line=True, bill_id=339638, date="2026-10-29",
        original_amount="446.77", original_amount_currency="EUR",
        billed_amount="446.77", billed_amount_currency="EUR")})
    out = c60.verify_and_update(bound_db, "5594776", client=client)
    assert out["verified"] is True and out["problems"] == []
    bound_db.refresh(line)
    bound_db.refresh(m)
    assert line.status == "resolved" and m.bezala_upload_status == "success"


def test_verify_not_coupled_gives_unverified(bound_db):
    line, m = _finnair(bound_db)
    client = FakeBezala({"5594776": _draft("5594776", original_amount="418.00",
                                           original_amount_currency="EUR")})
    out = c60.verify_and_update(bound_db, "5594776", client=client)
    assert out["verified"] is False
    assert any("inte kopplat" in p for p in out["problems"])
    assert any("bill_id" in p for p in out["problems"])
    assert any("Datum saknas" in p for p in out["problems"])
    bound_db.refresh(line)
    bound_db.refresh(m)
    assert line.status == c60.STATUS_UNVERIFIED
    assert m.bezala_upload_status == c60.STATUS_UNVERIFIED
    assert client.puts == []  # flaggan av: inget skrivet


def test_verify_wrong_amount_even_when_connected(bound_db):
    line, _ = _finnair(bound_db)
    client = FakeBezala({"5594776": _draft(
        "5594776", connected_to_bill_line=True, bill_id=1, date="2026-10-29",
        original_amount="418.00", original_amount_currency="EUR")})
    out = c60.verify_and_update(bound_db, "5594776", client=client)
    assert out["verified"] is False
    assert any("418.00" in p and "446.77" in p for p in out["problems"])


def test_verify_bezala_error_keeps_status(bound_db):
    line, m = _finnair(bound_db)
    line.status = c60.STATUS_UNVERIFIED
    bound_db.commit()
    client = FakeBezala({"5594776": _draft("5594776")})
    client.fail_get = True
    from app.services.bezala_client import BezalaError

    with pytest.raises(BezalaError):
        c60.verify_and_update(bound_db, "5594776", client=client)
    bound_db.refresh(line)
    assert line.status == c60.STATUS_UNVERIFIED


def test_couple_receipt_answer_says_not_verified(bound_db, monkeypatch):
    from app.services import card_line_coupling as clc

    line = _line(bound_db, "2513377", date(2026, 11, 16), 540.79, desc=FINNAIR)
    m = _receipt(bound_db, date(2026, 11, 24), "Finnair", 540.79, waiting=False,
                 category="Flyg", flight_segments=SEG_8LXVP2, booking_ref="8LXVP2",
                 ai_description_en="Finnair flight Helsinki-Stockholm round trip, "
                                   "24-25 November 2026")
    calls = []

    def fake_couple(db, row, bid, attempts=2):
        calls.append(bid)
        row.bezala_transaction_id = "5594780"
        row.bezala_upload_status = "success"
        row.matched_at = datetime.utcnow()
        db.commit()

    monkeypatch.setattr(clc, "_couple_with_retry", fake_couple)
    scheduled = []
    monkeypatch.setattr(c60, "schedule_verification",
                        lambda tx, **k: scheduled.append((tx, k.get("bill_line_id"))))
    out = clc.couple_receipt(bound_db, "2513377", m.message_id)
    assert out["status"] == "utkast skapat, koppling inte verifierad än"
    assert out["coupling_status"] == c60.STATUS_UNVERIFIED
    assert out["draft_fill_enabled"] is False
    assert out["description_to_paste"].endswith("24-25 November 2026")
    bound_db.refresh(line)
    assert line.status == c60.STATUS_UNVERIFIED  # inte "resolved"
    assert scheduled == [("5594780", "2513377")]


def test_couple_retries_once_on_bezala_5xx_then_gives_up(bound_db, monkeypatch):
    from app import main
    from app.services import bezala_client, card_line_coupling as clc
    from app.services.bezala_client import BezalaError

    m = _receipt(bound_db, date(2026, 11, 5), "Skånetrafiken", 150.0, "SEK", waiting=False)
    attempts = []

    class Dummy:
        def close(self):
            pass

    def boom(**kw):
        attempts.append(1)
        raise BezalaError("Bezala attach_file: 502", status_code=502)

    monkeypatch.setattr(bezala_client, "BezalaClient", Dummy)
    monkeypatch.setattr(main, "_do_match_to_bezala", boom)
    with pytest.raises(clc.CouplingError):
        clc._couple_with_retry(bound_db, m, "2493711")
    assert len(attempts) == 2  # max 2 försök
    bound_db.refresh(m)
    assert m.bezala_upload_status == "failed"


def test_couple_no_retry_on_4xx(bound_db, monkeypatch):
    from app import main
    from app.services import bezala_client, card_line_coupling as clc
    from app.services.bezala_client import BezalaError

    m = _receipt(bound_db, date(2026, 11, 5), "Skånetrafiken", 150.0, "SEK", waiting=False)
    attempts = []

    class Dummy:
        def close(self):
            pass

    def boom(**kw):
        attempts.append(1)
        raise BezalaError("Bezala attach_file: 422", status_code=422)

    monkeypatch.setattr(bezala_client, "BezalaClient", Dummy)
    monkeypatch.setattr(main, "_do_match_to_bezala", boom)
    with pytest.raises(clc.CouplingError):
        clc._couple_with_retry(bound_db, m, "2493711")
    assert len(attempts) == 1


def test_couplings_run_one_at_a_time(bound_db, monkeypatch):
    import threading
    import time as _time

    from app.services import card_line_coupling as clc

    active, peak = [0], [0]

    def slow(db, row, bid, attempts=2):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        _time.sleep(0.05)
        active[0] -= 1
        raise clc.CouplingError("stop")

    monkeypatch.setattr(clc, "_couple_with_retry", slow)
    rows = [_receipt(bound_db, date(2026, 11, 5), "X", 1.0, waiting=False) for _ in range(4)]
    ids = [r.message_id for r in rows]

    def run(mid):
        try:
            clc.couple_receipt(bound_db, "1", mid)
        except Exception:  # noqa: BLE001
            pass

    ts = [threading.Thread(target=run, args=(i,)) for i in ids]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1


def test_reset_broken_card_lines(bound_db):
    line, m = _finnair(bound_db)
    out = c60.reset_broken_card_lines(bound_db)
    bound_db.refresh(line)
    bound_db.refresh(m)
    assert line.status == "missing" and line.bezala_transaction_id == "5594776"
    assert m.bezala_upload_status == c60.STATUS_UNVERIFIED
    assert any(i["bill_line_id"] == "2478468" and i["before"] == "resolved" for i in out)
    assert c60.reset_broken_card_lines(bound_db) is None  # bara en gång


def test_list_missing_shows_unverified(bound_db):
    from app.services import trip_completeness as tc

    line, _ = _finnair(bound_db)
    line.status = c60.STATUS_UNVERIFIED
    bound_db.commit()
    rows = tc.list_card_lines(bound_db)
    hit = next(r for r in rows if r["bill_line_id"] == "2478468")
    assert hit["status"] == c60.STATUS_UNVERIFIED
    assert hit["coupling"].startswith("Utkast skapat")
    assert hit["bezala_transaction_id"] == "5594776"


def test_sync_keeps_unverified_while_line_still_missing_in_bezala(bound_db):
    from app.services import trip_completeness as tc

    line, _ = _finnair(bound_db)
    line.status = c60.STATUS_UNVERIFIED
    bound_db.commit()
    tc.sync_card_lines(bound_db, [{"id": 2478468, "description": FINNAIR,
                                   "date": "2026-10-29"}])
    bound_db.refresh(line)
    assert line.status == c60.STATUS_UNVERIFIED
    tc.sync_card_lines(bound_db, [])  # Bezala har fått kvittot
    bound_db.refresh(line)
    assert line.status == "resolved"


# ================================================================ Del 2/3: ifyllning


def test_empty_draft_is_planned_and_filled(bound_db, monkeypatch):
    line, m = _finnair(bound_db)
    tx = _draft("5594776", original_amount="418.00", original_amount_currency="EUR")
    client = FakeBezala({"5594776": tx})
    dry = c60.fill_drafts(bound_db, ["5594776"], client=client)
    item = dry["items"][0]
    assert dry["apply"] is False and client.puts == []
    ch = item["changes"]
    assert ch["original_amount"] == "446.77" and ch["original_amount_currency"] == "EUR"
    assert ch["date"] == "2026-10-29"
    assert ch["description"] == "Finnair flight Helsinki-Stockholm round trip, 3 November 2026"
    assert ch["vat_lines_attributes"] == [{"expense_account_id": 67100, "tax_percentage": "0.0",
                                          "cost_center_ids": [927151]}]
    assert item["before"]["original_amount"] == "418.00"
    assert item["after"]["original_amount"] == "446.77"

    # apply utan flagga: inget skrivs.
    out = c60.fill_drafts(bound_db, ["5594776"], client=client, apply=True)
    assert client.puts == [] and "false" in out["note"]

    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    out = c60.fill_drafts(bound_db, ["5594776"], client=client, apply=True)
    assert len(client.puts) == 1
    sent = client.puts[0][1]
    assert "state" not in sent and "bill_id" not in sent
    assert out["items"][0]["result"] == "Skrivet och bekräftat"
    from app.models import BezalaWriteLog

    log = bound_db.query(BezalaWriteLog).filter_by(operation="draft_fill").one()
    assert log.state_before["values"]["original_amount"] == "418.00"
    assert log.state_after["values"]["original_amount"] == "446.77"
    assert c60.writes_for(bound_db, "5594776")[0]["after"]["original_amount"] == "446.77"


def test_mikkos_own_text_and_account_are_kept(bound_db):
    line, m = _finnair(bound_db)
    tx = _draft("5594776", description="Mikkos egen text", date="2026-10-29",
                original_amount="446.77", original_amount_currency="EUR",
                vat_lines=[{"id": 7, "expense_account_id": 67102, "tax_percentage": "0.14",
                            "cost_center_ids": [927151]}])
    plan = c60.plan_fill(bound_db, m, line, tx, accounts=ACCOUNTS)
    assert "description" not in plan["changes"]
    assert "vat_lines_attributes" not in plan["changes"]
    assert any("behålls" in s for s in plan["skipped"])


def test_default_account_line_is_filled_with_line_id(bound_db):
    line, m = _finnair(bound_db)
    tx = _draft("5594776", vat_lines=[{"id": 7, "expense_account_id": 99999,
                                       "tax_percentage": None}])
    plan = c60.plan_fill(bound_db, m, line, tx, accounts=ACCOUNTS)
    assert plan["changes"]["vat_lines_attributes"][0]["id"] == 7
    assert plan["changes"]["vat_lines_attributes"][0]["expense_account_id"] == 67100


@pytest.mark.parametrize("state", ["reviewing", "accounted", "queue", None])
def test_non_draft_is_refused(bound_db, monkeypatch, state):
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    line, m = _finnair(bound_db)
    client = FakeBezala({"5594776": _draft("5594776", state=state)})
    out = c60.fill_drafts(bound_db, ["5594776"], client=client, apply=True)
    assert client.puts == []
    assert out["items"][0]["changes"] == {}
    assert "Inte ett utkast" in out["items"][0]["skipped"][0]


def test_cross_currency_amount_uses_receipt_when_rate_matches(bound_db):
    from app.models import CurrencyRate

    line = _line(bound_db, "2493711", date(2026, 11, 5), 14.01, desc=SKANE)
    m = _receipt(bound_db, date(2026, 11, 5), "Skånetrafiken", 150.0, "SEK", waiting=False,
                 category="Kollektivtrafik", bezala_transaction_id="5594772",
                 ai_description_en="Skånetrafiken train ticket CPH Airport-Malmö C, "
                                   "5 November 2026")
    line.bezala_transaction_id = "5594772"
    bound_db.add(CurrencyRate(date="2026-11-05", from_currency="SEK", to_currency="EUR",
                              rate=0.0934))
    bound_db.commit()
    plan = c60.plan_fill(bound_db, m, line, _draft("5594772", date="2026-11-05"),
                         accounts=ACCOUNTS)
    assert plan["changes"]["original_amount"] == "150.00"
    assert plan["changes"]["original_amount_currency"] == "SEK"
    assert plan["changes"]["vat_lines_attributes"][0]["tax_percentage"] == "0.0"


def test_amount_never_guessed_when_receipt_differs(bound_db):
    line = _line(bound_db, "1", date(2026, 11, 5), 100.0, desc=FINNAIR)
    m = _receipt(bound_db, date(2026, 11, 5), "Finnair", 90.0, waiting=False, category="Flyg",
                 bezala_transaction_id="9")
    plan = c60.plan_fill(bound_db, m, line, _draft("9"), accounts=ACCOUNTS)
    assert "original_amount" not in plan["changes"]
    assert any("ändras inte" in s for s in plan["skipped"])


def test_vat_parking_moovy_is_25_5(bound_db):
    line = _line(bound_db, "2512222", date(2026, 10, 22), 37.49, desc=MOOVY)
    m = _receipt(bound_db, date(2026, 10, 22), "Moovy", 37.49, waiting=False,
                 category="Parkering", bezala_transaction_id="5594768")
    plan = c60.plan_fill(bound_db, m, line, _draft("5594768"), accounts=ACCOUNTS)
    assert plan["vat"] == v.VAT_25_5
    assert plan["changes"]["vat_lines_attributes"][0]["tax_percentage"] == "0.255"
    assert plan["changes"]["vat_lines_attributes"][0]["expense_account_id"] == 67113


def test_vat_moovy_card_slip_no_longer_account_default():
    r = v.suggest_vat(category="Parkering", card_description=MOOVY, card_amount=37.49,
                      texts=["Moovy"], vat_info={"lines": [], "document": "card_slip"})
    assert r["vat"] == v.VAT_25_5 and r["rule"].startswith("7b")


def test_vat_flight_international_from_segments_is_0():
    r = v.suggest_vat(category="Flyg", card_description=FINNAIR, card_amount=446.77,
                      texts=["Finnair"], flight_segments=SEG_8LXVP2)
    assert r["vat"] == v.VAT_0 and v.FLAG_FLIGHT_ROUTE not in r["flags"]


def test_vat_subscription_non_eu_is_not_written(bound_db):
    line = _line(bound_db, "3", date(2026, 11, 5), 100.0,
                 desc="MIKKO KEINONEN: LOVABLE, SAN FRANCISCO, US 100.0 EUR")
    m = _receipt(bound_db, date(2026, 11, 5), "Lovable", 100.0, waiting=False,
                 category="AI-verktyg", bezala_transaction_id="5594767")
    plan = c60.plan_fill(bound_db, m, line, _draft("5594767"), accounts=ACCOUNTS)
    assert plan["vat"] == v.VAT_NON_EU
    vl = plan["changes"]["vat_lines_attributes"][0]
    assert vl["expense_account_id"] == 166648 and "tax_percentage" not in vl
    assert any("Purchases Abroad (Non-EU)" in s for s in plan["skipped"])


def test_change_fee_uses_booking_segments(bound_db):
    _receipt(bound_db, date(2026, 11, 24), "Finnair", 540.79, waiting=False, category="Flyg",
             booking_ref="8LXVP2", flight_segments=SEG_8LXVP2)
    fee = _receipt(bound_db, date(2026, 11, 22), "Finnair", 32.92, waiting=False,
                   category="Flyg", booking_ref="8LXVP2", kind="change_fee",
                   ai_description_en="Finnair change fee - flight")
    line = _line(bound_db, "5", date(2026, 11, 22), 32.92,
                 desc="MIKKO KEINONEN: FINNAIR, VANTAA, FI 32.92 EUR")
    from app.services.card_line_coupling import _bezala_fill

    fill = _bezala_fill(fee, line)
    assert fill["vat"] == v.VAT_0
    assert v.FLAG_FLIGHT_ROUTE not in fill["flags"]
    assert fill["description"] == "Finnair change fee - flight, 24-25 November 2026"


# ================================================================ Del 4: flygdatum


def test_flight_date_from_return_flight_not_purchase_date(bound_db):
    """Prod: 8LXVP2 köpt 16 nov, flyg 24-25 nov gav "24-16 November 2026"."""
    line = _line(bound_db, "2513377", date(2026, 11, 16), 540.79, desc=FINNAIR)
    m = _receipt(bound_db, date(2026, 11, 24), "Finnair", 540.79, waiting=False,
                 category="Flyg", flight_segments=SEG_8LXVP2, booking_ref="8LXVP2",
                 ai_description_en="Finnair flight Helsinki-Stockholm round trip, "
                                   "24-25 November 2026")
    from app.services.card_line_coupling import _description_to_paste

    assert _description_to_paste(m, line) == \
        "Finnair flight Helsinki-Stockholm round trip, 24-25 November 2026"


@pytest.mark.parametrize("desc,segs,want", [
    ("Finnair flight Helsinki-Oslo, 16 November 2026",
     [{"date": "2026-11-27"}], "Finnair flight Helsinki-Oslo, 27 November 2026"),
    ("Finnair flight Oslo-Helsinki, 16 November 2026",
     [{"date": "2026-11-28"}], "Finnair flight Oslo-Helsinki, 28 November 2026"),
    ("Finnair flight Helsinki-Stockholm round trip, 24-16 November 2026",
     [{"date": "2026-11-24"}, {"date": "2026-11-25"}],
     "Finnair flight Helsinki-Stockholm round trip, 24-25 November 2026"),
    ("Finnair flight Helsinki-Copenhagen round trip",
     [{"date": "2026-11-10"}, {"date": "2026-11-11"}],
     "Finnair flight Helsinki-Copenhagen round trip, 10-11 November 2026"),
    ("Finnair flight, November 2026", [{"date": "2026-11-30"}, {"date": "2026-12-02"}],
     "Finnair flight, 30 November - 2 December 2026"),
])
def test_flight_description(desc, segs, want):
    assert c60.flight_description(desc, segs) == want


def test_non_flight_keeps_c50_date_rule(bound_db):
    line = _line(bound_db, "7", date(2026, 11, 3), 10.0)
    m = _receipt(bound_db, date(2026, 11, 3), "Taxi", 10.0, waiting=False, category="Taxi",
                 ai_description_en="Taxi ride, 26 November 2018")
    from app.services.card_line_coupling import _description_to_paste

    assert _description_to_paste(m, line) == "Taxi ride, 3 November 2026"


# ================================================================ flaggor


def test_all_write_flags_default_false(monkeypatch):
    from app.services import bezala_write_guard as guard

    for name in ("AUTO_COUPLE_ENABLED", "BEZALA_METADATA_PUT_ENABLED", "NETVISOR_ENABLED",
                 "BEZALA_DRAFT_FILL_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert guard.env_auto_couple_enabled() is False
    assert guard.metadata_put_enabled() is False
    assert c60.draft_fill_enabled() is False


def test_guard_refuses_state_even_in_draft_fill():
    from app.services import bezala_write_guard as guard

    with pytest.raises(guard.BezalaWriteBlocked):
        guard.check_fields({"state": "draft"}, guard.DRAFT_FILL_FIELDS)
    with pytest.raises(guard.BezalaWriteBlocked):
        guard.check_fields({"bill_line_id": "1"}, guard.DRAFT_FILL_FIELDS)


# ================================================================ PostgreSQL


def test_postgres_unverified_status_and_fill_log(pg_url, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base
    from app.models import BezalaWriteLog
    from app.services import bezala_write_guard as guard

    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    @contextmanager
    def _session(_db=None):
        yield s

    monkeypatch.setattr(guard, "_session", _session)
    try:
        line, m = _finnair(s)
        assert c60.reset_broken_card_lines(s)
        s.refresh(line)
        s.refresh(m)
        assert line.status == "missing" and m.bezala_upload_status == "coupling_unverified"
        monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
        client = FakeBezala({"5594776": _draft("5594776", original_amount="418.00",
                                               original_amount_currency="EUR")})
        out = c60.verify_and_update(s, "5594776", client=client)
        assert out["fill"]["result"] == "Skrivet och bekräftat"
        assert out["verified"] is False  # Bezala har inte kopplat än
        s.refresh(line)
        assert line.status == "coupling_unverified"
        log = s.query(BezalaWriteLog).filter_by(operation="draft_fill").one()
        assert log.state_after["values"]["original_amount"] == "446.77"
    finally:
        s.close()
        engine.dispose()
