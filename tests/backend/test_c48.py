"""C48 — fixar efter C47: dublettlarm, belopp saknas, prenumerationer,
privata Gmail-kvitton, syfte, +kvitto-adress, städlogg, köns sidor."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from tests.backend.test_c42 import _trip, db  # noqa: E402,F401
from tests.backend.test_c47 import FakeCouple, _line, _rate, _receipt  # noqa: E402

FLY = "MIKKO KEINONEN: FLYTOGET 1009001 MC, OSLO, NO 26.84 EUR"
SKANE = "MIKKO KEINONEN: SKANETRAFIKEN APP, KRISTIANSTAD, SE 14.01 EUR"


def _ids(db):  # noqa: F811
    from app.services.decision_queue import list_pending

    return [i["id"] for i in list_pending(db)["items"]]


# ---------------------------------------------------------------- Del 1


def _flytoget_case(db):  # noqa: F811
    d = date(2026, 10, 21)
    _rate(db, d, "NOK", "EUR", 0.09513)
    _line(db, 2462940, d, 26.84, desc=FLY)
    _line(db, 2462963, d, 26.84, desc=FLY)
    a = _receipt(db, d, "Flytoget", 276.0, "NOK", waiting=False, upload_source="gmail")
    b = _receipt(db, d, "Flytoget", 276.0, "NOK", waiting=False, upload_source="gmail")
    return a, b


def test_flytoget_and_skanetrafiken_not_flagged(db):  # noqa: F811
    from app.services.refunds import duplicate_charges

    _flytoget_case(db)
    d = date(2026, 11, 5)
    _rate(db, d, "SEK", "EUR", 0.091037)
    _line(db, 2493708, d, 14.01, desc=SKANE)
    _line(db, 2493711, d, 14.01, desc=SKANE)
    _receipt(db, d, "Skånetrafiken", 150.0, "SEK", waiting=False, upload_source="gmail")
    # Kvittot daterat dagen före kortraden — inom ±5 dagar.
    _receipt(db, d - timedelta(days=1), "Skånetrafiken", 150.0, "SEK", waiting=False,
             upload_source="gmail")
    assert duplicate_charges(db) == []
    assert not [i for i in _ids(db) if i.startswith("dup:")]


def test_two_lines_one_receipt_flags_with_detail(db):  # noqa: F811
    from app.services.refunds import duplicate_charges

    d = date(2026, 10, 21)
    _rate(db, d, "NOK", "EUR", 0.09513)
    _line(db, 2462940, d, 26.84, desc=FLY)
    _line(db, 2462963, d, 26.84, desc=FLY)
    r = _receipt(db, d, "Flytoget", 276.0, "NOK", waiting=False, upload_source="gmail")
    # Papperskorg och dubbletter räknas inte.
    _receipt(db, d, "Flytoget", 276.0, "NOK", waiting=False, deleted_at=datetime.utcnow())
    _receipt(db, d, "Flytoget", 276.0, "NOK", waiting=False, duplicate_of=r.message_id)
    flags = duplicate_charges(db)
    assert len(flags) == 1
    f = flags[0]
    assert f["card_lines"] == 2 and f["receipts"] == 1
    assert [c["bill_line_id"] for c in f["charges"]] == ["2462940", "2462963"]
    assert [x["message_id"] for x in f["receipt_list"]] == [r.message_id]
    from app.services.decision_queue import list_pending

    card = next(i for i in list_pending(db)["items"] if i["kind"] == "dup")
    assert "2 charges, 1 receipts" in card["subtitle"]


def test_dup_card_disappears_when_receipts_coupled(db):  # noqa: F811
    from app.services.refunds import duplicate_charges

    a, b = _flytoget_case(db)
    couple = FakeCouple()
    couple(db, "2462940", a.message_id)
    assert duplicate_charges(db) == []
    couple(db, "2462963", b.message_id)
    assert duplicate_charges(db) == []
    # Ett andra kvitto saknas (bara ett finns) → flagga; när kvittot för den
    # andra kortraden dyker upp och kopplas försvinner kortet.
    d = date(2026, 11, 2)
    _line(db, 2500001, d, 30.0, desc="MIKKO KEINONEN: TAXI HELSINKI, HELSINKI, FI 30.00 EUR")
    _line(db, 2500002, d, 30.0, desc="MIKKO KEINONEN: TAXI HELSINKI, HELSINKI, FI 30.00 EUR")
    r1 = _receipt(db, d, "Taxi Helsinki", 30.0, waiting=False)
    assert len(duplicate_charges(db)) == 1
    couple(db, "2500001", r1.message_id)
    r2 = _receipt(db, d, "Taxi Helsinki", 30.0, waiting=False)
    couple(db, "2500002", r2.message_id)
    assert duplicate_charges(db) == []


# ---------------------------------------------------------------- Del 2


def test_arlanda_receipt_link_found_with_newline_in_end_tag():
    from app.services.link_extractor import extract_receipt_link

    html = (
        '<a href="https://u1.ct.sendgrid.net/ls/click?upn=wallet-aaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        'aaaaaaaaaaaaaaaaaaaaaa" class="button-link"\r\n  >Add ticket to your wallet</a\r\n>'
        '<a\r\n href="https://u1.ct.sendgrid.net/ls/click?upn=receipt-bbbbbbbbbbbbbbbbbbbbbbbbb'
        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"\r\n class="button-link"\r\n  >Download receipt pdf</a\r\n>'
    )
    assert "upn=receipt-" in extract_receipt_link("", html)


def test_arlanda_without_amount_never_autocoupled_and_flagged(db):  # noqa: F811
    from app.services import decision_queue as q
    from app.services.waiting_receipts import match_waiting_receipts

    d = date(2026, 11, 3)
    _line(db, 2486515, d, 55.95, desc="MIKKO KEINONEN: ARLANDA EXPRESS, STOCKHOLM, SE 55.95 EUR")
    r = _receipt(db, d, "Arlanda Express", None, "SEK", upload_source="gmail")
    couple = FakeCouple()
    match_waiting_receipts(db, couple=couple)
    assert couple.calls == []
    cards = [i for i in q.list_pending(db)["items"] if i["kind"] == "receipt"]
    assert cards  # föreslås bara med tydlig varning och utan beloppspoäng
    if cards:
        card = cards[0]
        assert card["receipt"]["message_id"] == r.message_id
        assert card["amount_missing"] is True
        assert card["warning"] == "Amount missing - open the receipt before coupling"
        assert "Amount missing - open the receipt before coupling" in card["subtitle"]
    from app.services.currency_converter import make_db_rate_provider
    from app.services.receipt_matcher import score_match
    from app.services.card_line_coupling import _missing_from_card_line, _serialize
    from app.models import CardLine

    line = db.query(CardLine).filter(CardLine.bill_line_id == "2486515").first()
    s = score_match(_missing_from_card_line(db, line), _serialize(r),
                    rate_provider=make_db_rate_provider(db))
    assert s["breakdown"]["amount"] == 0


def test_subscription_never_autocouples_receipt_without_amount(db):  # noqa: F811
    from app.models import Subscription
    from app.services.subscriptions import process_lines

    db.add(Subscription(name="Cursor", vendor_pattern="cursor", active=True, account=6820,
                        review_status="confirmed", expected_amount=20.0, currency="EUR"))
    db.commit()
    _receipt(db, date(2026, 11, 3), "Cursor", None, waiting=False, sender="cursor.com")
    _line(db, 2600040, date(2026, 11, 3), 20.0, desc="CURSOR, NEW YORK, US 20.00 EUR")
    couple = FakeCouple()
    process_lines(db, couple=couple)
    assert couple.calls == []


def test_amount_missing_report_and_reread(db):  # noqa: F811
    from contextlib import contextmanager

    from app.models import CleanupLog, ProcessedMessage
    from app.services import amount_missing as am

    d = date(2026, 11, 3)
    ax = _receipt(db, d, "Arlanda Express", None, "SEK", upload_source="gmail",
                  message_id="1a1a0298cf839a64")
    mob = _receipt(db, d, "Taxi", None, upload_source="mobile")
    _receipt(db, date(2025, 11, 3), "Old", None, upload_source="gmail")
    coupled = _receipt(db, d, "Coupled", None, upload_source="gmail",
                       bezala_transaction_id="tx-1")
    ax_id, mob_id = ax.message_id, mob.message_id
    assert {m.message_id for m in am.rows(db)} == {ax_id, mob_id, coupled.message_id}
    started = []
    out = am.report_and_reread(db, runner=started.append)
    assert out["without_amount"] == 3 and started == [[ax_id]]
    assert am.report_and_reread(db, runner=started.append) is None

    @contextmanager
    def scope():
        yield db
        db.commit()

    def fake_force(ids, auto_upload=None):
        assert auto_upload is False  # inga Bezala-utkast vid omläsning
        db.query(ProcessedMessage).filter(ProcessedMessage.message_id.in_(ids)).delete(
            synchronize_session=False)
        db.add(ProcessedMessage(message_id=ids[0], status="saved", vendor="Arlanda Express",
                                amount=640.0, currency="SEK", receipt_date="2026-11-03"))
        db.commit()
        return {"details": []}

    res = am.reread([ax_id], force_process=fake_force, session_factory=scope)
    assert res[0]["amount"] == 640.0
    row = db.query(ProcessedMessage).filter(ProcessedMessage.message_id == ax_id).one()
    assert row.waiting_status == "waiting_for_transaction"  # kopierat från före
    assert db.query(CleanupLog).filter(CleanupLog.rule == "amount_missing").count() >= 4

    # Misslyckad omläsning → raden återställs.
    def failing_force(ids, auto_upload=None):
        db.query(ProcessedMessage).filter(ProcessedMessage.message_id.in_(ids)).delete(
            synchronize_session=False)
        db.commit()
        raise RuntimeError("Gmail down")

    am.reread([mob_id], force_process=failing_force, session_factory=scope)
    assert db.query(ProcessedMessage).filter(
        ProcessedMessage.message_id == mob_id).count() == 1


# ---------------------------------------------------------------- Del 3


def _sub(db, name, pattern, **kw):  # noqa: F811
    from app.models import Subscription

    s = Subscription(name=name, vendor_pattern=pattern, cadence="monthly",
                     active=False, review_status=kw.pop("review_status", "suggested"),
                     origin=kw.pop("origin", "detected"), **kw)
    db.add(s)
    db.commit()
    return s


def test_likely_private_flow_with_undo(db):  # noqa: F811
    from app.models import CardLine
    from app.services import decision_queue as q
    from app.services.subscriptions import apply_private, tidy_suggestions

    desc = "MIKKO KEINONEN: NETFLIX.COM, AMSTERDAM, NL 13.99 EUR"
    _line(db, 2700001, date(2026, 9, 3), 13.99, desc=desc, status="resolved")
    _line(db, 2700002, date(2026, 10, 3), 13.99, desc=desc)
    sub = _sub(db, "Netflix", "netflix")
    tidy_suggestions(db)
    db.refresh(sub)
    assert sub.kind == "likely_private" and sub.expected_amount == 13.99
    card = next(i for i in q.list_pending(db)["items"] if i["kind"] == "subpriv")
    assert card["yes_label"] == "Private - mark as private expense"
    assert card["no_label"] == "Business subscription"
    assert f"sub:{sub.id}" not in _ids(db)
    q.decide(db, card["id"], "yes")
    line = db.query(CardLine).filter(CardLine.bill_line_id == "2700002").one()
    assert line.status == "private_expense"
    # Nya kortrader från leverantören markeras automatiskt privata.
    _line(db, 2700003, date(2026, 11, 3), 13.99, desc=desc)
    assert apply_private(db) == ["2700003"]
    # Ångra: kortraderna tillbaka, förslaget tillbaka i kön.
    q.undo_last(db)
    for bid in ("2700002", "2700003"):
        assert db.query(CardLine).filter(CardLine.bill_line_id == bid).one().status == "missing"
    db.refresh(sub)
    assert sub.review_status == "suggested" and card["id"] in _ids(db)
    # Nej = företagsprenumeration → vanligt prenumerationskort.
    q.decide(db, card["id"], "no")
    db.refresh(sub)
    assert sub.kind == "business" and f"sub:{sub.id}" in _ids(db)


def test_private_receipts_without_card_lines_dismissed_silently(db):  # noqa: F811
    from app.models import CardLine
    from app.services.subscriptions import detect_subscriptions, tidy_suggestions

    for m in (5, 6, 7, 8):
        _receipt(db, date(2026, m, 30), "SkyShowtime", 9.99, waiting=False,
                 sender="Apple <no_reply@email.apple.com>", source_account="private")
        _receipt(db, date(2026, m, 11), "Crunchyroll", 6.99, waiting=False,
                 sender="Apple <no_reply@email.apple.com>", source_account="private")
    _line(db, 2700100, date(2026, 11, 1), 50.0, desc="TAXI HELSINKI, HELSINKI, FI 50.00 EUR")
    assert detect_subscriptions(db) == []  # bara kvitton → inget förslag
    sky = _sub(db, "Skyshowtime", "skyshowtime", expected_amount=9.99, currency="EUR")
    tidy_suggestions(db)
    db.refresh(sky)
    assert sky.review_status == "dismissed" and sky.note == "no_card_lines"
    assert sky.kind is None
    assert db.query(CardLine).filter(CardLine.status == "private_expense").count() == 0
    assert f"sub:{sky.id}" not in _ids(db) and f"subpriv:{sky.id}" not in _ids(db)


def test_openai_chatgpt_merged(db):  # noqa: F811
    from app.services.subscriptions import tidy_suggestions

    for m in (8, 9):
        _line(db, 2700200 + m, date(2026, m, 28), 22.99,
              desc="MIKKO KEINONEN: OPENAI *CHATGPT SUBSCR, SAN FRANCISCO, US 22.99 EUR",
              status="resolved")
    a = _sub(db, "Openai (Apple App Store)", "openai", expected_amount=22.99, currency="EUR")
    b = _sub(db, "Chatgpt", "chatgpt", expected_amount=22.99, currency="EUR")
    out = tidy_suggestions(db)
    db.refresh(a)
    db.refresh(b)
    assert out["merged"] and a.vendor_pattern == "openai|chatgpt"
    assert b.review_status == "merged" and a.review_status == "suggested"


def test_recent_only_card_lines_and_amount_autofill(db):  # noqa: F811
    from app.services.subscriptions import serialize, tidy_suggestions

    desc = "MIKKO KEINONEN: LOVABLE, DOVER, US 100.0 EUR"
    sub = _sub(db, "Lovable", "lovable", origin="seed")
    for m in (9, 10, 11):
        _line(db, 2700300 + m, date(2026, m, 5), 100.0, desc=desc, status="resolved")
        _receipt(db, date(2026, m, 5), "Lovable", 100.0, waiting=False)
    _line(db, 2700399, date(2026, 8, 5), 90.0, desc=desc, status="resolved")
    s = serialize(db, sub)
    assert len(s["recent"]) == 4 and {r["source"] for r in s["recent"]} == {"card_line"}
    assert s["receipts"] == 3 and "amount_unknown" in s["flags"]
    tidy_suggestions(db)
    db.refresh(sub)
    assert (sub.expected_amount, sub.currency) == (100.0, "EUR")  # typvärdet


def test_subscription_requires_account(db):  # noqa: F811
    from app.services import decision_queue as q
    from app.services.subscriptions import SubscriptionError, bezala_accounts, set_account

    desc = "CURSOR, AI POWERED IDE, SAN FRANCISCO, US 22.03 EUR"
    _line(db, 2700400, date(2026, 10, 24), 22.03, desc=desc, status="resolved")
    sub = _sub(db, "Cursor", "cursor|anysphere", origin="seed")
    card = next(i for i in q.list_pending(db)["items"] if i["id"] == f"sub:{sub.id}")
    assert card["blocked"] and "Account missing - set it first" in card["subtitle"]
    with pytest.raises(Exception, match="Account missing - set it first"):
        q.decide(db, f"sub:{sub.id}", "yes")

    class FakeBezala:
        def list_accounts(self):
            return [{"id": 6820, "name": "Software"}, {"id": 6000, "name": "Travel"}]

        def close(self):
            pass

    accounts = bezala_accounts(db, client=FakeBezala())["accounts"]
    assert {a["id"] for a in accounts} == {6820, 6000}
    with pytest.raises(SubscriptionError):
        set_account(db, sub.id, 1234, accounts=accounts)
    set_account(db, sub.id, 6820, accounts=accounts)
    q.decide(db, f"sub:{sub.id}", "yes")
    db.refresh(sub)
    assert sub.active and sub.account == 6820 and sub.expected_amount == 22.03


# ---------------------------------------------------------------- Del 3b


def test_private_apple_receipt_without_card_line_ignored_after_14_days(db):  # noqa: F811
    from app.models import ProcessedMessage
    from app.services.waiting_receipts import match_waiting_receipts

    old = datetime.utcnow() - timedelta(days=15)
    priv = _receipt(db, date.today() - timedelta(days=15), "Crunchyroll", 6.99,
                    upload_source="gmail", source_account="private", since=old,
                    sender="Apple <no_reply@email.apple.com>")
    work = _receipt(db, date.today() - timedelta(days=15), "Taxi Helsinki", 31.0,
                    upload_source="gmail", since=old)
    fresh = _receipt(db, date.today(), "HBO Max", 11.99, upload_source="gmail",
                     source_account="private")
    # Gammalt köp som boten sparade nyss (omläsningen) → räknas från kvittodatum.
    rescanned = _receipt(db, date(2026, 4, 30), "SkyShowtime", 9.99, upload_source="gmail",
                         source_account="private", since=datetime.utcnow())
    out = match_waiting_receipts(db, couple=FakeCouple())
    assert out["ignored_private_source"] == 2
    get = lambda m: db.query(ProcessedMessage).get(m.id).waiting_status  # noqa: E731
    assert get(priv) == "ignored_private_source"
    assert get(rescanned) == "ignored_private_source"
    assert get(work) == "no_transaction"  # jobbkvitton flaggas som förut
    assert get(fresh) == "waiting_for_transaction"
    ids = _ids(db)
    assert f"notxn:{priv.message_id}" not in ids and f"notxn:{work.message_id}" in ids
    from app.services.cleanup_log import report

    assert report(db, "ignored_private_source")["counts"]["ignored_private_source"] == 2


def test_private_apple_receipt_with_card_line_coupled(db):  # noqa: F811
    from app.services.waiting_receipts import match_waiting_receipts

    d = date.today() - timedelta(days=2)
    r = _receipt(db, d, "Apple App Store", 9.99, upload_source="gmail",
                 source_account="private", sender="Apple <no_reply@email.apple.com>")
    _line(db, 2700500, d, 9.99, desc="MIKKO KEINONEN: APPLE.COM/BILL, ITUNES.COM, IE 9.99 EUR")
    couple = FakeCouple()
    match_waiting_receipts(db, couple=couple)
    assert couple.calls == [("2700500", r.message_id)]


# ---------------------------------------------------------------- Del 4


def test_purpose_normalized_trip_133(db):  # noqa: F811
    from app.services.c48_migrations import fix_purposes
    from app.services.trip_english import combine_purposes, normalize_purpose

    p = "Workshop and team syncs, Oslo office; Team syncs, Oslo office"
    assert normalize_purpose(p, ["Oslo"]) == "Workshop and team syncs, Oslo office"
    assert normalize_purpose(p) == "Workshop and team syncs, Oslo office"
    assert combine_purposes(["Workshop and team syncs, Oslo office",
                             "Team syncs, Oslo office"], ["Oslo"]) == \
        "Workshop and team syncs, Oslo office"
    assert normalize_purpose("Customer day Agda PS, Malmö; Workshop, Malmö") == \
        "Customer day Agda PS, Malmö; Workshop, Malmö"
    t = _trip(db, date(2026, 4, 13), date(2026, 4, 14), destination="Oslo", airport="OSL")
    t.purpose = p
    locked = _trip(db, date(2026, 5, 13), date(2026, 5, 14), status="approved",
                   destination="Oslo", airport="OSL")
    locked.purpose = p
    sv = _trip(db, date(2026, 6, 1), date(2026, 6, 2), status="edited")
    sv.purpose = "Kundmöte och workshop, Malmö"
    db.commit()
    out = fix_purposes(db)
    db.refresh(t)
    db.refresh(locked)
    assert t.purpose == "Workshop and team syncs, Oslo office"
    assert locked.purpose == p  # godkända ändras aldrig
    assert {r["trip_id"] for r in out["report"]} == {locked.id, sv.id}
    assert fix_purposes(db) is None


# ---------------------------------------------------------------- Del 5


def test_receipt_inbox_info_and_log(db, monkeypatch):  # noqa: F811
    from app.services import kvitto_mail as km

    class FakeGmail:
        def get_profile_email(self):
            return "mikko.keinonen@example.com"

    monkeypatch.setattr(km, "_private_profile_email", lambda: "mikko.private@gmail.com")
    km._last_config.clear()
    info = km.inbox_info(db, gmail=FakeGmail())
    assert info["address"] == "mikko.keinonen+kvitto@example.com"
    assert "mikkokeinonen+kvitto@example.com" in info["also_accepted"]
    assert "mikko.private@gmail.com" in info["allowed_senders"]
    assert info["recent"] == []

    from contextlib import contextmanager

    @contextmanager
    def scope():
        yield db
        db.commit()

    monkeypatch.setattr(km, "session_scope", scope)
    km._log_mail("m1", "x@evil.com", None, None, "rejected", "not own")
    km._log_mail("m2", "mikko.private@gmail.com", datetime(2026, 11, 20, 8), 2, "accepted")
    km._log_mail("m2", "mikko.private@gmail.com", datetime(2026, 11, 20, 8), 2, "accepted")
    recent = km.inbox_info(db)["recent"]
    assert [r["outcome"] for r in recent] == ["accepted", "rejected"]
    assert recent[0]["attachments"] == 2


# ---------------------------------------------------------------- Del 6


def test_cleanup_log_backfill_and_report(db):  # noqa: F811
    from app.models import ProcessedMessage
    from app.services.c47_cleanup import backfill_cleanup_log, clean_empty_error_rows
    from app.services.cleanup_log import report

    db.add(ProcessedMessage(message_id="err-1", status="error",
                            error_message="HttpError 404: Requested entity was not found."))
    _receipt(db, date(2025, 3, 1), "Old Hotel", 100.0, waiting=False,
             deleted_at=datetime.utcnow(), delete_reason="pre_2026")
    _receipt(db, date(2026, 3, 1), "Fixed", 10.0, waiting=False, review_flag="date_corrected")
    keep = _receipt(db, date(2026, 3, 2), "Finnair", 200.0, waiting=False)
    _receipt(db, date(2026, 3, 2), "Finnair", 200.0, waiting=False,
             duplicate_of=keep.message_id)
    db.commit()
    out = backfill_cleanup_log(db)
    assert out == {"empty_error_now": 1, "pre_2026": 1, "date_corrected": 1, "duplicates": 1}
    assert backfill_cleanup_log(db) is None
    r = report(db)
    assert r["counts"]["empty_error"] == 1 and r["counts"]["pre_2026"] == 1
    assert r["empty_error_causes"][0][0].startswith("HttpError 404")
    clean_empty_error_rows(db, run="c48-test")
    assert report(db, "empty_error")["counts"]["empty_error"] == 2
    assert db.query(ProcessedMessage).filter(ProcessedMessage.message_id == "err-1").count() == 0


# ---------------------------------------------------------------- Del 7


def test_queue_paging_and_trip_completeness(db):  # noqa: F811
    from app.models import CalendarTripDay
    from app.services.decision_queue import list_pending

    for i in range(3):
        t = _trip(db, date(2026, 3, 2 + i * 7), date(2026, 3, 3 + i * 7))
    day = db.query(CalendarTripDay).filter(CalendarTripDay.trip_id == t.id).first()
    day.free_meals = 1
    db.commit()
    p1 = list_pending(db, limit=2)
    assert p1["total"] == 3 and len(p1["items"]) == 2 and p1["next_offset"] == 2
    p2 = list_pending(db, limit=2, offset=2)
    assert len(p2["items"]) == 1 and "next_offset" not in p2
    assert {i["id"] for i in p1["items"] + p2["items"]} == {f"trip:{x}" for x in (1, 2, 3)}
    card = next(i for i in p1["items"] + p2["items"] if i["id"] == f"trip:{t.id}")
    assert card["free_meals"] == 1
    assert card["completeness"].startswith("0/") and "missing: flight" in card["completeness"]
    assert "1 free meal(s)" in card["subtitle"]


def test_mcp_list_pending_offset_signature():
    import inspect

    from app import mcp_server

    sig = inspect.signature(mcp_server.list_pending_decisions)
    assert "offset" in sig.parameters
