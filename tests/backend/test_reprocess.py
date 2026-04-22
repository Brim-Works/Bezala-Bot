"""Backend-tester för Gate 2: POST /api/messages/{id}/reprocess.

Täcker:
- skipped:* status → 200, raden raderas, bakgrundsscan triggas
- needs_manual_download status → 200
- saved-rad → 400 (får inte reprocessas)
- 404 vid icke-existerande msg_id
- Gmail mark_done-etikett tas bort best-effort
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


class ReprocessEndpointTest(unittest.TestCase):
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

    def _seed(self, **overrides) -> int:
        defaults = dict(
            message_id="gm-skip-1",
            sender="noreply@example.com",
            subject="Tomt mail",
            status="skipped:no_pdf",
        )
        defaults.update(overrides)
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(**defaults)
            db.add(row)
            db.flush()
            mid = row.id
            db.commit()
        return mid

    def test_reprocess_skipped_row_deletes_and_triggers_scan(self):
        """skipped:no_pdf → 200, raden raderas, run_scan kallas en gång."""
        mid = self._seed(status="skipped:no_pdf")

        fake_gmail = MagicMock()

        with patch.object(self.app_module, "_get_gmail_client_safe", return_value=fake_gmail), \
             patch.object(self.app_module, "run_scan") as mock_scan:
            resp = self.client.post(f"/api/messages/{mid}/reprocess")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "reprocessing")
        self.assertEqual(body["prior_status"], "skipped:no_pdf")

        # Raden är raderad
        with self.SessionLocal() as db:
            self.assertIsNone(
                db.query(self.ProcessedMessage).filter_by(id=mid).first()
            )

        # Gmail-etiketten togs bort best-effort
        fake_gmail.remove_done.assert_called_once_with("gm-skip-1")

        # Bakgrundsscan triggades (BackgroundTasks kör efter response i TestClient)
        mock_scan.assert_called_once()
        self.assertEqual(mock_scan.call_args.kwargs.get("max_results"), 10)

    def test_reprocess_needs_manual_download_row(self):
        """needs_manual_download är också en 'hoppad'-variant i UI → tillåts."""
        mid = self._seed(status="needs_manual_download")

        with patch.object(self.app_module, "_get_gmail_client_safe", return_value=None), \
             patch.object(self.app_module, "run_scan"):
            resp = self.client.post(f"/api/messages/{mid}/reprocess")

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["prior_status"], "needs_manual_download")

    def test_reprocess_saved_row_returns_400(self):
        """Sparad rad får INTE reprocessas — det vore destruktivt."""
        mid = self._seed(status="saved", drive_file_id="drv-1", file_name="x.pdf")

        with patch.object(self.app_module, "_get_gmail_client_safe", return_value=None), \
             patch.object(self.app_module, "run_scan") as mock_scan:
            resp = self.client.post(f"/api/messages/{mid}/reprocess")

        self.assertEqual(resp.status_code, 400)
        self.assertIn("skipped", resp.json()["detail"].lower())
        # Raden ska INTE ha raderats
        with self.SessionLocal() as db:
            self.assertIsNotNone(
                db.query(self.ProcessedMessage).filter_by(id=mid).first()
            )
        mock_scan.assert_not_called()

    def test_reprocess_error_row_returns_400(self):
        """Error-rader hanteras via 'Rensa fel' — inte reprocess."""
        mid = self._seed(status="error")

        with patch.object(self.app_module, "_get_gmail_client_safe", return_value=None), \
             patch.object(self.app_module, "run_scan"):
            resp = self.client.post(f"/api/messages/{mid}/reprocess")
        self.assertEqual(resp.status_code, 400)

    def test_reprocess_missing_row_returns_404(self):
        resp = self.client.post("/api/messages/99999/reprocess")
        self.assertEqual(resp.status_code, 404)

    def test_reprocess_when_gmail_client_fails_still_succeeds(self):
        """Gmail-klienten kan inte initialiseras → vi tar bort raden ändå
        och triggar bakgrundsscan. Etiketten kan städas manuellt senare."""
        mid = self._seed(status="skipped:html_pdf_failed")

        with patch.object(self.app_module, "_get_gmail_client_safe", return_value=None), \
             patch.object(self.app_module, "run_scan") as mock_scan:
            resp = self.client.post(f"/api/messages/{mid}/reprocess")

        self.assertEqual(resp.status_code, 200, resp.text)
        with self.SessionLocal() as db:
            self.assertIsNone(
                db.query(self.ProcessedMessage).filter_by(id=mid).first()
            )
        mock_scan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
