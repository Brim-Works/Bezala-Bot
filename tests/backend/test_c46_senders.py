"""C46 Del 1 — avsändare för privat Gmail (tabellen private_gmail_senders)."""

from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest  # noqa: E402

from app.services.private_gmail import (  # noqa: E402
    InvalidSender,
    PrivateSenderNotAllowed,
    build_private_query,
    sender_allowed,
    validate_sender,
)


@pytest.fixture()
def SessionLocal(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app import db as db_module
    from app import models  # noqa: F401
    from app.db import Base
    from app.services import oauth_token_store, pipeline  # noqa: F401

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


ENV = "no_reply@email.apple.com,noreply@scandichotels.com,no-reply@notify.scandichotels.com"


def _env(value=ENV):
    s = MagicMock()
    s.private_gmail_sender_allowlist = value
    return patch("app.services.private_gmail.get_settings", return_value=s)


# ------------------------------------------------------------- validering


@pytest.mark.parametrize("bad", [
    "@gmail.com", "someone@gmail.com", "x@googlemail.com", "@outlook.com",
    "a@hotmail.com", "@live.com", "@icloud.com", "a@me.com", "@yahoo.com",
    "@proton.me", "@protonmail.com", "@gmx.de", "a@gmx.net", "x@mail.gmx.com",
    "@sub.gmail.com",
])
def test_public_domains_are_blocked(bad):
    with pytest.raises(InvalidSender):
        validate_sender(bad)


@pytest.mark.parametrize("bad", ["", "*", "scandichotels.com", "a b@x.se", "@x", "a@@x.se"])
def test_invalid_input_is_refused(bad):
    with pytest.raises(InvalidSender):
        validate_sender(bad)


def test_valid_email_and_domain():
    assert validate_sender(" Kvitto@Firma.SE ") == ("kvitto@firma.se", "email")
    assert validate_sender("@scandichotels.com") == ("@scandichotels.com", "domain")


def test_own_private_address_is_blocked():
    with pytest.raises(InvalidSender):
        validate_sender("me@private-domain.fi", blocked_addresses=["me@private-domain.fi"])


def test_domain_matches_subdomain_but_not_lookalike():
    al = ["@scandichotels.com"]
    assert sender_allowed("Scandic <no-reply@notify.scandichotels.com>", al)
    assert sender_allowed("noreply@scandichotels.com", al)
    assert not sender_allowed("x@scandichotels.com.evil.io", al)
    assert not sender_allowed("x@notscandichotels.com", al)


def test_query_uses_domain_without_at():
    q = build_private_query(["@scandichotels.com", "no_reply@email.apple.com"])
    assert q == "from:(scandichotels.com OR no_reply@email.apple.com) after:2026/01/01"


# ------------------------------------------------------------- tabellen


def test_env_seeds_missing_and_never_removes(SessionLocal):
    from app.services import private_senders as ps

    with _env(), SessionLocal() as db:
        assert ps.seed_from_env(db) == ENV.split(",")
        ps.remove_sender(db, "noreply@scandichotels.com")
    # env ändras: en adress försvinner ur env, en tillkommer
    with _env("no_reply@email.apple.com,kvitto@firma.se"), SessionLocal() as db:
        assert ps.seed_from_env(db) == ["kvitto@firma.se"]
        rows = {r["address"]: r for r in ps.list_senders(db)["senders"]}
        # finns kvar trots att env inte längre nämner den
        assert rows["no-reply@notify.scandichotels.com"]["active"] is True
        # borttagen rad återaktiveras inte av env
        assert rows["noreply@scandichotels.com"]["active"] is False
        assert rows["kvitto@firma.se"]["added_via"] == "env"
        assert ps.active_addresses() == [
            "no_reply@email.apple.com", "no-reply@notify.scandichotels.com",
            "kvitto@firma.se",
        ]


def test_add_sender_starts_backfill_and_validates(SessionLocal):
    from app.services import private_senders as ps

    with _env(), SessionLocal() as db, \
         patch.object(ps, "start_backfill", return_value=True) as bf:
        r = ps.add_sender(db, "@Firma.se", "Hotel Firma", via="mcp")
        assert r["status"] == "added" and r["backfill_started"] is True
        assert r["sender"]["kind"] == "domain"
        assert r["sender"]["added_via"] == "mcp"
        bf.assert_called_once_with("@firma.se")
        again = ps.add_sender(db, "@firma.se", via="ui")
        assert again["status"] == "already_active" and again["backfill_started"] is False
        with pytest.raises(InvalidSender):
            ps.add_sender(db, "@gmail.com")
        ps.remove_sender(db, "@firma.se")
        back = ps.add_sender(db, "@firma.se")
        assert back["status"] == "reactivated" and back["backfill_started"] is True


def test_removed_sender_is_no_longer_read(SessionLocal):
    from app.services import pipeline as pl
    from app.services import private_senders as ps

    with _env(), SessionLocal() as db:
        ps.seed_from_env(db)
        ps.remove_sender(db, "noreply@scandichotels.com")
        ps.remove_sender(db, "no-reply@notify.scandichotels.com")
    fake = MagicMock()
    fake.list_candidate_message_ids.return_value = []
    with _env(), patch.object(pl, "GmailClient", return_value=fake), \
         patch("app.services.oauth_token_store.get_refresh_token", return_value="tok"):
        pl.run_private_pass(MagicMock(), MagicMock(), MagicMock(), None, pl.ScanResult())
    q = fake.list_candidate_message_ids.call_args.kwargs["query"]
    assert q == "from:(no_reply@email.apple.com) after:2026/01/01"
    assert "scandic" not in q
    # och avsändarkontrollen före hämtning: borttagen avsändare släpps inte igenom
    from app.services.private_gmail import get_private_allowlist

    with _env():
        assert not sender_allowed("noreply@scandichotels.com", get_private_allowlist())


def test_backfill_scans_only_that_sender(SessionLocal):
    from app.services import pipeline as pl
    from app.services import private_senders as ps

    with _env(), SessionLocal() as db:
        ps.seed_from_env(db)
    fake = MagicMock()
    fake.list_candidate_message_ids.return_value = []
    with _env(), patch.object(pl, "GmailClient", return_value=fake), \
         patch("app.services.oauth_token_store.get_refresh_token", return_value="tok"):
        pl.run_private_pass(
            MagicMock(), MagicMock(), MagicMock(), None, pl.ScanResult(),
            senders=["noreply@scandichotels.com", "not-in-list@x.se"], max_results=500,
        )
    q = fake.list_candidate_message_ids.call_args.kwargs["query"]
    assert q == "from:(noreply@scandichotels.com) after:2026/01/01"
    with SessionLocal() as db:
        rows = {r["address"]: r for r in ps.list_senders(db)["senders"]}
        assert rows["noreply@scandichotels.com"]["last_scan_at"]
        assert rows["no_reply@email.apple.com"]["last_scan_at"] is None


def test_unknown_sender_is_never_fetched(SessionLocal):
    """Gmail returnerar ett mail från en okänd avsändare: bara From-headern
    läses (metadata), inget innehåll hämtas, inget sparas — bara räknaren."""
    from app.models import ProcessedMessage
    from app.services import pipeline as pl
    from app.services.gmail_client import GmailClient

    c = GmailClient.__new__(GmailClient)
    c.account = "private"
    with _env():
        from app.services.private_gmail import get_private_allowlist

        c._allowlist = get_private_allowlist()
    svc = MagicMock()
    get = svc.users.return_value.messages.return_value.get
    get.return_value.execute.return_value = {
        "payload": {"headers": [{"name": "From", "value": "Friend <friend@example.com>"}]},
    }
    c._service = svc
    c.list_candidate_message_ids = MagicMock(return_value=["x-1"])
    analyzer = MagicMock()
    result = pl.ScanResult()
    with _env(), patch.object(pl, "GmailClient", return_value=c), \
         patch("app.services.oauth_token_store.get_refresh_token", return_value="tok"):
        pl.run_private_pass(MagicMock(), MagicMock(), analyzer, None, result, use_ai=True)
    assert get.call_count == 1
    assert get.call_args.kwargs["format"] == "metadata"
    assert get.call_args.kwargs["metadataHeaders"] == ["From"]
    analyzer.analyze.assert_not_called()
    assert result.private_rejected == 1
    with SessionLocal() as db:
        assert db.query(ProcessedMessage).count() == 0
    with pytest.raises(PrivateSenderNotAllowed):
        c.fetch_message("x-1")


def test_stats_per_sender(SessionLocal):
    from app.models import ProcessedMessage
    from app.services import private_senders as ps

    with _env("@scandichotels.com"), SessionLocal() as db:
        ps.seed_from_env(db)
        db.add_all([
            ProcessedMessage(message_id="a", sender="no-reply@notify.scandichotels.com",
                             status="saved", source_account="private"),
            ProcessedMessage(message_id="b", sender="noreply@scandichotels.com",
                             status="skipped:not_receipt", source_account="private"),
            ProcessedMessage(message_id="c", sender="noreply@scandichotels.com",
                             status="saved", source_account="visma"),
        ])
        db.commit()
        row = ps.list_senders(db)["senders"][0]
        assert (row["fetched"], row["saved"], row["rejected"]) == (2, 1, 1)


def test_mcp_tools_validate(SessionLocal):
    import anyio

    from app import mcp_server
    from app.services import private_senders as ps
    from app.services.calendar_trip_edit import TripEditError

    with patch.object(mcp_server, "SessionLocal", SessionLocal), _env(), \
         patch.object(ps, "start_backfill", return_value=True):
        r = anyio.run(mcp_server.add_private_sender, "kvitto@firma.se", "test")
        assert r["status"] == "added" and r["backfill_started"] is True
        with pytest.raises(TripEditError):
            anyio.run(mcp_server.add_private_sender, "@hotmail.com", None)
        lst = anyio.run(mcp_server.list_private_senders)
        assert "kvitto@firma.se" in [s["address"] for s in lst["senders"]]
        rm = anyio.run(mcp_server.remove_private_sender, "kvitto@firma.se")
        assert rm["status"] == "removed"
