"""FAS 8.5 — Travel Tinder Matchade-vy.

Tester för:
- GET /api/bezala/matched-pairs (period/search-filter, stats)
- POST /api/bezala/unmatch/{message_id}
- match-to-bezala sätter matched_at
- matched_at-migrationen är idempotent
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "")
os.environ.setdefault("DRIVE_REFRESH_TOKEN", "")
os.environ.setdefault("BEZALA_USERNAME", "x")
os.environ.setdefault("BEZALA_PASSWORD", "x")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

PDF_BYTES = b"%PDF-1.4\nfake"


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
        bind=db_module.engine, autoflush=False, autocommit=False,
    )
    return db_module


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import ProcessedMessage
        from fastapi.testclient import TestClient
        from contextlib import contextmanager

        Base.metadata.create_all(bind=db_module.engine)
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

    def _seed(self, **over):
        defaults = dict(
            message_id="m-1",
            sender="x@x", subject="s",
            status="saved",
            file_name="m-1.pdf",
            drive_file_id="drv-1",
            drive_link="https://d/drv-1",
            vendor="Moovy",
            amount=73.49,
            currency="EUR",
            receipt_date="2026-04-14",
            ai_confidence=90,
            received_at=datetime(2026, 4, 14, 10),
            bezala_upload_status="pending",
        )
        defaults.update(over)
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(**defaults)
            db.add(row)
            db.flush()
            db.commit()


class MatchedPairsEndpointTest(_Base):
    def test_returns_only_coupled_messages(self):
        self._seed(message_id="m-coupled",
                   bezala_transaction_id="bz-1",
                   bezala_upload_status="success",
                   matched_at=datetime.utcnow())
        self._seed(message_id="m-uncoupled")  # ingen tx_id
        resp = self.client.get("/api/bezala/matched-pairs?period=all")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        ids = [p["message_id"] for p in body["pairs"]]
        self.assertEqual(ids, ["m-coupled"])

    def test_period_7d_filters_old_matches(self):
        self._seed(message_id="m-recent",
                   bezala_transaction_id="bz-1",
                   matched_at=datetime.utcnow() - timedelta(days=2))
        self._seed(message_id="m-old",
                   bezala_transaction_id="bz-2",
                   matched_at=datetime.utcnow() - timedelta(days=20))
        resp = self.client.get("/api/bezala/matched-pairs?period=7d")
        ids = [p["message_id"] for p in resp.json()["pairs"]]
        self.assertEqual(ids, ["m-recent"])

    def test_period_all_returns_everything(self):
        self._seed(message_id="m-recent",
                   bezala_transaction_id="bz-1",
                   matched_at=datetime.utcnow() - timedelta(days=2))
        # Legacy: bezala_transaction_id satt men matched_at = NULL
        self._seed(message_id="m-legacy",
                   bezala_transaction_id="bz-legacy",
                   matched_at=None)
        resp = self.client.get("/api/bezala/matched-pairs?period=all")
        ids = sorted(p["message_id"] for p in resp.json()["pairs"])
        self.assertEqual(ids, ["m-legacy", "m-recent"])

    def test_search_case_insensitive(self):
        self._seed(message_id="m-fin",
                   vendor="Finnair",
                   bezala_transaction_id="bz-1",
                   matched_at=datetime.utcnow())
        self._seed(message_id="m-moo",
                   vendor="Moovy",
                   bezala_transaction_id="bz-2",
                   matched_at=datetime.utcnow())
        resp = self.client.get("/api/bezala/matched-pairs?period=all&search=FINN")
        ids = [p["message_id"] for p in resp.json()["pairs"]]
        self.assertEqual(ids, ["m-fin"])

    def test_empty_search_param_ignored(self):
        self._seed(message_id="m-1",
                   bezala_transaction_id="bz-1",
                   matched_at=datetime.utcnow())
        resp = self.client.get("/api/bezala/matched-pairs?period=all&search=")
        self.assertEqual(len(resp.json()["pairs"]), 1)

    def test_stats_total_and_this_week(self):
        self._seed(message_id="m-recent",
                   bezala_transaction_id="bz-1",
                   matched_at=datetime.utcnow() - timedelta(days=2))
        self._seed(message_id="m-old",
                   bezala_transaction_id="bz-2",
                   matched_at=datetime.utcnow() - timedelta(days=14))
        resp = self.client.get("/api/bezala/matched-pairs?period=all")
        stats = resp.json()["stats"]
        self.assertEqual(stats["total_all_time"], 2)
        self.assertEqual(stats["this_week"], 1)
        # 2 par × 10 min
        self.assertEqual(stats["estimated_minutes_saved"], 20)

    def test_pair_includes_receipt_payload(self):
        self._seed(
            message_id="m-pair",
            bezala_transaction_id="bz-7",
            matched_at=datetime.utcnow(),
            vendor="Moovy", amount=73.49, currency="EUR",
            receipt_date="2026-04-14",
            file_name="moovy.pdf", drive_file_id="drv-7",
        )
        resp = self.client.get("/api/bezala/matched-pairs?period=all")
        pair = resp.json()["pairs"][0]
        self.assertEqual(pair["bezala_transaction_id"], "bz-7")
        self.assertEqual(pair["receipt"]["vendor"], "Moovy")
        self.assertEqual(pair["receipt"]["amount"], 73.49)
        self.assertEqual(pair["receipt"]["drive_file_id"], "drv-7")


class UnmatchEndpointTest(_Base):
    def test_unmatch_clears_fields(self):
        self._seed(message_id="m-1",
                   bezala_transaction_id="bz-9",
                   bezala_upload_status="success",
                   matched_at=datetime.utcnow())
        resp = self.client.post("/api/bezala/unmatch/m-1")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["old_bezala_transaction_id"], "bz-9")
        with self.SessionLocal() as db:
            row = db.query(self.ProcessedMessage).filter_by(message_id="m-1").first()
            self.assertIsNone(row.bezala_transaction_id)
            self.assertIsNone(row.matched_at)
            self.assertEqual(row.bezala_upload_status, "pending")

    def test_unmatch_unknown_message_returns_404(self):
        resp = self.client.post("/api/bezala/unmatch/missing")
        self.assertEqual(resp.status_code, 404)

    def test_unmatch_uncoupled_returns_400(self):
        self._seed(message_id="m-1")  # ingen tx_id
        resp = self.client.post("/api/bezala/unmatch/m-1")
        self.assertEqual(resp.status_code, 400)


class MatchToBezalaSetsMatchedAtTest(_Base):
    def test_match_to_bezala_sets_matched_at(self):
        """Regression: match-to-bezala-endpointen ska sätta matched_at."""
        self._seed(message_id="m-1",
                   file_name="m-1.pdf",
                   drive_file_id="drv-1")

        fake_drive = MagicMock()
        fake_drive.download_pdf.return_value = PDF_BYTES

        fake_bezala = MagicMock()
        fake_attach = MagicMock()
        fake_attach.attachment_id = "att-1"
        fake_bezala.attach_file.return_value = fake_attach

        # Hitta numerisk DB-id
        with self.SessionLocal() as db:
            mid = db.query(self.ProcessedMessage).filter_by(message_id="m-1").first().id

        with patch.object(self.app_module, "DriveClient", return_value=fake_drive), \
             patch.object(self.app_module, "BezalaClient", return_value=fake_bezala):
            resp = self.client.post(
                f"/api/messages/{mid}/match-to-bezala",
                json={"missing_receipt_id": 12345},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        with self.SessionLocal() as db:
            row = db.query(self.ProcessedMessage).filter_by(message_id="m-1").first()
            self.assertIsNotNone(row.matched_at)
            # Inom senaste minuten
            self.assertLess(
                (datetime.utcnow() - row.matched_at).total_seconds(), 60,
            )


class MigrationIdempotenceTest(unittest.TestCase):
    def test_apply_schema_migrations_runs_twice_without_error(self):
        """matched_at-kolumnen läggs till idempotent."""
        db_module = _configure_memory_engine()
        from app.db import _apply_schema_migrations, Base
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=db_module.engine)
        # Andra körningen ska inte krascha även om kolumnen redan finns
        _apply_schema_migrations()
        _apply_schema_migrations()


if __name__ == "__main__":
    unittest.main()
