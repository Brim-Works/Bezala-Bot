"""Gate 5-tester: GET /api/messages/{id}/body + POST fetch-pdf-from-url +
HTML-sanitering + länkextraktion."""

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


# -------- Pure unit-tester --------


class SanitizeHtmlTest(unittest.TestCase):
    def test_removes_script_tags(self):
        from app.services.html_sanitizer import sanitize_html

        result = sanitize_html(
            "<p>Hello</p><script>alert('xss')</script><p>End</p>"
        )
        self.assertNotIn("<script", result.lower())
        self.assertNotIn("alert", result)
        self.assertIn("<p>Hello</p>", result)
        self.assertIn("<p>End</p>", result)

    def test_removes_style_tags(self):
        from app.services.html_sanitizer import sanitize_html

        result = sanitize_html("<style>body{display:none}</style><p>ok</p>")
        self.assertNotIn("<style", result.lower())
        self.assertNotIn("display:none", result)

    def test_strips_event_handlers(self):
        from app.services.html_sanitizer import sanitize_html

        result = sanitize_html('<img onerror="alert(1)" src="data:">')
        self.assertNotIn("onerror", result.lower())

    def test_neutralizes_javascript_urls(self):
        from app.services.html_sanitizer import sanitize_html

        result = sanitize_html('<a href="javascript:alert(1)">link</a>')
        self.assertNotIn("javascript:alert", result)

    def test_replaces_external_image_src(self):
        from app.services.html_sanitizer import sanitize_html

        result = sanitize_html(
            '<img src="https://tracker.example.com/pixel.gif">'
        )
        # Src finns kvar men pekar på data:-placeholder
        self.assertNotIn("https://tracker.example.com", result)
        self.assertIn("src=", result)


class ExtractLinksTest(unittest.TestCase):
    def test_extracts_http_links(self):
        from app.services.html_sanitizer import extract_links

        html = (
            '<a href="https://arlandaexpress.se/receipt/abc">Hämta kvitto</a>'
            '<a href="http://example.com/other">Annan</a>'
        )
        links = extract_links(html)
        self.assertEqual(len(links), 2)
        self.assertEqual(links[0]["href"], "https://arlandaexpress.se/receipt/abc")
        self.assertEqual(links[0]["text"], "Hämta kvitto")

    def test_skips_mailto_javascript_fragments(self):
        from app.services.html_sanitizer import extract_links

        html = (
            '<a href="mailto:foo@bar.com">mail</a>'
            '<a href="javascript:alert(1)">js</a>'
            '<a href="#anchor">fragment</a>'
            '<a href="https://ok.example/x">ok</a>'
        )
        links = extract_links(html)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["href"], "https://ok.example/x")

    def test_dedupes_identical_hrefs(self):
        from app.services.html_sanitizer import extract_links

        html = (
            '<a href="https://example.com/a">A</a>'
            '<a href="https://example.com/a">A</a>'
        )
        links = extract_links(html)
        self.assertEqual(len(links), 1)


# -------- Endpoint-tester --------


class MailPreviewEndpointTest(unittest.TestCase):
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

        app_module.app.dependency_overrides[app_module.require_auth] = fake_require_auth
        cls.client = TestClient(app_module.app)
        cls.app_module = app_module
        cls.SessionLocal = SessionLocal
        cls.ProcessedMessage = ProcessedMessage

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _seed(self, **overrides) -> int:
        defaults = dict(
            message_id="gm-preview-1",
            sender="noreply@arlandaexpress.se",
            subject="Din resa",
            status="needs_manual_download",
            pending_link="https://arlandaexpress.se/receipt/abc",
        )
        defaults.update(overrides)
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(**defaults)
            db.add(row)
            db.flush()
            mid = row.id
            db.commit()
        return mid

    def test_get_body_returns_sanitized_html_and_links(self):
        mid = self._seed()

        from app.services.gmail_client import GmailMessage

        fake_msg = GmailMessage(
            message_id="gm-preview-1",
            thread_id="t-1",
            sender="noreply@arlandaexpress.se",
            subject="Din resa",
            received_at=None,
            snippet="",
            body_html=(
                '<script>alert(1)</script>'
                '<style>body{display:none}</style>'
                '<p>Klicka här: <a href="https://arlandaexpress.se/r/abc">'
                'Kvitto</a></p>'
                '<img onerror="x" src="https://tracker/pixel">'
            ),
            body_text="Klicka här: https://arlandaexpress.se/r/abc",
        )

        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = fake_msg

        with patch.object(self.app_module, "GmailClient", return_value=fake_gmail):
            resp = self.client.get(f"/api/messages/{mid}/body")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertNotIn("<script", body["html"].lower())
        self.assertNotIn("alert(1)", body["html"])
        self.assertNotIn("onerror", body["html"].lower())
        self.assertIn("<p>", body["html"])
        self.assertEqual(len(body["links"]), 1)
        self.assertEqual(body["links"][0]["href"], "https://arlandaexpress.se/r/abc")

    def test_get_body_404_for_missing_message(self):
        resp = self.client.get("/api/messages/99999/body")
        self.assertEqual(resp.status_code, 404)

    def test_fetch_pdf_from_url_happy_path(self):
        """POST fetch-pdf-from-url med giltig PDF-URL → raden lyfts till saved."""
        mid = self._seed()

        pdf_bytes = b"%PDF-1.4\nfejk"
        fake_upload = MagicMock()
        fake_upload.file_id = "drive-xyz"
        fake_upload.web_view_link = "https://drive/drive-xyz"
        fake_drive = MagicMock()
        fake_drive.upload_pdf.return_value = fake_upload

        fake_analyzer = MagicMock()
        fake_analyzer.enabled = False

        fake_gmail = MagicMock()

        with patch.object(self.app_module, "_fetch_pdf_helper", return_value=pdf_bytes), \
             patch.object(self.app_module, "DriveClient", return_value=fake_drive), \
             patch.object(self.app_module, "ReceiptAnalyzer", return_value=fake_analyzer), \
             patch.object(self.app_module, "GmailClient", return_value=fake_gmail):
            resp = self.client.post(
                f"/api/messages/{mid}/fetch-pdf-from-url",
                json={"url": "https://arlandaexpress.se/r/abc"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "saved")
        self.assertEqual(body["drive_file_id"], "drive-xyz")
        self.assertIsNone(body["pending_link"])

    def test_fetch_pdf_from_url_rejects_non_pdf(self):
        """HTML-respons → 422 med tydligt meddelande."""
        from app.services.link_fetcher import LinkFetchError

        mid = self._seed()

        def raise_html(_url):
            raise LinkFetchError(
                "Länken gav text/html istället för PDF. Öppna länken manuellt."
            )

        with patch.object(self.app_module, "_fetch_pdf_helper", side_effect=raise_html):
            resp = self.client.post(
                f"/api/messages/{mid}/fetch-pdf-from-url",
                json={"url": "https://arlandaexpress.se/landing"},
            )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("pdf", resp.json()["detail"].lower())

    def test_fetch_pdf_from_url_ssrf_blocked(self):
        """localhost-URL → 400 (SSRF-skydd via link_fetcher)."""
        from app.services.link_fetcher import LinkFetchError

        mid = self._seed()

        def raise_ssrf(_url):
            raise LinkFetchError("Host 127.0.0.1 är blockerad (SSRF-skydd)")

        with patch.object(self.app_module, "_fetch_pdf_helper", side_effect=raise_ssrf):
            resp = self.client.post(
                f"/api/messages/{mid}/fetch-pdf-from-url",
                json={"url": "http://127.0.0.1/evil.pdf"},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("blockerad", resp.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
