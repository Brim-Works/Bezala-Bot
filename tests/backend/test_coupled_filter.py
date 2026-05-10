"""Bugfix — kopplade kvitton (bezala_transaction_id IS NOT NULL) får aldrig
visas som AI-match-förslag i /api/bezala/match-suggestions.

Reproduktion innan fix: efter att ett kvitto matchats till en korttrans
(bezala_transaction_id satt) dök det upp igen som AI-förslag för en annan
korttrans med liknande belopp. Bugg: query filtrerade bara på
bezala_upload_status != 'success' — bezala_transaction_id-fältet
ignorerades.

Vi belt-and-suspendersar: båda filtren tillämpas. Kopplade kvitton ska
fortfarande returneras i `all_messages` (Travel Tinder-läget) så de kan
visas grå.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
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


class CoupledReceiptsFilterTest(unittest.TestCase):
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
            sender="x@x",
            subject="Moovy parking",
            status="saved",
            file_name="moovy.pdf",
            drive_file_id="drv-x",
            vendor="Moovy",
            amount=73.49,
            currency="EUR",
            receipt_date="2026-04-14",
            ai_confidence=90,
            received_at=datetime(2026, 4, 14, 10, 0),
            # 'pending' är default-statusen rätt efter att en rad sparats
            # men innan användaren matchat. NULL skulle filtreras bort av
            # `!= 'success'` (NULL != X → NULL → FALSE i SQL).
            bezala_upload_status="pending",
        )
        defaults.update(over)
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(**defaults)
            db.add(row)
            db.flush()
            db.commit()

    def _missing_payload(self, amount=73.49, date_="2026-04-14"):
        return [{
            "id": 9999,
            "description": "MOOVY OY",
            "amount": amount,
            "currency": "EUR",
            "date": date_,
        }]

    # ---- Default-shape (utan include_all_messages) ----

    def test_coupled_excluded_from_suggestions_default_shape(self):
        """Kvitto med bezala_transaction_id IS NOT NULL får ALDRIG
        föreslås, även om bezala_upload_status inte hunnit bli 'success'."""
        self._seed(
            message_id="m-coupled",
            bezala_transaction_id="bz-1",
            bezala_upload_status="pending",  # OBS: inte 'success'!
        )
        fake = MagicMock()
        fake.list_missing_receipts.return_value = self._missing_payload()
        with patch.object(self.app_module, "BezalaClient", return_value=fake):
            resp = self.client.get("/api/bezala/match-suggestions")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        suggestions = body[0]["suggestions"]
        self.assertEqual(suggestions, [])

    def test_uncoupled_returned_as_suggestion(self):
        """Regression: ingen bezala_transaction_id, ingen success-status →
        kvittot visas som förslag."""
        self._seed(message_id="m-fresh")
        fake = MagicMock()
        fake.list_missing_receipts.return_value = self._missing_payload()
        with patch.object(self.app_module, "BezalaClient", return_value=fake):
            resp = self.client.get("/api/bezala/match-suggestions")
        body = resp.json()
        suggestions = body[0]["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["message"]["message_id"], "m-fresh")

    def test_two_uncoupled_one_coupled_only_uncoupled_proposed(self):
        """Två okopplade + ett kopplat med samma belopp → bara de
        okopplade föreslås."""
        self._seed(message_id="m-a")
        self._seed(message_id="m-b")
        self._seed(
            message_id="m-coupled",
            bezala_transaction_id="bz-7",
            bezala_upload_status="success",
        )
        fake = MagicMock()
        fake.list_missing_receipts.return_value = self._missing_payload()
        with patch.object(self.app_module, "BezalaClient", return_value=fake):
            resp = self.client.get("/api/bezala/match-suggestions")
        body = resp.json()
        ids = sorted(s["message"]["message_id"] for s in body[0]["suggestions"])
        self.assertEqual(ids, ["m-a", "m-b"])

    def test_uncoupling_re_includes_in_suggestions(self):
        """Edge case: kvitto var kopplat och blev frikopplat
        (bezala_transaction_id satt till NULL) → ska dyka upp som
        förslag igen."""
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(
                message_id="m-rec",
                sender="x@x", subject="Moovy",
                status="saved",
                file_name="moovy.pdf",
                drive_file_id="drv-rec",
                vendor="Moovy",
                amount=73.49,
                currency="EUR",
                receipt_date="2026-04-14",
                ai_confidence=90,
                received_at=datetime(2026, 4, 14),
                bezala_transaction_id=None,
                bezala_upload_status="pending",
            )
            db.add(row)
            db.commit()

        fake = MagicMock()
        fake.list_missing_receipts.return_value = self._missing_payload()
        with patch.object(self.app_module, "BezalaClient", return_value=fake):
            resp = self.client.get("/api/bezala/match-suggestions")
        suggestions = resp.json()[0]["suggestions"]
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["message"]["message_id"], "m-rec")

    # ---- Travel Tinder-shape (include_all_messages=true) ----

    def test_coupled_in_all_messages_but_not_in_suggestions(self):
        """include_all_messages=true: kopplat kvitto ska finnas kvar i
        all_messages (med coupled=true) men INTE i missing_receipts/suggestions."""
        self._seed(
            message_id="m-coupled",
            bezala_transaction_id="bz-1",
            bezala_upload_status="success",
        )
        self._seed(message_id="m-fresh")
        fake = MagicMock()
        fake.list_missing_receipts.return_value = self._missing_payload()
        with patch.object(self.app_module, "BezalaClient", return_value=fake):
            resp = self.client.get(
                "/api/bezala/match-suggestions?include_all_messages=true",
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        # Kopplat finns i all_messages
        all_ids = {m["message_id"] for m in body["all_messages"]}
        self.assertIn("m-coupled", all_ids)
        self.assertIn("m-fresh", all_ids)
        # Men inte i suggestions
        suggestions = body["missing_receipts"][0]["suggestions"]
        suggestion_ids = {s["message"]["message_id"] for s in suggestions}
        self.assertNotIn("m-coupled", suggestion_ids)
        self.assertIn("m-fresh", suggestion_ids)

    def test_coupled_via_dual_date_match_still_filtered(self):
        """Edge case: kvitto matchar via received_at (dual-date) men är
        kopplat → fortfarande filtreras bort."""
        self._seed(
            message_id="m-flight",
            vendor="Finnair",
            amount=366.32,
            currency="EUR",
            receipt_date="2026-04-30",  # resedag
            received_at=datetime(2026, 4, 24, 18, 42),  # bokningsdag
            bezala_transaction_id="bz-flight",
            bezala_upload_status="success",
        )
        fake = MagicMock()
        fake.list_missing_receipts.return_value = [{
            "id": 5,
            "description": "FINNAIR FI",
            "amount": 366.32,
            "currency": "EUR",
            "date": "2026-04-24",  # matchar received_at exakt
        }]
        with patch.object(self.app_module, "BezalaClient", return_value=fake):
            resp = self.client.get("/api/bezala/match-suggestions")
        self.assertEqual(resp.json()[0]["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
