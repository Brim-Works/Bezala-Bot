"""Backend-tester för Gate 1: HTML→PDF konvertering.

Täcker:
- html_to_pdf returnerar giltig PDF (b'%PDF')
- Mail utan bilaga + html_body → sparas (inte hoppat)
- Mail utan bilaga + html_to_pdf_enabled=False → skippas (no_pdf)
- link_fetch_senders-mail → HTML-konvertering körs INTE
- Mail utan bilaga + utan body → skipped:no_content
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


class HtmlToPdfConverterTest(unittest.TestCase):
    """html_to_pdf returnerar bytes som börjar med b'%PDF'."""

    def test_html_string_produces_valid_pdf(self):
        from app.services.html_pdf_converter import html_to_pdf

        pdf = html_to_pdf("<html><body><h1>Kvitto</h1><p>EUR 50</p></body></html>")
        self.assertIsInstance(pdf, bytes)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 200)

    def test_falls_back_to_plain_text(self):
        from app.services.html_pdf_converter import html_to_pdf

        pdf = html_to_pdf(None, plain_text_fallback="Tack för köpet!\nBelopp: 19.99 EUR")
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_empty_input_raises(self):
        from app.services.html_pdf_converter import HtmlToPdfError, html_to_pdf

        with self.assertRaises(HtmlToPdfError):
            html_to_pdf("", plain_text_fallback="")


class PipelineHtmlToPdfTest(unittest.TestCase):
    """Verifiera pipeline-grenen som synthesizar PDF från mail-body."""

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

    def _build_msg(self, *, mid="mail-1", body_html="", body_text="", attachments=None):
        from app.services.gmail_client import GmailMessage

        return GmailMessage(
            message_id=mid,
            thread_id="t",
            sender="kvitto@moovy.fi",
            subject="Din resa Moovy",
            received_at=datetime(2026, 4, 21, tzinfo=timezone.utc),
            snippet="",
            attachments=attachments or [],
            body_html=body_html,
            body_text=body_text,
        )

    def test_mail_without_pdf_with_html_body_is_saved(self):
        """HTML-body finns + html_to_pdf_enabled=True → konverteras + sparas."""
        from app.services.pipeline import _process_one_message, ScanResult

        msg = self._build_msg(
            body_html="<html><body><h1>Moovy-kvitto</h1><p>Pris: 8,40 EUR</p></body></html>",
        )

        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_upload = MagicMock()
        fake_upload.file_id = "drv-html-1"
        fake_upload.web_view_link = "https://drive/drv-html-1"
        fake_drive.upload_pdf.return_value = fake_upload
        fake_drive.filename_exists.return_value = False
        fake_namer = MagicMock()
        fake_namer.name_for.return_value = "20260421 Moovy Kvitto.pdf"
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        _process_one_message(
            "mail-1",
            fake_gmail,
            fake_drive,
            fake_namer,
            fake_analyzer,
            None,
            result,
            html_to_pdf_enabled=True,
        )

        # PDF ska ha laddats upp till Drive
        fake_drive.upload_pdf.assert_called_once()
        upload_args = fake_drive.upload_pdf.call_args[0]
        self.assertEqual(upload_args[0], "20260421 Moovy Kvitto.pdf")
        # bytes-arg börjar med %PDF
        self.assertTrue(upload_args[1].startswith(b"%PDF"))

        # Raden i DB
        with self.SessionLocal() as db:
            row = (
                db.query(self.ProcessedMessage)
                .filter_by(message_id="mail-1")
                .first()
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "saved")
        self.assertEqual(result.processed, 1)
        fake_gmail.mark_done.assert_called_once_with("mail-1")

    def test_mail_without_pdf_html_disabled_is_skipped_as_no_pdf(self):
        """html_to_pdf_enabled=False → original beteende: skipped:no_pdf."""
        from app.services.pipeline import _process_one_message, ScanResult

        msg = self._build_msg(body_html="<p>Något kvitto</p>")

        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_namer = MagicMock()
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        _process_one_message(
            "mail-1",
            fake_gmail,
            fake_drive,
            fake_namer,
            fake_analyzer,
            None,
            result,
            html_to_pdf_enabled=False,
        )

        fake_drive.upload_pdf.assert_not_called()
        fake_gmail.mark_done.assert_called_once_with("mail-1")
        self.assertEqual(result.skipped, 1)
        with self.SessionLocal() as db:
            row = (
                db.query(self.ProcessedMessage)
                .filter_by(message_id="mail-1")
                .first()
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "skipped:no_pdf")

    def test_link_fetch_sender_does_not_trigger_html_pdf(self):
        """Avsändare i link_fetch_senders → HTML-konvertering körs INTE
        även om html_to_pdf_enabled=True. Link-fetch-grenen vinner."""
        from app.services.pipeline import _process_one_message, ScanResult

        msg = self._build_msg(
            mid="link-1",
            body_html="<p>Hämta kvitto: https://arlandaexpress.se/receipt/abcdefg-token-1234567</p>",
            body_text="Hämta kvitto: https://arlandaexpress.se/receipt/abcdefg-token-1234567",
        )
        # Override sender till en länk-fetch-avsändare
        msg.sender = "noreply@arlandaexpress.se"

        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_namer = MagicMock()
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        _process_one_message(
            "link-1",
            fake_gmail,
            fake_drive,
            fake_namer,
            fake_analyzer,
            None,
            result,
            link_fetch_senders=["noreply@arlandaexpress.se"],
            html_to_pdf_enabled=True,
        )

        # Drive får INTE röras — link-fetch-grenen sparar bara needs_manual_download
        fake_drive.upload_pdf.assert_not_called()
        with self.SessionLocal() as db:
            row = (
                db.query(self.ProcessedMessage)
                .filter_by(message_id="link-1")
                .first()
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "needs_manual_download")

    def test_mail_without_pdf_or_body_skipped_as_no_content(self):
        """Inget body → skipped:no_content (inte no_pdf)."""
        from app.services.pipeline import _process_one_message, ScanResult

        msg = self._build_msg(mid="empty-1", body_html="", body_text="")

        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_namer = MagicMock()
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        _process_one_message(
            "empty-1",
            fake_gmail,
            fake_drive,
            fake_namer,
            fake_analyzer,
            None,
            result,
            html_to_pdf_enabled=True,
        )

        fake_drive.upload_pdf.assert_not_called()
        fake_gmail.mark_done.assert_called_once_with("empty-1")
        self.assertEqual(result.skipped, 1)
        with self.SessionLocal() as db:
            row = (
                db.query(self.ProcessedMessage)
                .filter_by(message_id="empty-1")
                .first()
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "skipped:no_content")

    def test_html_pdf_conversion_failure_is_skipped(self):
        """Om weasyprint kraschar → skipped:html_pdf_failed, inget Drive-anrop."""
        from app.services.pipeline import _process_one_message, ScanResult
        from app.services.html_pdf_converter import HtmlToPdfError

        msg = self._build_msg(mid="fail-1", body_html="<p>kvitto</p>")

        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_namer = MagicMock()
        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        result = ScanResult()
        with patch(
            "app.services.pipeline.html_to_pdf",
            side_effect=HtmlToPdfError("simulated"),
        ):
            _process_one_message(
                "fail-1",
                fake_gmail,
                fake_drive,
                fake_namer,
                fake_analyzer,
                None,
                result,
                html_to_pdf_enabled=True,
            )

        fake_drive.upload_pdf.assert_not_called()
        fake_gmail.mark_done.assert_called_once_with("fail-1")
        self.assertEqual(result.skipped, 1)
        with self.SessionLocal() as db:
            row = (
                db.query(self.ProcessedMessage)
                .filter_by(message_id="fail-1")
                .first()
            )
            self.assertIsNotNone(row)
            self.assertEqual(row.status, "skipped:html_pdf_failed")


if __name__ == "__main__":
    unittest.main()
