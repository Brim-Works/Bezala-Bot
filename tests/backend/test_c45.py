"""C45 — privat Gmail (Apple-kvitton) + kvitton som svar i trådar."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services.apple_receipt import parse_apple_receipt  # noqa: E402
from app.services.gmail_client import GmailClient, GmailMessage  # noqa: E402
from app.services.private_gmail import (  # noqa: E402
    PrivateSenderNotAllowed,
    build_private_query,
    parse_allowlist,
    sender_allowed,
)
from app.services.receipt_analyzer import ReceiptAnalysis  # noqa: E402
from app.services.reply_receipts import match_reply_receipt  # noqa: E402

WORK_ADDR = "employee@visma.com"
PRIVATE_ADDR = "someone@gmail.com"


@pytest.fixture()
def SessionLocal(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import db as db_module
    from app import models  # noqa: F401
    from app.db import Base
    from app.services import oauth_token_store, pipeline, settings_service  # noqa: F401

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SL = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    @contextmanager
    def scope():
        s = SL()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    monkeypatch.setattr(db_module, "session_scope", scope)
    monkeypatch.setattr(pipeline, "session_scope", scope)
    monkeypatch.setattr(oauth_token_store, "session_scope", scope)
    return SL


AIRPORT_RECEIPT = (
    "Hi there,\nThank you for reaching out. This is your request for a "
    "receipt of payment. Please find the payment details below.\nPayment Details:\n"
    "Order Number: 171791\nPayment Date: 04/14/2026\n"
    "Amount Paid: $ 69\n"
)

APPLE_ICLOUD_HTML = """
<html><body><h1>Receipt</h1>
<table><tr><td>APPLE ACCOUNT</td><td>s***@gmail.com</td></tr>
<tr><td>ORDER ID</td><td>MT7QX2K9LP</td></tr>
<tr><td>iCloud+ with 2 TB storage</td></tr><tr><td>Monthly · Renews 21 Dec 2026</td><td>€9.99</td></tr>
<tr><td>Subtotal</td><td>€7.96</td></tr><tr><td>VAT charged at 25.5%</td><td>€2.03</td></tr>
<tr><td>Total</td><td>€9.99</td></tr></table></body></html>
"""


def _msg(mid, *, sender, subject="", text="", html="", thread="t1", account="visma",
         received=datetime(2026, 6, 4, 13, 51, tzinfo=timezone.utc)):
    return GmailMessage(
        message_id=mid, thread_id=thread, sender=sender, subject=subject,
        received_at=received, snippet="", attachments=[],
        body_text=text, body_html=html, source_account=account,
    )


def _drive():
    d = MagicMock()
    up = MagicMock()
    up.file_id, up.web_view_link = "drv-1", "https://drive/drv-1"
    d.upload_pdf.return_value = up
    d.filename_exists.return_value = False
    return d


def _analyzer(analysis: ReceiptAnalysis):
    a = MagicMock()
    a.enabled = True
    a.analyze.return_value = analysis
    return a


# ---------------------------------------------------------------- privat konto


def test_private_query_contains_only_allowlist_senders():
    q = build_private_query(["no_reply@email.apple.com", "  NO_REPLY@email.apple.com "])
    assert q == "from:(no_reply@email.apple.com) after:2026/01/01"
    q2 = build_private_query(["no_reply@email.apple.com", "billing@example.org"])
    assert q2.startswith("from:(no_reply@email.apple.com OR billing@example.org) ")
    stripped = q2.replace("from:(no_reply@email.apple.com OR billing@example.org)", "")
    assert stripped.strip() == "after:2026/01/01"


def test_allowlist_rejects_wildcards_and_empty():
    # C46: '@domän' är tillåtet (domän + underdomäner); bara 'apple.com' inte.
    assert parse_allowlist("apple.com, @apple.com, *, , no_reply@email.apple.com, @gmail.com") == [
        "@apple.com", "no_reply@email.apple.com",
    ]
    assert build_private_query([]) is None
    assert build_private_query(["apple"]) is None


def test_sender_allowed_is_exact_address():
    al = ["no_reply@email.apple.com"]
    assert sender_allowed('"Apple" <No_Reply@Email.Apple.com>', al)
    assert not sender_allowed("friend@example.com", al)
    assert not sender_allowed("no_reply@email.apple.com.evil.io", al)
    assert not sender_allowed("x-no_reply@email.apple.com", al)


def _private_client(from_header: str) -> tuple[GmailClient, MagicMock]:
    c = GmailClient.__new__(GmailClient)
    c.account = "private"
    c._allowlist = ["no_reply@email.apple.com"]
    svc = MagicMock()
    get = svc.users.return_value.messages.return_value.get
    get.return_value.execute.return_value = {
        "payload": {"headers": [{"name": "From", "value": from_header}]},
    }
    c._service = svc
    return c, get


def test_private_message_from_other_sender_is_not_fetched():
    c, get = _private_client("Friend <friend@example.com>")
    with pytest.raises(PrivateSenderNotAllowed):
        c.fetch_message("m-private-1")
    assert get.call_count == 1
    kwargs = get.call_args.kwargs
    assert kwargs["format"] == "metadata"
    assert kwargs["metadataHeaders"] == ["From"]


def test_private_client_never_modifies_mailbox():
    c, _ = _private_client("no_reply@email.apple.com")
    c.mark_done("x")
    c.remove_done("x")
    c._service.users.return_value.messages.return_value.modify.assert_not_called()
    c._service.users.return_value.labels.assert_not_called()


def test_private_pass_ignores_non_allowlisted_even_if_returned(SessionLocal):
    from app.models import ProcessedMessage
    from app.services import pipeline as pl

    fake = MagicMock()
    fake.list_candidate_message_ids.return_value = ["bad-1"]
    fake.fetch_message.side_effect = PrivateSenderNotAllowed("nope")
    analyzer = MagicMock()
    analyzer.enabled = True
    result = pl.ScanResult()
    with patch.object(pl, "GmailClient", return_value=fake) as ctor, \
         patch("app.services.oauth_token_store.get_refresh_token", return_value="tok"):
        pl.run_private_pass(_drive(), MagicMock(), analyzer, None, result, use_ai=True)
    ctor.assert_called_once_with(account="private")
    q = fake.list_candidate_message_ids.call_args.kwargs["query"]
    assert q == "from:(no_reply@email.apple.com) after:2026/01/01"
    analyzer.analyze.assert_not_called()
    assert result.private_rejected == 1
    assert result.filtered == []
    with SessionLocal() as db:
        assert db.query(ProcessedMessage).count() == 0


def test_private_pass_skipped_when_not_connected(SessionLocal):
    from app.services import pipeline as pl

    with patch.object(pl, "GmailClient") as ctor, \
         patch("app.services.oauth_token_store.get_refresh_token", return_value=""):
        pl.run_private_pass(_drive(), MagicMock(), MagicMock(), None, pl.ScanResult())
    ctor.assert_not_called()


def test_two_accounts_use_separate_tokens_and_scopes(SessionLocal):
    from app.services import gmail_client as gc
    from app.services.oauth_token_store import get_refresh_token, save_refresh_token

    save_refresh_token("gmail", "visma-token")
    save_refresh_token("gmail_private", "private-token", extra={"email": PRIVATE_ADDR})
    assert get_refresh_token("gmail") == "visma-token"
    assert get_refresh_token("gmail_private") == "private-token"

    seen = []

    class FakeCreds:
        def __init__(self, **kw):
            seen.append(kw)

        def refresh(self, _req):
            pass

    settings = MagicMock(gmail_client_id="cid", gmail_client_secret="sec")
    with patch.object(gc, "Credentials", FakeCreds), \
         patch.object(gc, "build", return_value=MagicMock()), \
         patch.object(gc, "get_settings", return_value=settings):
        v = gc.GmailClient()
        p = gc.GmailClient(account="private")
    assert v.account == "visma" and p.account == "private"
    assert seen[0]["refresh_token"] == "visma-token"
    assert seen[0]["scopes"] == gc.SCOPES
    assert seen[1]["refresh_token"] == "private-token"
    assert seen[1]["scopes"] == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_settings_show_private_status_without_token(SessionLocal):
    from app.services.oauth_token_store import save_refresh_token
    from app.services.settings_service import private_gmail_status

    assert private_gmail_status()["connected"] is False
    save_refresh_token(
        "gmail_private", "secret-refresh",
        extra={"email": PRIVATE_ADDR, "scopes": ["gmail.readonly"]},
    )
    st = private_gmail_status()
    assert st["connected"] is True
    assert st["email"] == PRIVATE_ADDR
    assert st["allowlist"] == ["no_reply@email.apple.com"]
    assert "secret-refresh" not in repr(st)


def test_oauth_private_grant_rejects_work_account_and_extra_scopes():
    from app import main

    creds = MagicMock(scopes=["https://www.googleapis.com/auth/gmail.modify"])
    assert "error" in main._check_private_gmail_grant(creds)

    creds = MagicMock(scopes=["https://www.googleapis.com/auth/gmail.readonly"])
    svc = MagicMock()
    profile = svc.users.return_value.getProfile.return_value.execute
    profile.return_value = {"emailAddress": WORK_ADDR}
    with patch("googleapiclient.discovery.build", return_value=svc):
        assert "error" in main._check_private_gmail_grant(creds)
    profile.return_value = {"emailAddress": "Someone@Gmail.com"}
    with patch("googleapiclient.discovery.build", return_value=svc):
        assert main._check_private_gmail_grant(creds) == {"email": PRIVATE_ADDR}


def test_private_oauth_reuses_gmail_redirect_and_readonly_scope():
    from fastapi.testclient import TestClient

    from app import main

    captured = {}

    class FakeFlow:
        def authorization_url(self, **kw):
            captured.update(kw)
            return "https://accounts.google.com/x", "state-1"

    def fake_build(service, redirect_uri):
        captured["service"] = service
        captured["redirect_uri"] = redirect_uri
        return FakeFlow()

    client = TestClient(main.app)
    main.app.dependency_overrides[main.require_auth] = lambda: None
    try:
        with patch.object(main, "_build_oauth_flow", side_effect=fake_build):
            r = client.get("/api/auth/gmail_private/start", follow_redirects=False)
    finally:
        main.app.dependency_overrides.clear()
    assert r.status_code == 303
    assert captured["service"] == "gmail_private"
    assert captured["redirect_uri"].endswith("/api/auth/gmail/callback")
    assert main.OAUTH_SCOPES["gmail_private"] == [
        "https://www.googleapis.com/auth/gmail.readonly",
    ]


# ---------------------------------------------------------------- Apple


def test_apple_receipt_parsed_to_english_description():
    r = parse_apple_receipt(
        text="", html=APPLE_ICLOUD_HTML,
        received_at=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    assert r.service == "iCloud+"
    assert r.amount == 9.99 and r.currency == "EUR"
    assert r.order_id == "MT7QX2K9LP"
    assert r.description_en() == "Apple iCloud+ subscription - Nov 2026"
    assert not r.needs_review


def test_apple_hardware_is_flagged_not_guessed():
    r = parse_apple_receipt(
        text="Your receipt\nMacBook Air 13-inch\nTotal €278,90",
        html="", received_at=datetime(2026, 8, 10),
    )
    assert r.is_hardware and r.needs_review
    assert r.amount == 278.90
    assert r.description_en() == "Apple hardware/AppleCare purchase - Aug 2026"


def test_private_apple_receipt_becomes_pdf_with_english_description(SessionLocal):
    from app.models import ProcessedMessage
    from app.services import pipeline as pl

    msg = _msg(
        "apple-1", sender="Apple <no_reply@email.apple.com>",
        subject="Your receipt from Apple.", html=APPLE_ICLOUD_HTML,
        account="private", received=datetime(2026, 11, 21, tzinfo=timezone.utc),
    )
    gmail = MagicMock()
    gmail.fetch_message.return_value = msg
    drive = _drive()
    analysis = ReceiptAnalysis(
        is_receipt=True, confidence=95, filename="20261121 Apple iCloud.pdf",
        vendor="Apple", amount=None, currency=None, date="2026-11-21",
        category="software", summary="iCloud+", description_en="Apple receipt",
    )
    result = pl.ScanResult()
    pl._process_one_message(
        "apple-1", gmail, drive, MagicMock(), _analyzer(analysis), None, result,
        use_ai=True, html_to_pdf_enabled=True,
    )
    assert result.processed == 1
    assert drive.upload_pdf.call_args[0][1].startswith(b"%PDF")
    with SessionLocal() as db:
        row = db.query(ProcessedMessage).filter_by(message_id="apple-1").one()
        assert row.source_account == "private"
        assert row.ai_description_en == "Apple iCloud+ subscription - Nov 2026"
        assert row.amount == 9.99 and row.currency == "EUR"


def test_cross_account_duplicate_is_not_saved_twice(SessionLocal):
    from app.models import ProcessedMessage
    from app.services import pipeline as pl

    with SessionLocal() as db:
        db.add(ProcessedMessage(
            message_id="visma-fwd-1", sender=f"Me <{WORK_ADDR}>",
            subject="Fwd: Your receipt from Apple.", vendor="Apple",
            status="saved", amount=9.99, currency="EUR",
            receipt_date="2026-11-21", source_account="visma",
            booking_ref="MT7QX2K9LP",
        ))
        db.commit()
    msg = _msg(
        "apple-2", sender="no_reply@email.apple.com", html=APPLE_ICLOUD_HTML,
        account="private",
    )
    gmail = MagicMock()
    gmail.fetch_message.return_value = msg
    drive = _drive()
    analysis = ReceiptAnalysis(
        is_receipt=True, confidence=95, filename="Apple.pdf", vendor="Apple",
        amount=9.99, currency="EUR", date="2026-11-21", category="software",
        summary="", description_en="",
    )
    result = pl.ScanResult()
    pl._process_one_message(
        "apple-2", gmail, drive, MagicMock(), _analyzer(analysis), None, result,
        use_ai=True,
    )
    drive.upload_pdf.assert_not_called()
    assert result.processed == 0
    assert result.filtered[-1]["reason"] == "duplicate_other_account"
    with SessionLocal() as db:
        row = db.query(ProcessedMessage).filter_by(message_id="apple-2").one()
        assert row.status == "skipped:duplicate_other_account"
        assert row.source_account == "private"


def test_same_receipt_in_both_mailboxes_gives_one_receipt(SessionLocal):
    """C46 Del 0 — EN dubblettkontroll (C44:s). Samma Apple-kvitto i
    jobbkontot (vidarebefordrat) och i privata Gmail → ett sparat kvitto."""
    from app.models import ProcessedMessage
    from app.services import pipeline as pl

    analysis = lambda: ReceiptAnalysis(  # noqa: E731
        is_receipt=True, confidence=95, filename="Apple.pdf", vendor="Apple",
        amount=9.99, currency="EUR", date="2026-11-21", category="software",
        summary="", description_en="",
    )
    work = _msg("work-1", sender=f"Me <{WORK_ADDR}>",
                subject="Fwd: Your receipt from Apple.", html=APPLE_ICLOUD_HTML)
    priv = _msg("priv-1", sender="no_reply@email.apple.com",
                html=APPLE_ICLOUD_HTML, account="private")
    for msg in (work, priv):
        gmail = MagicMock()
        gmail.fetch_message.return_value = msg
        pl._process_one_message(
            msg.message_id, gmail, _drive(), MagicMock(), _analyzer(analysis()),
            None, pl.ScanResult(), use_ai=True, html_to_pdf_enabled=True,
        )
    with SessionLocal() as db:
        saved = db.query(ProcessedMessage).filter_by(status="saved").all()
        assert [r.message_id for r in saved] == ["work-1"]
        assert saved[0].booking_ref == "MT7QX2K9LP"
        skipped = db.query(ProcessedMessage).filter_by(message_id="priv-1").one()
        assert skipped.status == "skipped:duplicate_other_account"


def test_only_one_duplicate_check_exists():
    from app.services import pipeline as pl

    assert not hasattr(pl, "find_cross_account_duplicate")


def test_cross_account_same_amount_without_number_is_not_duplicate(SessionLocal):
    """C44-regeln: utan biljett-/bokningsnummer räcker inte belopp + datum."""
    from app.models import ProcessedMessage
    from app.services.receipt_details import find_saved_duplicate

    with SessionLocal() as db:
        db.add(ProcessedMessage(
            message_id="v-1", vendor="Skanetrafiken", status="saved",
            amount=150.0, currency="SEK", receipt_date="2026-03-10",
            source_account="visma",
        ))
        db.commit()
        assert find_saved_duplicate(
            db, message_id="p-1", amount=150.0, currency="SEK",
            receipt_date="2026-03-10", vendor="Skanetrafiken",
            other_account_than="private",
        ) is None


# ---------------------------------------------------------------- trådsvar


def test_reply_rule_matches_airport_receipt():
    m = match_reply_receipt(
        "Concierge Agent1 (Airport Recovery Services) <support@airportrecovery.com>",
        AIRPORT_RECEIPT,
    )
    assert m is not None
    assert m.amount == 69.0 and m.currency == "USD"
    assert m.order_id == "171791"
    assert match_reply_receipt("support@airportrecovery.com", "Update on your lost item") is None
    assert match_reply_receipt("other@example.com", AIRPORT_RECEIPT) is None


def test_thread_third_message_receipt_is_caught(SessionLocal):
    """Tråd: 1) leverantörens uppdatering 2) eget svar 3) kvittot.
    Sökträffen var meddelande 1 → meddelande 3 bedöms och sparas, trots
    att AI:n säger 'inte kvitto'."""
    from app.models import ProcessedMessage
    from app.services import pipeline as pl

    sender = "Concierge Agent1 <support@airportrecovery.com>"
    first = _msg("m1", sender=sender, subject="[ARS] Re: New form submission",
                 text="Unfortunately, the item was not turned in.")
    third = _msg("m3", sender=sender, subject="[ARS] Re: New form submission",
                 text=AIRPORT_RECEIPT)
    gmail = MagicMock()
    gmail.is_private = False
    gmail.fetch_message.side_effect = lambda mid: {"m1": first, "m3": third}[mid]
    gmail._ensure_done_label.return_value = "Label_DONE"
    gmail.list_thread_reply_ids.return_value = [
        {"message_id": "m1", "labels": ["INBOX"], "sender": sender},
        {"message_id": "m2", "labels": ["SENT"], "sender": WORK_ADDR},
        {"message_id": "m3", "labels": ["INBOX"], "sender": sender},
    ]
    analyzer = MagicMock()
    analyzer.enabled = True
    analyzer.analyze.side_effect = lambda **kw: ReceiptAnalysis(
        is_receipt=False, confidence=20, filename="x.pdf", vendor=None,
        amount=None, currency=None, date="2026-06-04", category=None,
        summary=None, description_en=None,
    )
    drive = _drive()
    result = pl.ScanResult()
    kwargs = dict(use_ai=True, html_to_pdf_enabled=True)
    pl._process_one_message("m1", gmail, drive, MagicMock(), analyzer, None, result, **kwargs)
    assert result.processed == 0
    assert result.threads == {"t1": "support@airportrecovery.com"}

    pl._process_thread_siblings(gmail, drive, MagicMock(), analyzer, None, result, **kwargs)
    fetched = [c.args[0] for c in gmail.fetch_message.call_args_list]
    assert fetched == ["m1", "m3"]
    assert result.processed == 1
    with SessionLocal() as db:
        row = db.query(ProcessedMessage).filter_by(message_id="m3").one()
        assert row.status == "saved"
        assert row.amount == 69.0 and row.currency == "USD"
        assert row.ai_description_en == (
            "Airport Recovery Services - lost item recovery fee, order 171791"
        )
    assert analyzer.analyze.call_args_list[-1].kwargs["negative_examples"] == []


def test_thread_sibling_filter():
    from app.services.pipeline import thread_sibling_candidates

    msgs = [
        {"message_id": "a", "labels": ["INBOX"], "sender": "x@v.com"},
        {"message_id": "b", "labels": ["SENT"], "sender": "x@v.com"},
        {"message_id": "c", "labels": ["DONE"], "sender": "x@v.com"},
        {"message_id": "d", "labels": [], "sender": "other@v.com"},
        {"message_id": "e", "labels": [], "sender": "X <x@v.com>"},
    ]
    assert thread_sibling_candidates(
        msgs, sender="x@v.com", done_label_id="DONE", known_ids={"a"},
    ) == ["e"]


def test_one_off_thread_scan_finds_airport_reply_and_runs_once(SessionLocal):
    from app.models import MaintenanceTask, ProcessedMessage
    from app.services import c45_migrations as c45
    from app.services import pipeline as pl

    sender = "Concierge Agent1 <support@airportrecovery.com>"
    msgs = {
        "m1": _msg("m1", sender=sender, text="Your lost item was not turned in."),
        "m3": _msg("m3", sender=sender, text=AIRPORT_RECEIPT),
    }
    gmail = MagicMock()
    gmail.is_private = False
    gmail.list_candidates_with_threads.return_value = [("m1", "t1"), ("m3", "t1")]
    gmail.fetch_message_metadata.side_effect = lambda mid: {"sender": sender}
    gmail.fetch_message.side_effect = lambda mid: msgs[mid]
    # Båda har Bezala-Klar sedan tidigare (AI avfärdade dem) — ignoreras här.
    gmail.list_thread_reply_ids.return_value = [
        {"message_id": "m1", "labels": ["Label_DONE"], "sender": sender},
        {"message_id": "m2", "labels": ["SENT"], "sender": WORK_ADDR},
        {"message_id": "m3", "labels": ["Label_DONE"], "sender": sender},
    ]
    analyzer = MagicMock()
    analyzer.enabled = True
    analyzer.analyze.side_effect = lambda **kw: ReceiptAnalysis(
        is_receipt=False, confidence=10, filename="ars.pdf", vendor=None,
        amount=None, currency=None, date="2026-06-04", category=None,
        summary=None, description_en=None,
    )
    deps = {
        "clients": (_drive(), MagicMock(), analyzer, None),
        "kwargs": {"use_ai": True, "html_to_pdf_enabled": True},
    }
    with patch.object(pl, "GmailClient", return_value=gmail), \
         patch.object(c45, "_scan_deps", return_value=deps):
        out = c45.thread_scan_2026()
        again = c45.thread_scan_2026()
    assert out["new_receipts"] == 1
    assert out["receipts"][0]["message_id"] == "m3"
    assert out["receipts"][0]["amount"] == 69.0
    assert again == {"skipped": "already_done"}
    q = gmail.list_candidates_with_threads.call_args.args[0]
    assert "from:support@airportrecovery.com" in q and "after:2026/01/01" in q
    with SessionLocal() as db:
        assert db.query(ProcessedMessage).filter_by(message_id="m3").one().status == "saved"
        assert db.query(MaintenanceTask).filter_by(name=c45.THREAD_SCAN_TASK).count() == 1


def test_seed_adds_airport_recovery_html_only_sender(SessionLocal):
    from app.models import HtmlOnlySender
    from app.services.c45_migrations import seed_c45_html_only_senders

    with SessionLocal() as db:
        assert seed_c45_html_only_senders(db) == 1
        assert seed_c45_html_only_senders(db) == 0
        pats = [s.sender_pattern for s in db.query(HtmlOnlySender).all()]
    assert "support@airportrecovery.com" in pats


def test_c45b_reprocesses_known_but_rejected_reply_receipt(SessionLocal):
    from app.models import ProcessedMessage
    from app.services import c45_migrations as c45
    from app.services import pipeline as pl

    sender = "Concierge Agent1 <support@airportrecovery.com>"
    with SessionLocal() as db:
        db.add_all([
            ProcessedMessage(message_id="m1", sender=sender, status="skipped:not_receipt"),
            ProcessedMessage(message_id="m3", sender=sender, status="skipped:not_receipt"),
            ProcessedMessage(message_id="ok", sender=sender, status="saved"),
        ])
        db.commit()
    msgs = {
        "m1": _msg("m1", sender=sender, text="Your lost item was not turned in."),
        "m3": _msg("m3", sender=sender, text=AIRPORT_RECEIPT),
    }
    gmail = MagicMock()
    gmail.fetch_message.side_effect = lambda mid: msgs[mid]
    with patch.object(pl, "GmailClient", return_value=gmail), \
         patch.object(pl, "force_process_message_ids",
                      return_value={"processed": 1, "details": []}) as fp:
        out = c45.reprocess_known_reply_receipts()
        again = c45.reprocess_known_reply_receipts()
    fp.assert_called_once_with(["m3"])
    assert out["forced"] == ["m3"] and out["candidates"] == 3
    assert again == {"skipped": "already_done"}
