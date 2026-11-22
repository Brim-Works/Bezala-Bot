"""C47 — mobilkvitton, +kvitto-mail, svep-kö, prenumerationer, återbetalningar."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from tests.backend.test_c42 import _trip, db  # noqa: E402,F401

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 9000
PDF = b"%PDF-1.4 fake"
_seq = iter(range(1, 100_000))


# ---------------------------------------------------------------- helpers


def _line(s, bid, day, amount, currency="EUR", desc="TAXI HELSINKI, HELSINKI, FI",
          status="missing"):
    from app.models import CardLine
    from app.services.trip_completeness import _vendor_from_description

    c = CardLine(bill_line_id=str(bid), txn_date=day, description=desc,
                 vendor=_vendor_from_description(desc), amount=amount, currency=currency,
                 status=status)
    s.add(c)
    s.commit()
    return c


def _receipt(s, day, vendor, amount, currency="EUR", *, waiting=True, since=None, **kw):
    from app.models import ProcessedMessage

    m = ProcessedMessage(
        message_id=kw.pop("message_id", f"c47-{next(_seq)}"), status="saved", vendor=vendor,
        amount=amount, currency=currency, receipt_date=day.isoformat() if day else None,
        received_at=datetime.combine(day or date.today(), datetime.min.time()),
        drive_file_id="drv-1", file_name="k.jpg", drive_link="https://drive/x",
        upload_source=kw.pop("upload_source", "mobile"),
        waiting_status="waiting_for_transaction" if waiting else None,
        waiting_since=since or datetime.utcnow(), **kw,
    )
    s.add(m)
    s.commit()
    return m


def _rate(s, day, frm, to, rate):
    from app.models import CurrencyRate

    s.add(CurrencyRate(date=day.isoformat(), from_currency=frm, to_currency=to, rate=rate))
    s.commit()


class FakeCouple:
    def __init__(self):
        self.calls = []

    def __call__(self, db, bid, mid):
        from app.models import CardLine, ProcessedMessage

        self.calls.append((bid, mid))
        m = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == mid).first()
        m.bezala_transaction_id = f"tx-{bid}"
        m.matched_at = datetime.utcnow()
        line = db.query(CardLine).filter(CardLine.bill_line_id == bid).first()
        line.status = "resolved"
        line.bezala_transaction_id = m.bezala_transaction_id
        line.note = f"Coupled via Bezala Bot to {mid}"
        if m.waiting_status in ("waiting_for_transaction", "no_transaction"):
            m.waiting_status = "coupled"
        db.commit()
        return {"bill_line_id": bid, "message_id": mid}


# ---------------------------------------------------------------- Del 1.6


def test_receipt_before_card_line_is_coupled_when_line_arrives(db):  # noqa: F811
    from app.services.waiting_receipts import match_waiting_receipts

    today = date.today()
    r = _receipt(db, today - timedelta(days=3), "Taxi Helsinki", 42.50)
    couple = FakeCouple()
    # Ingen kortrad ännu → väntar, inget fel.
    out = match_waiting_receipts(db, couple=couple)
    assert couple.calls == [] and out["waiting"] == 1
    db.refresh(r)
    assert r.waiting_status == "waiting_for_transaction"
    # Kortraden kommer två dagar senare.
    _line(db, 2600001, today - timedelta(days=1), 42.50)
    out = match_waiting_receipts(db, couple=couple)
    assert couple.calls == [("2600001", r.message_id)]
    db.refresh(r)
    assert r.waiting_status == "coupled"


def test_sek_receipt_matches_eur_card_line(db):  # noqa: F811
    from app.services.waiting_receipts import match_waiting_receipts

    day = date.today() - timedelta(days=2)
    _rate(db, day, "SEK", "EUR", 0.0870)
    r = _receipt(db, day, "Taxi Göteborg", 450.0, "SEK")
    _line(db, 2600002, day, 39.86, "EUR", desc="TAXI GOTEBORG, GOTEBORG, SE 39.86 EUR")
    couple = FakeCouple()
    out = match_waiting_receipts(db, couple=couple)
    assert couple.calls == [("2600002", r.message_id)], out


def test_no_match_after_14_days_is_flagged(db):  # noqa: F811
    from app.services import decision_queue as q
    from app.services.trip_completeness import list_missing
    from app.services.waiting_receipts import match_waiting_receipts

    old = datetime.utcnow() - timedelta(days=15)
    r = _receipt(db, date.today() - timedelta(days=15), "Kiosk", 7.90, since=old)
    match_waiting_receipts(db, couple=FakeCouple())
    db.refresh(r)
    # C50 Del 3 — ingen träff efter 14 dagar → granskningslista, inte kön.
    assert r.waiting_status == "probably_handled_elsewhere"
    assert f"notxn:{r.message_id}" not in [i["id"] for i in q.list_pending(db)["items"]]
    # Äldre flaggade (no_transaction) syns fortfarande i kön.
    r.waiting_status = "no_transaction"
    db.commit()
    lm = list_missing(db, sync=False)
    assert [x["message_id"] for x in lm["receipts_without_transaction"]] == [r.message_id]
    items = q.list_pending(db)["items"]
    assert f"notxn:{r.message_id}" in [i["id"] for i in items]
    # Mikko: privat / annat kort → borta ur kön, går att ångra.
    q.decide(db, f"notxn:{r.message_id}", "yes")
    db.refresh(r)
    assert r.waiting_status == "private"
    q.undo_last(db)
    db.refresh(r)
    assert r.waiting_status == "no_transaction"


def test_uncertain_match_goes_to_queue_not_coupled(db):  # noqa: F811
    from app.services import decision_queue as q
    from app.services.waiting_receipts import match_waiting_receipts

    day = date.today() - timedelta(days=2)
    r = _receipt(db, day, "Taxi Helsinki", 42.50)
    # Två identiska kortrader → inte entydigt → ingen auto-koppling.
    _line(db, 2600010, day, 42.50)
    _line(db, 2600011, day, 42.50)
    couple = FakeCouple()
    out = match_waiting_receipts(db, couple=couple)
    assert couple.calls == [] and out["uncertain"] >= 1
    ids = [i["id"] for i in q.list_pending(db)["items"] if i["kind"] == "receipt"]
    assert any(r.message_id in i for i in ids)


# ---------------------------------------------------------------- Del 1 + 6: ingest + datum


def test_ingest_mobile_receipt_waits_and_fixes_year(db):  # noqa: F811
    from app.services.receipt_inbox import InboxError, ingest_receipt_file

    analyzer = MagicMock(enabled=True)
    analyzer.analyze.return_value = SimpleNamespace(
        filename="20181126 Emmanuel Taxi.pdf", vendor="Emmanuel Taxi", amount=23.0,
        currency="EUR", date="2018-11-26", category="Taxi", summary="Taxi",
        description_en="Taxi in Helsinki", confidence=90,
    )
    drive = MagicMock()
    drive.upload_attachment.return_value = SimpleNamespace(file_id="f1", web_view_link="l")
    res = ingest_receipt_file(db, JPEG, filename="IMG_1.jpg", source="mobile",
                              sender="mobile@bezala-bot", analyzer=analyzer, drive=drive,
                              received_at=datetime(2026, 11, 27, 8, 0))
    row = res.row
    assert row.upload_source == "mobile" and row.waiting_status == "waiting_for_transaction"
    assert row.receipt_date == "2026-11-26" and row.review_flag == "date_corrected"
    assert row.file_name.endswith(".jpg")
    with pytest.raises(InboxError):
        ingest_receipt_file(db, b"hello", filename="x.txt", source="mobile",
                            sender="m", analyzer=analyzer, drive=drive)


def test_date_rule():
    from app.services.receipt_dates import sanitize_receipt_date as fix

    recv = datetime(2026, 11, 20)
    # Emmanuel Taxi: 2026-11-26 vore efter mailet → året innan.
    assert fix("2018-11-26", recv) == ("2025-11-26", True)
    assert fix("2018-11-16", recv) == ("2026-11-16", True)
    assert fix("2026-10-01", recv) == ("2026-10-01", False)
    # Liv Taxi: mer än 13 månader före mailet → mottagningsåret.
    assert fix("2025-10-01", recv) == ("2026-10-01", True)
    assert fix("2025-10-25", recv) == ("2025-10-25", False)
    assert fix("2024-10-01", recv) == ("2026-10-01", True)
    # Kvitto efter mailet → mottagningsårets år (året innan om det behövs).
    assert fix("2027-01-05", datetime(2026, 1, 10)) == ("2026-01-05", True)
    # Flyg dateras på resdagen → får ligga efter mailet.
    assert fix("2027-03-10", recv, "Flyg") == ("2027-03-10", False)


# ---------------------------------------------------------------- Del 2: +kvitto


class FakeGmail:
    def __init__(self, mails):
        self.mails = mails
        self.done = []

    def get_profile_email(self):
        return "anna.svensson@work.example"

    def list_candidate_message_ids(self, query, max_results=25):
        self.query = query
        return list(self.mails)

    def fetch_headers(self, mid, names):
        return self.mails[mid]["headers"]

    def fetch_message(self, mid):
        m = self.mails[mid]
        return SimpleNamespace(message_id=mid, thread_id="t", sender=m["headers"]["from"][0],
                               subject="Kvitto", received_at=datetime(2026, 11, 20),
                               attachments=m["atts"])

    def mark_done(self, mid):
        self.done.append(mid)


def _att(name, data):
    return SimpleNamespace(filename=name, mime_type="image/jpeg", data=data)


def test_kvitto_mail_only_from_own_addresses(db, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    from app.models import ProcessedMessage
    from app.services import kvitto_mail

    @contextmanager
    def scope():
        yield db
        db.commit()

    monkeypatch.setattr(kvitto_mail, "session_scope", scope)
    monkeypatch.setattr(kvitto_mail, "_private_profile_email", lambda: "anna.private@gmail.com")
    gmail = FakeGmail({
        "m1": {"headers": {"from": ["Anna <annasvensson@work.example>"],
                           "to": ["annasvensson+kvitto@work.example"]},
               "atts": [_att("a.jpg", JPEG), _att("b.jpg", JPEG), _att("logo.png", b"\x89PNG")]},
        "m2": {"headers": {"from": ["Someone <evil@spam.example>"],
                           "to": ["anna.svensson+kvitto@work.example"]},
               "atts": [_att("c.jpg", JPEG)]},
        "m3": {"headers": {"from": ["A <annaprivate@gmail.com>"],
                           "delivered-to": ["anna.svensson+kvitto@work.example"]},
               "atts": [_att("d.jpg", JPEG)]},
    })
    analyzer = MagicMock(enabled=False)
    drive = MagicMock()
    drive.upload_attachment.return_value = SimpleNamespace(file_id="f", web_view_link="l")
    out = kvitto_mail.run_kvitto_pass(gmail, drive=drive, analyzer=analyzer)
    assert out["saved"] == 3 and out["rejected"] == 1, out
    ids = sorted(m.message_id for m in db.query(ProcessedMessage).all())
    assert ids == ["m1", "m1#2", "m3"]
    assert {m.upload_source for m in db.query(ProcessedMessage).all()} == {"mail_kvitto"}
    assert "anna.svensson+kvitto@work.example" in gmail.query
    # Idempotent.
    out = kvitto_mail.run_kvitto_pass(gmail, drive=drive, analyzer=analyzer)
    assert out["saved"] == 0


# ---------------------------------------------------------------- Del 3: kön


def test_queue_receipt_yes_no_later_undo(db, monkeypatch):  # noqa: F811
    from app.services import decision_queue as q

    day = date.today() - timedelta(days=6)
    r = _receipt(db, day, "Taxi Helsinki", 42.50)
    _line(db, 2600020, day - timedelta(days=1), 42.50)
    items = q.list_pending(db)["items"]
    item = next(i for i in items if i["kind"] == "receipt")
    assert item["id"] == f"receipt:2600020:{r.message_id}"
    assert item["yes_label"] and item["no_label"]
    # Senare → sist i kön.
    _trip(db, day, day + timedelta(days=1))
    q.decide(db, item["id"], "later")
    items = q.list_pending(db)["items"]
    assert items[-1]["id"] == item["id"] and items[-1]["later"]
    # Nej → föreslås aldrig igen.
    q.decide(db, item["id"], "no")
    assert item["id"] not in [i["id"] for i in q.list_pending(db)["items"]]
    q.undo_last(db)
    assert item["id"] in [i["id"] for i in q.list_pending(db)["items"]]
    # Ja → kopplas.
    couple = FakeCouple()
    q.decide(db, item["id"], "yes", couple=couple)
    assert couple.calls == [("2600020", r.message_id)]


def test_queue_trip_yes_no_and_undo(db):  # noqa: F811
    from app.models import CalendarTrip
    from app.services import decision_queue as q

    t1 = _trip(db, date(2026, 10, 1), date(2026, 10, 2))
    t2 = _trip(db, date(2026, 10, 5), date(2026, 10, 6))
    ids = [i["id"] for i in q.list_pending(db)["items"]]
    assert f"trip:{t1.id}" in ids and f"trip:{t2.id}" in ids
    q.decide(db, f"trip:{t1.id}", "yes")
    q.decide(db, f"trip:{t2.id}", "no")
    assert db.get(CalendarTrip, t1.id).status == "approved"
    assert db.get(CalendarTrip, t2.id).status == "rejected"
    q.undo_last(db)
    assert db.get(CalendarTrip, t2.id).status == "suggested"
    with pytest.raises(q.QueueError):
        q.decide(db, f"trip:{t1.id}", "maybe")


# ---------------------------------------------------------------- Del 4


def test_seeded_subscriptions_are_inactive_until_confirmed(db):  # noqa: F811
    from app.models import BezalaVendorMapping, Subscription
    from app.services import decision_queue as q
    from app.services.subscriptions import seed_subscriptions

    db.add(BezalaVendorMapping(vendor_pattern="cursor", bezala_account_id=6820, vat_rate=0))
    db.commit()
    assert seed_subscriptions(db) == 4
    assert seed_subscriptions(db) == 0
    subs = db.query(Subscription).all()
    assert {s.name for s in subs} == {"Cursor", "Lovable", "Anthropic (Claude)",
                                      "Bolt/StackBlitz"}
    assert not any(s.active for s in subs)
    cursor = next(s for s in subs if s.name == "Cursor")
    _line(db, 2600030, date(2026, 10, 3), 20.0, desc="CURSOR, NEW YORK, US 20.00 EUR",
          status="resolved")
    item = f"sub:{cursor.id}"
    card = next(i for i in q.list_pending(db)["items"] if i["id"] == item)
    # C48: kontot gissas aldrig (vendor-mappningen visas bara som förslag)
    # och utan konto kan prenumerationen inte aktiveras.
    assert card["warning"] == "Account missing - set it first"
    assert card["subscription"]["suggested_account"] == 6820
    with pytest.raises(Exception, match="Account missing"):
        q.decide(db, item, "yes")
    db.refresh(cursor)
    assert not cursor.active and cursor.account is None
    from app.services.subscriptions import set_account

    set_account(db, cursor.id, 6820)
    q.decide(db, item, "yes")
    db.refresh(cursor)
    assert cursor.active and cursor.account == 6820 and cursor.expected_amount == 20.0


def test_subscription_autocouple_with_description_and_flags(db):  # noqa: F811
    from app.models import ProcessedMessage, Subscription
    from app.services import decision_queue as q
    from app.services.subscriptions import process_lines

    sub = Subscription(name="Cursor", vendor_pattern="cursor|anysphere", active=True,
                       review_status="confirmed", expected_amount=20.0, currency="EUR",
                       description_template="Cursor subscription - {month}")
    db.add(sub)
    db.commit()
    r = _receipt(db, date(2026, 11, 3), "Cursor", 20.0, waiting=False, sender="cursor.com")
    _line(db, 2600040, date(2026, 11, 3), 20.0, desc="CURSOR, NEW YORK, US 20.00 EUR")
    _line(db, 2600041, date(2026, 11, 4), 40.0, desc="CURSOR USAGE, NEW YORK, US 40.00 EUR")
    _line(db, 2600042, date.today() - timedelta(days=8), 20.0,
          desc="ANYSPHERE CURSOR, NEW YORK, US 20.00 EUR")
    couple = FakeCouple()
    out = process_lines(db, couple=couple)
    assert couple.calls == [("2600040", r.message_id)], out
    db.refresh(r)
    assert r.ai_description_en == "Cursor subscription - Nov 2026"
    ids = [i["id"] for i in q.list_pending(db)["items"]]
    assert "subdev:2600041" in ids  # +100 %
    assert "subnr:2600042" in ids   # inget kvitto efter 5 dagar


def test_detect_subscription_two_months_similar_amount(db):  # noqa: F811
    from app.services.subscriptions import detect_subscriptions

    # C48: bara kortrader på företagskortet räknas.
    _line(db, 2600070, date(2026, 9, 5), 10.0, desc="NOTION LABS, SAN FRANCISCO, US 10.00 EUR",
          status="resolved")
    _line(db, 2600071, date(2026, 10, 5), 10.5, desc="NOTION LABS, SAN FRANCISCO, US 10.50 EUR")
    _receipt(db, date(2026, 9, 21), "Flytoget", 24.0, waiting=False, category="Kollektivtrafik")
    _receipt(db, date(2026, 10, 21), "Flytoget", 24.0, waiting=False, category="Kollektivtrafik")
    _line(db, 2600072, date(2026, 9, 21), 24.0, desc="FLYTOGET AS, OSLO, NO 24.00 EUR",
          status="resolved")
    _line(db, 2600073, date(2026, 10, 21), 24.0, desc="FLYTOGET AS, OSLO, NO 24.00 EUR",
          status="resolved")
    _receipt(db, date(2026, 9, 11), "Crunchyroll", 6.99, waiting=False, category="Mjukvara")
    _receipt(db, date(2026, 10, 11), "Crunchyroll", 6.99, waiting=False, category="Mjukvara")
    new = detect_subscriptions(db)
    assert [s.vendor_pattern for s in new] == ["notion"]
    assert not new[0].active and new[0].review_status == "suggested"
    assert detect_subscriptions(db) == []


# ---------------------------------------------------------------- Del 5


def test_expected_refund_matches_negative_line_and_overdue(db):  # noqa: F811
    from app.models import ExpectedRefund
    from app.services import decision_queue as q
    from app.services.refunds import expect_refund, match_refunds, seed_finnair_refund

    out = seed_finnair_refund(db)
    assert out["bill_line_id"] == "2513379" and out["amount"] == 134.48
    assert seed_finnair_refund(db) is None
    _line(db, 2600050, date.today(), -134.48, desc="FINNAIR, VANTAA, FI -134.48 EUR")
    _line(db, 2600051, date.today(), -50.00, desc="FINNAIR, VANTAA, FI -50.00 EUR")
    m = match_refunds(db)
    assert [x["refund_bill_line_id"] for x in m] == ["2600050"]
    r = db.query(ExpectedRefund).first()
    assert r.status == "matched"
    # Ny väntad återbetalning, 31 dagar gammal → kön + list_missing.
    _line(db, 2600052, date(2026, 9, 1), 80.0, desc="SAS, STOCKHOLM, SE 80.00 EUR")
    res = expect_refund(db, "2600052", "Cancelled")
    row = db.get(ExpectedRefund, res["id"])
    row.created_at = datetime.utcnow() - timedelta(days=31)
    db.commit()
    assert f"refund:{row.id}" in [i["id"] for i in q.list_pending(db)["items"]]


def test_duplicates_only_when_fewer_receipts_than_lines(db):  # noqa: F811
    from app.services.refunds import duplicate_charges

    d = date(2026, 10, 21)
    desc = "FLYTOGET AS, OSLO, NO 21.00 EUR"
    _line(db, 2600060, d, 21.0, desc=desc, status="resolved")
    _line(db, 2600061, d, 21.0, desc=desc, status="resolved")
    _receipt(db, d, "Flytoget", 21.0, waiting=False)
    _receipt(db, d, "Flytoget", 21.0, waiting=False)
    assert duplicate_charges(db) == []
    d2 = date(2026, 10, 22)
    _line(db, 2600062, d2, 21.0, desc=desc)
    _line(db, 2600063, d2, 21.0, desc=desc)
    _receipt(db, d2, "Flytoget", 21.0, waiting=False)
    flags = duplicate_charges(db)
    assert len(flags) == 1 and flags[0]["card_lines"] == 2 and flags[0]["receipts"] == 1


# ---------------------------------------------------------------- API


@pytest.fixture()
def client(db):  # noqa: F811
    from fastapi.testclient import TestClient

    from app import main
    from app.routers import c47

    def _get_db():
        yield db

    # Andra testmoduler byter ut app.db.get_db — använd routerns egen referens.
    main.app.dependency_overrides[c47.get_db] = _get_db
    main.app.dependency_overrides[c47.require_auth] = lambda: None
    main.app.dependency_overrides[main.require_auth] = lambda: None
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


def test_mobile_upload_endpoint_waiting_then_suggested(client, db, monkeypatch):  # noqa: F811
    from app.services import receipt_analyzer, receipt_inbox

    day = date.today()
    analyzer = MagicMock(enabled=True)
    analyzer.analyze.return_value = SimpleNamespace(
        filename="x.pdf", vendor="Taxi Helsinki", amount=42.5, currency="EUR",
        date=day.isoformat(), category="Taxi", summary="", description_en="Taxi",
        confidence=95,
    )
    monkeypatch.setattr(receipt_analyzer, "ReceiptAnalyzer", lambda: analyzer)
    drive = MagicMock()
    drive.upload_attachment.return_value = SimpleNamespace(file_id="f", web_view_link="l")
    import app.services.drive_client as dc

    monkeypatch.setattr(dc, "DriveClient", lambda: drive)
    r = client.post("/api/m/receipts", files={"file": ("IMG.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "waiting" and body["receipt"]["source"] == "mobile"
    mid = body["receipt"]["message_id"]
    _line(db, 2600070, day, 42.5)
    body = client.get(f"/api/m/receipts/{mid}").json()
    assert body["status"] == "suggested"
    assert body["suggestion"]["bill_line_id"] == "2600070"
    lines = client.get("/api/m/card-lines", params={"message_id": mid}).json()["card_lines"]
    assert lines[0]["bill_line_id"] == "2600070"
    # Spara utan koppling → förslaget avvisas, kvittot väntar kvar.
    body = client.post(f"/api/m/receipts/{mid}/keep", json={"bill_line_id": "2600070"}).json()
    assert body["status"] == "waiting" and body["suggestion"] is None
    assert receipt_inbox.SOURCE_MOBILE == "mobile"
    q = client.get("/api/queue").json()
    assert "items" in q and "last_decision" in q
    ov = client.get("/api/overview").json()
    assert ov["receipts_waiting_for_transaction"] == 1 and ov["card_lines_without_receipt"] == 1


# ---------------------------------------------------------------- Del 6.7 loggning


def test_logging_info_to_stdout_errors_to_stderr_fonttools_quiet(capsys):
    import logging

    from app.logging_setup import configure_logging

    configure_logging(logging.INFO)
    logging.getLogger("bezala-test").info("vanlig rad")
    logging.getLogger("bezala-test").error("riktigt fel")
    logging.getLogger("fontTools.subset").info("brus")
    out, err = capsys.readouterr()
    assert "vanlig rad" in out and "vanlig rad" not in err
    assert "riktigt fel" in err and "riktigt fel" not in out
    assert "brus" not in out and "brus" not in err


# ---------------------------------------------------------------- Del 6.4 + 6.6


def _scope_for(db):
    from contextlib import contextmanager

    @contextmanager
    def scope():
        yield db
        db.commit()
    return scope


def test_trash_auto_purge_setting_works(db, monkeypatch):  # noqa: F811
    from datetime import timezone

    from app.models import ProcessedMessage
    from app.services import trash_scheduler
    from app.services.settings_service import load_settings

    monkeypatch.setattr(trash_scheduler, "session_scope", _scope_for(db))
    old = _receipt(db, date(2026, 9, 1), "Old", 1.0, waiting=False)
    new = _receipt(db, date(2026, 9, 1), "New", 1.0, waiting=False)
    old.deleted_at = datetime.now(timezone.utc) - timedelta(days=40)
    new.deleted_at = datetime.now(timezone.utc) - timedelta(days=2)
    old_id, new_id = old.message_id, new.message_id
    db.commit()
    assert trash_scheduler.purge_old_trash() == 0  # 0 = av
    load_settings(db).trash_auto_purge_days = 30
    db.commit()
    assert trash_scheduler.purge_old_trash() == 1
    db.expire_all()
    ids = {m.message_id for m in db.query(ProcessedMessage).all()}
    assert old_id not in ids and new_id in ids


def test_finnair_confirmation_and_hotel_payment_are_duplicates(db):  # noqa: F811
    from app.models import ProcessedMessage
    from app.services.receipt_details import mark_duplicates

    ticket = _receipt(db, date(2026, 3, 10), "Finnair", 334.0, waiting=False,
                      subject="Finnair e-ticket receipt", booking_ref="ABC123",
                      sender="Finnair <noreply@finnair.com>")
    conf = _receipt(db, date(2026, 2, 1), "Finnair", 334.0, waiting=False,
                    subject="Bokningsbekräftelse ABC123", booking_ref="ABC123",
                    sender="Finnair <noreply@finnair.com>")
    pay = _receipt(db, date(2026, 1, 13), "Clarion Hotel Sea U", 1444.0, "SEK", waiting=False,
                   category="Hotell", subject="Betalningsbekräftelse",
                   sender="Clarion <noreply@strawberry.se>")
    inv = _receipt(db, date(2026, 1, 13), "Clarion Hotel Sea U", 1444.0, "SEK", waiting=False,
                   category="Hotell", subject="Faktura SE131B0074992",
                   sender="Clarion <invoice@strawberry.se>")
    mark_duplicates(db)
    for m in (ticket, conf, pay, inv):
        db.refresh(m)
    assert conf.duplicate_of == ticket.message_id and ticket.duplicate_of is None
    assert pay.duplicate_of == inv.message_id and inv.duplicate_of is None
    # Idempotent — körs igen utan att märkningen försvinner.
    mark_duplicates(db)
    db.refresh(conf)
    assert conf.duplicate_of == ticket.message_id
    assert db.query(ProcessedMessage).filter(ProcessedMessage.duplicate_of.isnot(None)).count() == 2


def test_cleanup_empty_errors_and_pre_2026(db):  # noqa: F811
    from app.models import MaintenanceTask, ProcessedMessage
    from app.services.c47_cleanup import run_once

    db.add(ProcessedMessage(message_id="err-1", status="error", error_message="HttpError 404"))
    db.add(ProcessedMessage(message_id="err-2", status="error", sender="x@y", subject="s",
                            error_message="AI"))
    db.add(MaintenanceTask(name="c46_rescan_work_2026_v1",
                           ran_at=datetime.utcnow() - timedelta(days=3)))
    db.commit()
    skane = _receipt(db, date(2025, 11, 7), "Skånetrafiken", 30.0, "SEK", waiting=False)
    keep = _receipt(db, date(2026, 1, 5), "Moovy", 30.0, waiting=False)
    out = run_once(db)
    assert out["empty_errors"]["deleted"] == 1 and out["pre_2026"]["trashed"] == 1
    ids = {m.message_id for m in db.query(ProcessedMessage).all()}
    assert "err-1" not in ids and "err-2" in ids
    db.refresh(skane)
    db.refresh(keep)
    assert skane.deleted_at is not None and skane.delete_reason == "pre_2026"
    assert keep.deleted_at is None
    assert run_once(db) is None
