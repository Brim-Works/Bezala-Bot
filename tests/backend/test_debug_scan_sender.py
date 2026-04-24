"""Test för GET /api/debug/scan-sender — debug-endpoint som gör en
Gmail-query med bara from:<sender> (inga exclusion-filter) och
returnerar metadata för varje träff."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "x")
os.environ.setdefault("SECRET_KEY", "x")
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


class DebugScanSenderTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from fastapi.testclient import TestClient

        Base.metadata.create_all(bind=db_module.engine)

        SessionLocal = db_module.SessionLocal

        def get_db():
            s = SessionLocal()
            try:
                yield s
            finally:
                s.close()

        app_module.get_db = get_db
        try:
            from app.db import get_db as original_get_db
            app_module.app.dependency_overrides[original_get_db] = get_db
        except Exception:
            pass

        async def fake_require_auth():
            return None

        app_module.app.dependency_overrides[app_module.require_auth] = fake_require_auth
        cls.client = TestClient(app_module.app)
        cls.app_module = app_module

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def test_scan_sender_returns_metadata_no_exclusions(self):
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = ["m1", "m2"]
        fake_gmail.fetch_message_metadata.side_effect = [
            {
                "message_id": "m1",
                "thread_id": "t1",
                "sender": "Skånetrafiken <noreply@skanetrafiken.se>",
                "subject": "Din biljett",
                "date": "Mon, 14 Apr 2026 10:00:00 +0200",
                "labels": ["INBOX", "CATEGORY_PROMOTIONS"],
                "snippet": "Din biljett…",
            },
            {
                "message_id": "m2",
                "thread_id": "t2",
                "sender": "Skånetrafiken <noreply@skanetrafiken.se>",
                "subject": "Kvitto",
                "date": "Tue, 15 Apr 2026 09:00:00 +0200",
                "labels": ["INBOX"],
                "snippet": "Kvitto…",
            },
        ]

        with patch.object(self.app_module, "GmailClient", return_value=fake_gmail):
            resp = self.client.get(
                "/api/debug/scan-sender", params={"sender": "skanetrafiken"}
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        # Query ska INTE innehålla exclusion-filter
        self.assertEqual(body["query"], "from:skanetrafiken")
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["messages"]), 2)

        first = body["messages"][0]
        self.assertEqual(first["message_id"], "m1")
        self.assertEqual(first["subject"], "Din biljett")
        self.assertIn("CATEGORY_PROMOTIONS", first["labels"])
        self.assertEqual(first["categories"], ["CATEGORY_PROMOTIONS"])

        # Vi anropade Gmail med rå from:-query utan några minus-filter
        q = fake_gmail.list_candidate_message_ids.call_args.kwargs["query"]
        self.assertEqual(q, "from:skanetrafiken")
        self.assertNotIn("-category", q)
        self.assertNotIn("-label", q)

    def test_scan_sender_400_without_sender(self):
        resp = self.client.get("/api/debug/scan-sender", params={"sender": ""})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
