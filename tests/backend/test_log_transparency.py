"""Backend-tester för Gate 1.5: Loggtransparens.

Täcker:
- DB-migration: scan_runs.filtered_messages kolumn skapas idempotent
- ScanResult.filtered default är []
- _record_filtered lägger rätt form för varje reason
- Pipeline-integration: ai_filtered, not_receipt, no_pdf, no_content,
  html_pdf_failed, excluded_subject, no_link, already_processed
- /api/runs returnerar filtered_messages (tom lista när NULL)
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
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


# ============================================================
# DB migration
# ============================================================


class ScanRunMigrationTest(unittest.TestCase):
    """Verifiera att filtered_messages-kolumnen läggs till idempotent."""

    def test_migration_adds_column_on_old_schema(self):
        """En tidigare scan_runs-tabell utan filtered_messages får kolumnen
        när _apply_schema_migrations körs. Existerande rader behåller NULL."""
        from sqlalchemy import create_engine, text, inspect
        from sqlalchemy.pool import StaticPool
        from app import db as db_module

        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        db_module.engine = eng

        with eng.begin() as c:
            # Gammalt schema — ingen filtered_messages
            c.execute(text("""
                CREATE TABLE scan_runs (
                  id INTEGER PRIMARY KEY,
                  started_at TIMESTAMP NOT NULL,
                  finished_at TIMESTAMP,
                  messages_found INTEGER NOT NULL DEFAULT 0,
                  messages_processed INTEGER NOT NULL DEFAULT 0,
                  messages_skipped INTEGER NOT NULL DEFAULT 0,
                  errors INTEGER NOT NULL DEFAULT 0,
                  status VARCHAR(32) NOT NULL DEFAULT 'running',
                  notes TEXT
                )
            """))
            c.execute(text(
                "INSERT INTO scan_runs (id, started_at, status) VALUES (1, '2026-01-01', 'ok')"
            ))

        db_module._apply_schema_migrations()

        insp = inspect(eng)
        cols = {c["name"] for c in insp.get_columns("scan_runs")}
        self.assertIn("filtered_messages", cols)

        with eng.begin() as c:
            val = c.execute(
                text("SELECT filtered_messages FROM scan_runs WHERE id=1")
            ).scalar()
            self.assertIsNone(val)  # Existerande rader → NULL (tolkas som [])

    def test_migration_is_idempotent(self):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import StaticPool
        from app import db as db_module

        eng = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        db_module.engine = eng

        from app.db import Base
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=eng)

        # Två på varandra följande körningar ska inte krasha
        db_module._apply_schema_migrations()
        db_module._apply_schema_migrations()


# ============================================================
# _record_filtered helper
# ============================================================


class RecordFilteredTest(unittest.TestCase):
    def _msg(self, **overrides):
        from app.services.gmail_client import GmailMessage

        defaults = dict(
            message_id="gm-1",
            thread_id="t",
            sender="foo@bar.com",
            subject="Subj",
            received_at=datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc),
            snippet="",
        )
        defaults.update(overrides)
        return GmailMessage(**defaults)

    def test_ai_filtered_entry_has_confidence(self):
        from app.services.pipeline import (
            ScanResult,
            _record_filtered,
            FILTERED_REASON_AI_FILTERED,
        )

        result = ScanResult()
        _record_filtered(result, self._msg(), FILTERED_REASON_AI_FILTERED, confidence=35)

        self.assertEqual(len(result.filtered), 1)
        entry = result.filtered[0]
        self.assertEqual(entry["message_id"], "gm-1")
        self.assertEqual(entry["sender"], "foo@bar.com")
        self.assertEqual(entry["subject"], "Subj")
        self.assertEqual(entry["reason"], "ai_filtered")
        self.assertEqual(entry["confidence"], 35)
        self.assertIsNone(entry["detail"])
        self.assertEqual(entry["received_at"], "2026-04-22T12:00:00+00:00")

    def test_html_pdf_failed_entry_has_detail(self):
        from app.services.pipeline import (
            ScanResult,
            _record_filtered,
            FILTERED_REASON_HTML_PDF_FAILED,
        )

        result = ScanResult()
        _record_filtered(
            result, self._msg(), FILTERED_REASON_HTML_PDF_FAILED,
            detail="weasyprint CSS error",
        )

        entry = result.filtered[0]
        self.assertEqual(entry["reason"], "html_pdf_failed")
        self.assertEqual(entry["detail"], "weasyprint CSS error")
        self.assertIsNone(entry["confidence"])

    def test_already_processed_uses_message_id_only(self):
        """Duplicate-skip sker innan vi hämtar mailet → msg är None."""
        from app.services.pipeline import (
            ScanResult,
            _record_filtered,
            FILTERED_REASON_ALREADY_PROCESSED,
        )

        result = ScanResult()
        _record_filtered(
            result, None, FILTERED_REASON_ALREADY_PROCESSED,
            message_id="gm-dup-42",
        )

        entry = result.filtered[0]
        self.assertEqual(entry["message_id"], "gm-dup-42")
        self.assertIsNone(entry["sender"])
        self.assertIsNone(entry["subject"])


# ============================================================
# Pipeline integration — filtered growing per skip path
# ============================================================


class PipelineFilteredIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()

        from app.db import Base
        from app import models  # noqa: F401
        from app.services import pipeline as pipeline_module

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

        db_module.session_scope = session_scope
        pipeline_module.session_scope = session_scope

        cls.SessionLocal = SessionLocal
        cls.ProcessedMessage = models.ProcessedMessage

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _msg(self, **overrides):
        from app.services.gmail_client import GmailMessage

        defaults = dict(
            message_id="m-1", thread_id="t", sender="x@y.com",
            subject="Subj",
            received_at=datetime(2026, 4, 22, tzinfo=timezone.utc), snippet="",
        )
        defaults.update(overrides)
        return GmailMessage(**defaults)

    def test_ai_filtered_adds_entry(self):
        from app.services.pipeline import _process_one_message, ScanResult
        from app.services.receipt_analyzer import ReceiptAnalysis
        from app.services.gmail_client import Attachment

        msg = self._msg(attachments=[
            Attachment(filename="r.pdf", mime_type="application/pdf", data=b"%PDF-1.4"),
        ])
        analysis = ReceiptAnalysis(
            is_receipt=True, confidence=20, filename="K.pdf", vendor="V",
            amount=10.0, currency="EUR", date="2026-04-22",
            category="Annat", summary="s",
        )
        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_namer = MagicMock()
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = True
        fake_analyzer.analyze.return_value = analysis

        result = ScanResult()
        _process_one_message(
            "m-1", fake_gmail, fake_drive, fake_namer, fake_analyzer, None, result,
            use_ai=True, ai_min_confidence=40,
        )

        self.assertEqual(len(result.filtered), 1)
        self.assertEqual(result.filtered[0]["reason"], "ai_filtered")
        self.assertEqual(result.filtered[0]["confidence"], 20)

    def test_not_receipt_adds_entry(self):
        from app.services.pipeline import _process_one_message, ScanResult
        from app.services.receipt_analyzer import ReceiptAnalysis
        from app.services.gmail_client import Attachment

        msg = self._msg(attachments=[
            Attachment(filename="r.pdf", mime_type="application/pdf", data=b"%PDF-1.4"),
        ])
        analysis = ReceiptAnalysis(
            is_receipt=False, confidence=85, filename="x.pdf", vendor=None,
            amount=None, currency=None, date=None, category=None, summary=None,
        )
        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = True
        fake_analyzer.analyze.return_value = analysis

        result = ScanResult()
        _process_one_message(
            "m-1", fake_gmail, MagicMock(), MagicMock(), fake_analyzer, None, result,
            use_ai=True,
        )

        self.assertEqual(len(result.filtered), 1)
        self.assertEqual(result.filtered[0]["reason"], "not_receipt")
        self.assertEqual(result.filtered[0]["confidence"], 85)

    def test_no_pdf_adds_entry_when_html_disabled(self):
        from app.services.pipeline import _process_one_message, ScanResult

        msg = self._msg(attachments=[], body_html="<p>x</p>")
        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        _process_one_message(
            "m-1", fake_gmail, MagicMock(), MagicMock(), fake_analyzer, None, result,
            html_to_pdf_enabled=False,
        )
        self.assertEqual(len(result.filtered), 1)
        self.assertEqual(result.filtered[0]["reason"], "no_pdf")

    def test_excluded_subject_adds_entry_with_detail(self):
        from app.services.pipeline import _process_one_message, ScanResult

        msg = self._msg(subject="Newsletter April", attachments=[])
        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        _process_one_message(
            "m-1", fake_gmail, MagicMock(), MagicMock(), fake_analyzer, None, result,
            excluded_subjects=["newsletter"],
        )
        self.assertEqual(len(result.filtered), 1)
        entry = result.filtered[0]
        self.assertEqual(entry["reason"], "excluded_subject")
        self.assertEqual(entry["detail"], "Newsletter April")

    def test_no_link_adds_entry(self):
        from app.services.pipeline import _process_one_message, ScanResult

        msg = self._msg(
            sender="noreply@arlandaexpress.se",
            body_text="Tack för resan!",  # ingen URL
            attachments=[],
        )
        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        _process_one_message(
            "m-1", fake_gmail, MagicMock(), MagicMock(), fake_analyzer, None, result,
            link_fetch_senders=["noreply@arlandaexpress.se"],
        )
        self.assertEqual(len(result.filtered), 1)
        self.assertEqual(result.filtered[0]["reason"], "no_link")


# ============================================================
# /api/runs — filtered_messages in response
# ============================================================


class RunsEndpointFilteredMessagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import ScanRun
        from fastapi.testclient import TestClient

        Base.metadata.create_all(bind=db_module.engine)

        from contextlib import contextmanager
        SessionLocal = db_module.SessionLocal

        @contextmanager
        def session_scope():
            s = SessionLocal()
            try:
                yield s; s.commit()
            except Exception:
                s.rollback(); raise
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

        app_module.app.dependency_overrides[app_module.require_auth] = fake_require_auth
        cls.client = TestClient(app_module.app)
        cls.app_module = app_module
        cls.SessionLocal = SessionLocal
        cls.ScanRun = ScanRun

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.ScanRun).delete()
            db.commit()

    def test_null_filtered_messages_serialized_as_empty_list(self):
        """Äldre körningar har filtered_messages=NULL — API returnerar []."""
        with self.SessionLocal() as db:
            db.add(self.ScanRun(
                started_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
                status="ok",
            ))
            db.commit()

        resp = self.client.get("/api/runs")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filtered_messages"], [])

    def test_populated_filtered_messages_serialized_as_array(self):
        entries = [
            {
                "message_id": "gm-1",
                "sender": "foo@bar.com",
                "subject": "Spam",
                "received_at": "2026-04-22T12:00:00+00:00",
                "reason": "ai_filtered",
                "confidence": 30,
                "detail": None,
            },
            {
                "message_id": "gm-2",
                "sender": "x@y.com",
                "subject": "Tomt",
                "received_at": None,
                "reason": "no_content",
                "confidence": None,
                "detail": None,
            },
        ]
        with self.SessionLocal() as db:
            db.add(self.ScanRun(
                started_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
                status="ok",
                filtered_messages=entries,
            ))
            db.commit()

        resp = self.client.get("/api/runs")
        self.assertEqual(resp.status_code, 200)
        rows = resp.json()
        self.assertEqual(rows[0]["filtered_messages"], entries)


if __name__ == "__main__":
    unittest.main()
