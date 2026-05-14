"""Tester för GET /api/gmail/peek — debug-endpoint som returnerar
de råa message_ids som Gmail-sökningen ger, utan dedup eller pipeline."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "")
os.environ.setdefault("DRIVE_REFRESH_TOKEN", "")
os.environ.setdefault("BEZALA_USERNAME", "")
os.environ.setdefault("BEZALA_PASSWORD", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


def _configure_memory_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app import db as db_module

    db_module.engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_module.SessionLocal = sessionmaker(
        bind=db_module.engine, autoflush=False, autocommit=False
    )
    return db_module


class GmailPeekEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()

        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import ProcessedMessage
        from fastapi.testclient import TestClient

        Base.metadata.create_all(bind=db_module.engine)

        from contextlib import contextmanager
        SessionLocal = db_module.SessionLocal

        @contextmanager
        def session_scope():
            s = SessionLocal()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        def get_db():
            s = SessionLocal()
            try:
                yield s
            finally:
                s.close()

        db_module.session_scope = session_scope
        db_module.get_db = get_db
        app_module.get_db = get_db
        app_module.session_scope = session_scope

        try:
            from app.db import get_db as original_get_db
            app_module.app.dependency_overrides[original_get_db] = get_db
        except Exception:
            pass

        async def fake_require_auth():
            return None

        app_module.app.dependency_overrides[
            app_module.require_auth
        ] = fake_require_auth

        cls.app_module = app_module
        cls.SessionLocal = SessionLocal
        cls.ProcessedMessage = ProcessedMessage
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.ProcessedMessage).delete()
            db.commit()

    # --- Tests ----------------------------------------------------------

    def test_peek_with_vendor_filter_returns_ids(self):
        """vendor_filter byggs in i query, raw IDs returneras."""
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = [
            "peek-1", "peek-2",
        ]

        with patch.object(
            self.app_module, "GmailClient", return_value=fake_gmail,
        ):
            resp = self.client.get(
                "/api/gmail/peek",
                params={"vendor_filter": "lovable", "days": 14},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["message_ids"], ["peek-1", "peek-2"])
        self.assertIn("from:lovable", body["query"])
        self.assertIn("subject:lovable", body["query"])
        self.assertIn("after:", body["query"])

    def test_peek_with_raw_query_overrides_filter(self):
        """När 'query' anges ignoreras vendor_filter och days."""
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = ["x-1"]

        with patch.object(
            self.app_module, "GmailClient", return_value=fake_gmail,
        ):
            resp = self.client.get(
                "/api/gmail/peek",
                params={
                    "vendor_filter": "ignored",
                    "days": 30,
                    "query": "from:foo@bar.com newer_than:7d",
                },
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["query"], "from:foo@bar.com newer_than:7d")
        self.assertNotIn("ignored", body["query"])
        self.assertNotIn("after:", body["query"])
        call_kwargs = fake_gmail.list_candidate_message_ids.call_args.kwargs
        self.assertEqual(
            call_kwargs.get("query"), "from:foo@bar.com newer_than:7d",
        )

    def test_peek_no_side_effects(self):
        """Endpointen får inte skapa ProcessedMessage-rader."""
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = [
            "ghost-1", "ghost-2", "ghost-3",
        ]

        with self.SessionLocal() as db:
            before = db.query(self.ProcessedMessage).count()

        with patch.object(
            self.app_module, "GmailClient", return_value=fake_gmail,
        ):
            resp = self.client.get(
                "/api/gmail/peek", params={"days": 14},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        with self.SessionLocal() as db:
            after = db.query(self.ProcessedMessage).count()
        self.assertEqual(before, 0)
        self.assertEqual(after, 0)

    def test_peek_returns_empty_for_no_matches(self):
        """Tom Gmail-träfflista → total=0, message_ids=[]."""
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = []

        with patch.object(
            self.app_module, "GmailClient", return_value=fake_gmail,
        ):
            resp = self.client.get(
                "/api/gmail/peek",
                params={"vendor_filter": "nonexistent", "days": 14},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["total"], 0)
        self.assertEqual(body["message_ids"], [])
        self.assertIn("nonexistent", body["query"])


if __name__ == "__main__":
    unittest.main()
