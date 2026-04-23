"""FAS 5.4 — kortmatchning. Tester för:
- receipt_matcher pure functions (score_match, find_matches, vendor)
- BezalaClient.list_missing_receipts + attach_file
- GET /api/bezala/missing-receipts endpoint
- GET /api/bezala/match-suggestions endpoint
- POST /api/messages/{id}/match-to-bezala endpoint
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
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake"


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


def _make_client():
    from app.services.bezala_client import BezalaClient
    client = BezalaClient.__new__(BezalaClient)
    client._email = "u"
    client._password = "p"
    client._base_url = "https://mock.bezala/api"
    client._client = MagicMock()
    client._token = "fake"
    client._token_expires_at = 9e18
    return client


# ---------- Pure matcher-tester ----------


class ScoreMatchTest(unittest.TestCase):
    def _missing(self, **over):
        base = {
            "amount": 112.95,
            "currency": "EUR",
            "date": "2026-04-14",
            "description": "CLAUDE.AI SUBSCRIPTION",
        }
        base.update(over)
        return base

    def _candidate(self, **over):
        base = {
            "amount": 112.95,
            "currency": "EUR",
            "receipt_date": "2026-04-14",
            "vendor": "Anthropic",
        }
        base.update(over)
        return base

    def test_perfect_match_high_score(self):
        from app.services.receipt_matcher import score_match
        s = score_match(self._missing(), self._candidate())
        # exact amount (50) + exact date (30) + vendor override claude→anthropic
        self.assertGreaterEqual(s["total"], 80)
        self.assertEqual(s["breakdown"]["amount"], 50)
        self.assertEqual(s["breakdown"]["date"], 30)
        self.assertGreater(s["breakdown"]["vendor"], 20)

    def test_amount_within_5pct(self):
        from app.services.receipt_matcher import score_match
        # 112.95 vs 117.00 → 3.6% diff → inom 5%
        s = score_match(self._missing(), self._candidate(amount=117.00))
        self.assertEqual(s["breakdown"]["amount"], 50)

    def test_amount_outside_5pct_no_bonus(self):
        from app.services.receipt_matcher import score_match
        s = score_match(self._missing(), self._candidate(amount=200.00))
        self.assertEqual(s["breakdown"]["amount"], 0)

    def test_date_distance_decay(self):
        from app.services.receipt_matcher import score_match
        # Same date → 30
        self.assertEqual(
            score_match(self._missing(), self._candidate())["breakdown"]["date"], 30,
        )
        # ±1 day → 25
        self.assertEqual(
            score_match(self._missing(), self._candidate(receipt_date="2026-04-15"))["breakdown"]["date"], 25,
        )
        # ±3 days → 15
        self.assertEqual(
            score_match(self._missing(), self._candidate(receipt_date="2026-04-17"))["breakdown"]["date"], 15,
        )
        # >3 days → 0
        self.assertEqual(
            score_match(self._missing(), self._candidate(receipt_date="2026-04-20"))["breakdown"]["date"], 0,
        )

    def test_vendor_override_claude_anthropic(self):
        from app.services.receipt_matcher import vendor_similarity
        # Direct override
        sim = vendor_similarity("CLAUDE.AI SUBSCRIPTION", "Anthropic")
        self.assertGreaterEqual(sim, 0.9)

    def test_vendor_override_airport_lrs_arlanda(self):
        from app.services.receipt_matcher import vendor_similarity
        sim = vendor_similarity("AIRPORT LRS", "Arlanda Express")
        self.assertGreaterEqual(sim, 0.9)

    def test_find_matches_filters_below_threshold(self):
        from app.services.receipt_matcher import find_matches, MIN_DISPLAY_SCORE
        candidates = [
            {  # Bra match
                "id": 1, "amount": 112.95, "receipt_date": "2026-04-14",
                "vendor": "Anthropic",
            },
            {  # Helt fel
                "id": 2, "amount": 9999, "receipt_date": "2025-01-01",
                "vendor": "Random Inc",
            },
        ]
        matches = find_matches(self._missing(), candidates)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["message"]["id"], 1)
        self.assertGreaterEqual(matches[0]["score"], MIN_DISPLAY_SCORE)


# ---------- BezalaClient-tester ----------


class BezalaMissingReceiptsClientTest(unittest.TestCase):
    def test_list_missing_receipts_parses_array(self):
        client = _make_client()
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = "[]"
        rows = [{"id": 1, "description": "X", "amount": 10, "date": "2026-04-14"}]
        resp.json = MagicMock(return_value=rows)

        def fake_request(method, url, **kwargs):
            return resp
        client._client.request = fake_request

        result = client.list_missing_receipts()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)

    def test_attach_file_sends_transaction_id(self):
        client = _make_client()
        captured = {}
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = '{"id": 999}'
        resp.json = MagicMock(return_value={"id": 999})

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["files"] = kwargs.get("files")
            captured["data"] = kwargs.get("data")
            return resp
        client._client.request = fake_request

        att = client.attach_file("tx-77", "kvitto.pdf", PDF_BYTES)
        self.assertEqual(att.attachment_id, "999")
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/attachments"))
        self.assertEqual(captured["data"], {"transaction_id": "tx-77"})
        self.assertIn("file", captured["files"])

    def test_attach_file_rejects_invalid_pdf(self):
        from app.services.bezala_client import BezalaError
        client = _make_client()
        with self.assertRaises(BezalaError):
            client.attach_file("tx-77", "x.pdf", b"not a pdf")


# ---------- Endpoint-tester ----------


class CardMatchingEndpointsTest(unittest.TestCase):
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

    def _seed_processed(self, **over):
        defaults = dict(
            message_id="m-1",
            sender="invoice@anthropic.com",
            subject="Anthropic API",
            status="saved",
            file_name="20260414 Anthropic API.pdf",
            drive_file_id="drv-1",
            drive_link="https://drive/drv-1",
            vendor="Anthropic",
            amount=112.95,
            currency="EUR",
            receipt_date="2026-04-14",
            category="AI",
            ai_confidence=95,
            bezala_upload_status="pending",
        )
        defaults.update(over)
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(**defaults)
            db.add(row)
            db.flush()
            mid = row.id
            db.commit()
        return mid

    def test_get_missing_receipts_returns_normalized_list(self):
        fake_bezala = MagicMock()
        fake_bezala.list_missing_receipts.return_value = [
            {
                "id": 12345,
                "description": "CLAUDE.AI SUBSCRIPTION",
                "amount": 112.95,
                "currency": "EUR",
                "date": "2026-04-14",
            },
        ]
        with patch.object(self.app_module, "BezalaClient", return_value=fake_bezala):
            resp = self.client.get("/api/bezala/missing-receipts")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], 12345)
        self.assertEqual(body[0]["description"], "CLAUDE.AI SUBSCRIPTION")

    def test_match_suggestions_returns_top_candidates(self):
        # Skapa en ProcessedMessage som matchar perfekt
        self._seed_processed()

        fake_bezala = MagicMock()
        fake_bezala.list_missing_receipts.return_value = [
            {
                "id": 12345,
                "description": "CLAUDE.AI SUBSCRIPTION",
                "amount": 112.95,
                "currency": "EUR",
                "date": "2026-04-14",
            },
        ]
        with patch.object(self.app_module, "BezalaClient", return_value=fake_bezala):
            resp = self.client.get("/api/bezala/match-suggestions")

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        entry = body[0]
        self.assertEqual(entry["missing_receipt"]["id"], 12345)
        self.assertEqual(len(entry["suggestions"]), 1)
        self.assertGreaterEqual(entry["suggestions"][0]["score"], 80)

    def test_match_to_bezala_does_two_step_put_then_attach(self):
        """Match-flödet kör two-step: PUT /transactions/{id} med metadata
        FÖRST, sedan POST /attachments. Samma som upload_receipt."""
        mid = self._seed_processed()

        fake_drive = MagicMock()
        fake_drive.download_pdf.return_value = PDF_BYTES

        fake_bezala = MagicMock()
        # Live-verifierad metadata — inkludera AI-konto eftersom
        # _seed_processed() sätter category='AI'
        fake_bezala.list_accounts.return_value = [
            {"id": 166648, "name": "AI työkalut", "default_vat_id": 1355},
        ]
        fake_bezala.list_cost_centers.return_value = [{
            "id": 927151, "name": "VIS128",
        }]
        fake_bezala.list_vat_rates.return_value = []

        fake_attachment = MagicMock()
        fake_attachment.attachment_id = "att-1"
        fake_bezala.attach_file.return_value = fake_attachment

        # Spara anropsordning så vi kan verifiera PUT först, POST sist
        call_order: list[str] = []
        fake_bezala.update_transaction.side_effect = (
            lambda *a, **kw: call_order.append("update") or MagicMock()
        )
        fake_bezala.attach_file.side_effect = (
            lambda *a, **kw: call_order.append("attach") or fake_attachment
        )

        with patch.object(self.app_module, "DriveClient", return_value=fake_drive), \
             patch.object(self.app_module, "BezalaClient", return_value=fake_bezala):
            resp = self.client.post(
                f"/api/messages/{mid}/match-to-bezala",
                json={"missing_receipt_id": 12345},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["bezala_upload_status"], "success")
        self.assertEqual(body["bezala_transaction_id"], "12345")

        # Två-step: PUT /transactions → POST /attachments i rätt ordning
        self.assertEqual(call_order, ["update", "attach"])

        # update_transaction fick metadata (description + date + credit +
        # vat_lines_attributes)
        update_kwargs = fake_bezala.update_transaction.call_args.kwargs
        update_args = fake_bezala.update_transaction.call_args.args
        self.assertEqual(update_args[0], "12345")  # transaction_id
        self.assertEqual(update_kwargs["description"], "20260414 Anthropic API")
        self.assertEqual(update_kwargs["date"], "2026-04-14")
        self.assertEqual(update_kwargs["credit_account_id"], 82320)
        self.assertEqual(len(update_kwargs["vat_lines_attributes"]), 1)

        # attach_file fick rätt tx_id + fil
        attach_args = fake_bezala.attach_file.call_args.args
        self.assertEqual(attach_args[0], "12345")
        self.assertEqual(attach_args[1], "20260414 Anthropic API.pdf")
        self.assertEqual(attach_args[2], PDF_BYTES)

    def test_match_to_bezala_400_when_missing_date(self):
        mid = self._seed_processed(receipt_date=None)
        resp = self.client.post(
            f"/api/messages/{mid}/match-to-bezala",
            json={"missing_receipt_id": 1},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("datum", resp.json()["detail"].lower())

    def test_match_to_bezala_400_when_missing_amount(self):
        mid = self._seed_processed(amount=None)
        resp = self.client.post(
            f"/api/messages/{mid}/match-to-bezala",
            json={"missing_receipt_id": 1},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("belopp", resp.json()["detail"].lower())

    def test_match_to_bezala_404_for_missing_message(self):
        resp = self.client.post(
            "/api/messages/99999/match-to-bezala",
            json={"missing_receipt_id": 1},
        )
        self.assertEqual(resp.status_code, 404)

    def test_match_to_bezala_400_when_missing_drive_file(self):
        mid = self._seed_processed(drive_file_id=None, file_name=None)
        resp = self.client.post(
            f"/api/messages/{mid}/match-to-bezala",
            json={"missing_receipt_id": 1},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Drive-fil", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
