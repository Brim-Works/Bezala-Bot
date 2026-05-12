"""Tester för POST /api/gmail/reprocess + pipeline.reprocess_gmail_window.

Täcker:
- Hittar Gmail-mail i fönstret som inte finns i ProcessedMessage
- Hoppar över message_id som redan har en ProcessedMessage-rad
- Hanterar pipeline-fel per mail utan att krascha hela körningen
- vendor_filter syns i Gmail-queryn (from:/subject:)
"""

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


class GmailReprocessEndpointTest(unittest.TestCase):
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

        # pipeline.session_scope används internt av reprocess_gmail_window
        from app.services import pipeline as pipeline_module
        pipeline_module.session_scope = session_scope

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
        cls.pipeline_module = pipeline_module
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

    # --- Helpers --------------------------------------------------------

    def _seed_processed(self, message_id: str, **kwargs) -> None:
        with self.SessionLocal() as db:
            db.add(self.ProcessedMessage(
                message_id=message_id,
                status=kwargs.pop("status", "saved"),
                **kwargs,
            ))
            db.commit()

    # --- Tests ----------------------------------------------------------

    def test_reprocess_finds_unprocessed_mails(self):
        """Mail som finns i Gmail men inte i DB → pipelinen körs för dem."""
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = [
            "gm-new-1", "gm-new-2",
        ]
        fake_drive = MagicMock()

        calls: list[str] = []

        def fake_process(mid, gmail, drive, namer, analyzer, bezala, result,
                         **kwargs):
            calls.append(mid)
            result.processed += 1

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=fake_drive,
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer", return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/gmail/reprocess",
                json={"days": 30},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["found"], 2)
        self.assertEqual(body["processed"], 2)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(set(calls), {"gm-new-1", "gm-new-2"})
        self.assertIn("after:", body["query"])

    def test_reprocess_skips_already_processed(self):
        """message_id som redan finns i ProcessedMessage hoppas över."""
        self._seed_processed("gm-old-1", status="saved", file_name="x.pdf")
        self._seed_processed("gm-old-2", status="skipped:no_pdf")

        fake_gmail = MagicMock()
        # Gmail returnerar tre IDs, två är redan processade
        fake_gmail.list_candidate_message_ids.return_value = [
            "gm-old-1", "gm-old-2", "gm-new-3",
        ]

        called_with: list[str] = []

        def fake_process(mid, *a, **kw):
            called_with.append(mid)
            kw_result = a[5] if len(a) > 5 else kw.get("result")
            # _process_one_message signature: positional args result is 7th
            # (mid, gmail, drive, namer, analyzer, bezala, result)
            # Find result obj — it's positional arg index 6
            result = a[5]  # 0:gmail 1:drive 2:namer 3:analyzer 4:bezala 5:result
            result.processed += 1

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer", return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/gmail/reprocess",
                json={"days": 30},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["found"], 1)
        self.assertEqual(body["processed"], 1)
        self.assertEqual(called_with, ["gm-new-3"])
        # candidates_total inkluderar redan-processade så användaren ser
        # att Gmail-sökningen faktiskt fungerade.
        self.assertEqual(body["candidates_total"], 3)

    def test_reprocess_handles_failures_gracefully(self):
        """Om _process_one_message kastar för ett mail → failed++ men
        loopen fortsätter med resterande mail."""
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = [
            "gm-ok-1", "gm-fail-2", "gm-ok-3",
        ]

        def fake_process(mid, *a, **kw):
            if mid == "gm-fail-2":
                raise RuntimeError("simulerat pipeline-krasch")
            result = a[5]
            result.processed += 1

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer", return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/gmail/reprocess",
                json={"days": 30},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["found"], 3)
        self.assertEqual(body["processed"], 2)
        self.assertEqual(body["failed"], 1)
        # _log_error har sparat en error-rad för gm-fail-2
        with self.SessionLocal() as db:
            err_row = (
                db.query(self.ProcessedMessage)
                .filter_by(message_id="gm-fail-2")
                .first()
            )
            self.assertIsNotNone(err_row)
            self.assertEqual(err_row.status, "error")
            self.assertIn("simulerat", (err_row.error_message or ""))
        # details-listan har en entry per mail
        outcomes = {d["message_id"]: d["outcome"] for d in body["details"]}
        self.assertEqual(outcomes["gm-fail-2"], "error")
        self.assertEqual(outcomes["gm-ok-1"], "processed")

    def test_reprocess_respects_vendor_filter(self):
        """vendor_filter ska hamna i Gmail-queryn (from:/subject:)."""
        fake_gmail = MagicMock()
        fake_gmail.list_candidate_message_ids.return_value = []

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ):
            resp = self.client.post(
                "/api/gmail/reprocess",
                json={"days": 14, "vendor_filter": "lovable"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        # Inga kandidater → found=0, men queryn har byggts upp korrekt
        self.assertEqual(body["found"], 0)
        # Gmail kallades med den färdiga queryn
        self.assertTrue(fake_gmail.list_candidate_message_ids.called)
        call_kwargs = fake_gmail.list_candidate_message_ids.call_args.kwargs
        query = call_kwargs.get("query") or ""
        self.assertIn("from:lovable", query)
        self.assertIn("subject:lovable", query)
        self.assertIn("after:", query)
        # Och i svaret också så frontend kan visa
        self.assertIn("lovable", body["query"])

    def test_reprocess_validates_days_range(self):
        """days måste vara 1–365."""
        resp = self.client.post("/api/gmail/reprocess", json={"days": 0})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/gmail/reprocess", json={"days": 366})
        self.assertEqual(resp.status_code, 400)

    def test_reprocess_validates_max_results_range(self):
        """max_results måste vara 1–500."""
        resp = self.client.post(
            "/api/gmail/reprocess", json={"days": 30, "max_results": 0},
        )
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(
            "/api/gmail/reprocess", json={"days": 30, "max_results": 501},
        )
        self.assertEqual(resp.status_code, 400)

    def test_reprocess_gmail_init_error_returns_payload(self):
        """Om Gmail-klienten inte kan initialiseras returnerar vi 200 med
        error-fält (samma mönster som andra reprocess-endpoints)."""
        with patch.object(
            self.pipeline_module, "GmailClient",
            side_effect=RuntimeError("no token"),
        ):
            resp = self.client.post("/api/gmail/reprocess", json={"days": 30})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["found"], 0)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
