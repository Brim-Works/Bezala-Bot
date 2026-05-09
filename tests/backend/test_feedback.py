"""FAS 8 — feedback-loop backend tester.

Täcker:
- save_correction / save_thumbs / get_few_shot_examples i feedback.py
- format_examples_for_prompt
- extract_vendor_for_context heuristik
- POST /api/feedback/thumbs och /api/feedback/correction endpoints
- Auto-correction trigger i POST /api/messages/{id}/upload-to-bezala

Körs med:
    python -m unittest tests.backend.test_feedback
"""

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
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake"


def _configure_memory_engine():
    """SQLAlchemy memory-DB delas inte mellan connections per default.
    StaticPool delar samma connection så alla sessions ser samma DB."""
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


# ---------- Pure helpers (ingen DB) ----------


class ExtractVendorForContextTest(unittest.TestCase):
    def _extract(self, sender):
        from app.services.feedback import extract_vendor_for_context
        return extract_vendor_for_context(sender)

    def test_named_with_email(self):
        self.assertEqual(
            self._extract("Finnair <noreply@finnair.com>"), "Finnair",
        )

    def test_email_only(self):
        self.assertEqual(
            self._extract("noreply@arlandaexpress.se"), "arlandaexpress",
        )

    def test_empty_returns_none(self):
        self.assertIsNone(self._extract(""))
        self.assertIsNone(self._extract(None))

    def test_quoted_name(self):
        self.assertEqual(
            self._extract('"SL Reseinfo" <kvitto@sl.se>'), "SL Reseinfo",
        )


class FormatExamplesForPromptTest(unittest.TestCase):
    def _format(self, examples):
        from app.services.feedback import format_examples_for_prompt
        return format_examples_for_prompt(examples)

    def test_empty_list_returns_empty_string(self):
        self.assertEqual(self._format([]), "")

    def test_none_returns_empty_string(self):
        self.assertEqual(self._format(None), "")

    def test_includes_vendor_field_and_correction(self):
        out = self._format([
            {
                "vendor_context": "Finnair",
                "field_name": "vendor",
                "ai_value": "Finnair Plc",
                "correct_value": "Finnair",
            },
        ])
        self.assertIn("Tidigare rättelser", out)
        self.assertIn("Finnair", out)
        self.assertIn("vendor", out)
        self.assertIn("Finnair Plc", out)


# ---------- Service-tester (med riktig DB) ----------


class FeedbackServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401 — registrerar AiFeedback också
        from app.models import AiFeedback, ProcessedMessage

        Base.metadata.create_all(bind=db_module.engine)
        cls.db_module = db_module
        cls.SessionLocal = db_module.SessionLocal
        cls.AiFeedback = AiFeedback
        cls.ProcessedMessage = ProcessedMessage

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.AiFeedback).delete()
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _seed_message(self, message_id="m-1", vendor="Finnair"):
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(
                message_id=message_id,
                sender=f"noreply@{vendor.lower()}.com",
                subject="Resa",
                status="saved",
                vendor=vendor,
            )
            db.add(row)
            db.commit()

    def test_save_correction_creates_row(self):
        from app.services.feedback import save_correction
        self._seed_message("m-1", "Finnair")
        with self.SessionLocal() as db:
            row = save_correction(db, "m-1", "vendor", "Finnair Plc", "Finnair")
            db.commit()
        self.assertIsNotNone(row)
        with self.SessionLocal() as db:
            stored = db.query(self.AiFeedback).first()
            self.assertEqual(stored.feedback_type, "correction")
            self.assertEqual(stored.field_name, "vendor")
            self.assertEqual(stored.ai_value, "Finnair Plc")
            self.assertEqual(stored.correct_value, "Finnair")
            self.assertEqual(stored.vendor_context, "Finnair")

    def test_save_correction_normalizes_date_to_receipt_date(self):
        from app.services.feedback import save_correction
        self._seed_message("m-2", "SL")
        with self.SessionLocal() as db:
            save_correction(db, "m-2", "date", "2026-01-01", "2026-04-22")
            db.commit()
        with self.SessionLocal() as db:
            row = db.query(self.AiFeedback).first()
            self.assertEqual(row.field_name, "receipt_date")

    def test_save_correction_skips_identical_values(self):
        from app.services.feedback import save_correction
        self._seed_message("m-3")
        with self.SessionLocal() as db:
            row = save_correction(db, "m-3", "vendor", "Finnair", "Finnair")
            db.commit()
        self.assertIsNone(row)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(self.AiFeedback).count(), 0)

    def test_save_correction_returns_none_for_empty_message_id(self):
        from app.services.feedback import save_correction
        with self.SessionLocal() as db:
            self.assertIsNone(save_correction(db, "", "vendor", "a", "b"))
            self.assertIsNone(save_correction(db, "m", "", "a", "b"))

    def test_save_thumbs_positive_creates_one_row_no_field(self):
        from app.services.feedback import save_thumbs
        self._seed_message("m-1")
        with self.SessionLocal() as db:
            rows = save_thumbs(db, "m-1", True)
            db.commit()
        self.assertEqual(len(rows), 1)
        with self.SessionLocal() as db:
            stored = db.query(self.AiFeedback).first()
            self.assertEqual(stored.feedback_type, "thumbs_up")
            self.assertIsNone(stored.field_name)

    def test_save_thumbs_negative_with_fields_creates_per_field(self):
        from app.services.feedback import save_thumbs
        self._seed_message("m-1")
        with self.SessionLocal() as db:
            rows = save_thumbs(db, "m-1", False, ["vendor", "amount"])
            db.commit()
        self.assertEqual(len(rows), 2)
        with self.SessionLocal() as db:
            field_names = sorted(
                r.field_name for r in db.query(self.AiFeedback).all()
            )
            self.assertEqual(field_names, ["amount", "vendor"])

    def test_save_thumbs_negative_without_fields_creates_one_row(self):
        from app.services.feedback import save_thumbs
        self._seed_message("m-1")
        with self.SessionLocal() as db:
            rows = save_thumbs(db, "m-1", False, [])
            db.commit()
        self.assertEqual(len(rows), 1)
        with self.SessionLocal() as db:
            stored = db.query(self.AiFeedback).first()
            self.assertEqual(stored.feedback_type, "thumbs_down")
            self.assertIsNone(stored.field_name)

    def test_get_few_shot_examples_5_per_bucket_when_vendor_given(self):
        from app.services.feedback import save_correction, get_few_shot_examples
        # Seed 7 Finnair-rättelser + 7 från andra leverantörer
        for i in range(7):
            self._seed_message(f"finnair-{i}", "Finnair")
            self._seed_message(f"sl-{i}", "SL")
        with self.SessionLocal() as db:
            for i in range(7):
                save_correction(
                    db, f"finnair-{i}", "vendor",
                    f"AI-{i}", f"Finnair{i}",
                )
                save_correction(
                    db, f"sl-{i}", "vendor",
                    f"AI-{i}", f"SL-{i}",
                )
            db.commit()

        with self.SessionLocal() as db:
            results = get_few_shot_examples(db, vendor="Finnair", limit=10)

        # 5 Finnair + 5 övriga
        self.assertEqual(len(results), 10)
        finnair_count = sum(
            1 for r in results if r["vendor_context"] == "Finnair"
        )
        sl_count = sum(1 for r in results if r["vendor_context"] == "SL")
        self.assertEqual(finnair_count, 5)
        self.assertEqual(sl_count, 5)

    def test_get_few_shot_examples_unknown_vendor_returns_latest(self):
        from app.services.feedback import save_correction, get_few_shot_examples
        for i in range(3):
            self._seed_message(f"x-{i}", "X")
        with self.SessionLocal() as db:
            for i in range(3):
                save_correction(db, f"x-{i}", "vendor", f"a{i}", f"b{i}")
            db.commit()
        with self.SessionLocal() as db:
            results = get_few_shot_examples(
                db, vendor="OkändLeverantör", limit=10,
            )
        self.assertEqual(len(results), 3)

    def test_get_few_shot_examples_empty_db_returns_empty_list(self):
        from app.services.feedback import get_few_shot_examples
        with self.SessionLocal() as db:
            self.assertEqual(get_few_shot_examples(db, vendor="X"), [])
            self.assertEqual(get_few_shot_examples(db, vendor=None), [])

    def test_get_examples_for_sender_extracts_vendor(self):
        from app.services.feedback import (
            save_correction, get_examples_for_sender,
        )
        self._seed_message("m-fin", "Finnair")
        with self.SessionLocal() as db:
            save_correction(db, "m-fin", "vendor", "Finnair Plc", "Finnair")
            db.commit()
        with self.SessionLocal() as db:
            results = get_examples_for_sender(
                db, "Finnair <noreply@finnair.com>", limit=10,
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["vendor_context"], "Finnair")


# ---------- Endpoint-tester ----------


class FeedbackEndpointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import AiFeedback, ProcessedMessage
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
        cls.AiFeedback = AiFeedback
        cls.ProcessedMessage = ProcessedMessage

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.AiFeedback).delete()
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _seed(self, message_id="m-end", vendor="Finnair", **fields):
        defaults = dict(
            sender=f"noreply@{vendor.lower()}.com",
            subject="Resa",
            status="saved",
            vendor=vendor,
            amount=100.0,
            currency="EUR",
            receipt_date="2026-04-14",
            category="Flyg",
            file_name="20260414 Finnair.pdf",
            drive_file_id="drv-1",
            ai_confidence=85,
            bezala_upload_status="pending",
        )
        defaults.update(fields)
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(message_id=message_id, **defaults)
            db.add(row)
            db.flush()
            mid = row.id
            db.commit()
        return mid

    def test_post_thumbs_endpoint_positive(self):
        self._seed("m-1")
        resp = self.client.post(
            "/api/feedback/thumbs",
            json={"message_id": "m-1", "is_positive": True},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["saved"], 1)
        with self.SessionLocal() as db:
            row = db.query(self.AiFeedback).first()
            self.assertEqual(row.feedback_type, "thumbs_up")

    def test_post_thumbs_endpoint_negative_with_fields(self):
        self._seed("m-2")
        resp = self.client.post(
            "/api/feedback/thumbs",
            json={
                "message_id": "m-2",
                "is_positive": False,
                "fields": ["vendor", "amount"],
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["saved"], 2)

    def test_post_correction_endpoint_uses_db_for_ai_value(self):
        """När frontend skickar bara message_id + field_name + correct_value,
        plockar backend ai_value från ProcessedMessage."""
        self._seed("m-3", vendor="OldVendor")
        resp = self.client.post(
            "/api/feedback/correction",
            json={
                "message_id": "m-3",
                "field_name": "vendor",
                "correct_value": "NewVendor",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["saved"])
        with self.SessionLocal() as db:
            row = db.query(self.AiFeedback).first()
            self.assertEqual(row.ai_value, "OldVendor")
            self.assertEqual(row.correct_value, "NewVendor")
            self.assertEqual(row.field_name, "vendor")

    def test_post_correction_with_explicit_ai_value(self):
        """Frontend kan skicka ai_value själv för att undvika race med
        upload-to-bezala."""
        self._seed("m-4")
        resp = self.client.post(
            "/api/feedback/correction",
            json={
                "message_id": "m-4",
                "field_name": "amount",
                "ai_value": "100.0",
                "correct_value": "120.5",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        with self.SessionLocal() as db:
            row = db.query(self.AiFeedback).first()
            self.assertEqual(row.ai_value, "100.0")
            self.assertEqual(row.correct_value, "120.5")

    def test_upload_to_bezala_records_corrections_for_changed_fields(self):
        """När user godkänner i Granska med ändringar (overrides),
        loggas dessa som auto-correction-feedback."""
        mid = self._seed("m-5", vendor="OldVendor", amount=100.0)

        fake_drive = MagicMock()
        fake_drive.download_pdf.return_value = PDF_BYTES

        fake_bezala = MagicMock()
        fake_bezala_metadata = {
            "accounts": [], "cost_centers": [], "vat_rates": [],
        }
        fake_receipt = MagicMock()
        fake_receipt.attachment_id = "att-1"
        fake_bezala.upload_receipt.return_value = fake_receipt

        with patch.object(
            self.app_module, "DriveClient", return_value=fake_drive,
        ), patch.object(
            self.app_module, "BezalaClient", return_value=fake_bezala,
        ), patch.object(
            self.app_module, "fetch_bezala_metadata",
            return_value=fake_bezala_metadata,
        ), patch.object(
            self.app_module, "build_receipt_params",
            return_value={
                "description": "x",
                "date": "2026-04-14",
                "credit_account_id": None,
                "vat_lines_attributes": [],
            },
        ):
            resp = self.client.post(
                f"/api/messages/{mid}/upload-to-bezala",
                json={
                    "vendor": "NewVendor",
                    "amount": 250.0,
                    # currency oförändrad — ska INTE generera feedback
                    "currency": "EUR",
                },
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        with self.SessionLocal() as db:
            rows = db.query(self.AiFeedback).all()
            field_names = sorted(r.field_name for r in rows)
            # Bara vendor + amount ska ha auto-correction-rader (currency
            # var oförändrad). receipt_date/category skickades inte alls.
            self.assertEqual(field_names, ["amount", "vendor"])
            for r in rows:
                self.assertEqual(r.feedback_type, "correction")



# ---------- FAS 8.1: not_a_receipt-feedback ----------


class FormatNotReceiptExamplesTest(unittest.TestCase):
    def _format(self, examples):
        from app.services.feedback import format_not_receipt_examples_for_prompt
        return format_not_receipt_examples_for_prompt(examples)

    def test_empty_returns_empty_string(self):
        self.assertEqual(self._format([]), "")
        self.assertEqual(self._format(None), "")

    def test_includes_sender_and_warning_text(self):
        out = self._format([
            {"sender": "Finnair <noreply@finnair.com>"},
            {"sender": "events@meetingpro.com"},
        ])
        self.assertIn("Mail som ANVÄNDAREN markerat som icke-kvitto", out)
        self.assertIn("Finnair", out)
        self.assertIn("meetingpro", out)
        self.assertIn("INTE ett kvitto", out)


class NotAReceiptServiceTest(unittest.TestCase):
    """save_not_a_receipt + get_not_receipt_examples med riktig DB."""

    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app.models import AiFeedback, ProcessedMessage

        Base.metadata.create_all(bind=db_module.engine)
        cls.db_module = db_module
        cls.SessionLocal = db_module.SessionLocal
        cls.AiFeedback = AiFeedback
        cls.ProcessedMessage = ProcessedMessage

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.AiFeedback).delete()
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _seed_message(self, message_id, sender, vendor=None):
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(
                message_id=message_id,
                sender=sender,
                subject="Bokningsbekräftelse",
                status="saved",
                vendor=vendor,
            )
            db.add(row)
            db.commit()

    def test_save_not_a_receipt_creates_feedback_and_soft_deletes_message(self):
        from app.services.feedback import save_not_a_receipt
        self._seed_message("m-1", "Finnair <noreply@finnair.com>", vendor="Finnair")
        with self.SessionLocal() as db:
            result = save_not_a_receipt(db, "m-1")
        self.assertTrue(result.get("saved"))
        self.assertTrue(result.get("deleted"))
        with self.SessionLocal() as db:
            fb = db.query(self.AiFeedback).first()
            self.assertEqual(fb.feedback_type, "not_a_receipt")
            self.assertIsNone(fb.field_name)
            self.assertEqual(fb.ai_value, "is_receipt: true")
            self.assertEqual(fb.correct_value, "is_receipt: false")
            self.assertIn("Finnair", fb.vendor_context or "")

            msg = db.query(self.ProcessedMessage).filter_by(
                message_id="m-1"
            ).first()
            self.assertIsNotNone(msg.deleted_at)
            self.assertEqual(msg.delete_reason, "user_marked_not_receipt")

    def test_save_not_a_receipt_returns_false_for_unknown(self):
        from app.services.feedback import save_not_a_receipt
        with self.SessionLocal() as db:
            self.assertEqual(
                save_not_a_receipt(db, "missing"), {"saved": False},
            )

    def test_save_not_a_receipt_handles_empty_message_id(self):
        from app.services.feedback import save_not_a_receipt
        with self.SessionLocal() as db:
            self.assertEqual(
                save_not_a_receipt(db, ""), {"saved": False},
            )

    def test_get_not_receipt_examples_prioritizes_same_vendor(self):
        from app.services.feedback import (
            save_not_a_receipt, get_not_receipt_examples,
        )
        # Seed 3 Finnair-mail + 3 från andra leverantörer
        for i in range(3):
            self._seed_message(
                f"fin-{i}", f"Finnair <noreply{i}@finnair.com>",
            )
            self._seed_message(
                f"oth-{i}", f"Acme <events{i}@acme.com>",
            )
        with self.SessionLocal() as db:
            for i in range(3):
                save_not_a_receipt(db, f"fin-{i}")
                save_not_a_receipt(db, f"oth-{i}")

        with self.SessionLocal() as db:
            results = get_not_receipt_examples(
                db, sender="Finnair <eticket@finnair.com>", limit=5,
            )

        self.assertEqual(len(results), 5)
        # Första 3 ska vara Finnair (samma vendor-token)
        finnair_first_three = sum(
            1 for r in results[:3] if "Finnair" in r["sender"]
        )
        self.assertEqual(finnair_first_three, 3)

    def test_get_not_receipt_examples_returns_empty_when_table_empty(self):
        from app.services.feedback import get_not_receipt_examples
        with self.SessionLocal() as db:
            self.assertEqual(
                get_not_receipt_examples(db, sender="Finnair"), [],
            )
            self.assertEqual(
                get_not_receipt_examples(db, sender=None), [],
            )


class NotAReceiptEndpointTest(unittest.TestCase):
    """POST /api/feedback/not-a-receipt — happy path + 404."""

    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import AiFeedback, ProcessedMessage
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
        cls.AiFeedback = AiFeedback
        cls.ProcessedMessage = ProcessedMessage

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.AiFeedback).delete()
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _seed(self, message_id="m-nr", sender="Finnair <eticket@finnair.com>"):
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(
                message_id=message_id,
                sender=sender,
                subject="Bokningsbekräftelse",
                status="saved",
                vendor="Finnair",
            )
            db.add(row)
            db.commit()

    def test_post_not_a_receipt_happy_path(self):
        self._seed("m-nr-1")
        resp = self.client.post(
            "/api/feedback/not-a-receipt",
            json={"message_id": "m-nr-1"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["saved"])
        self.assertTrue(body["deleted"])

        with self.SessionLocal() as db:
            fb = db.query(self.AiFeedback).first()
            self.assertEqual(fb.feedback_type, "not_a_receipt")
            msg = db.query(self.ProcessedMessage).filter_by(
                message_id="m-nr-1"
            ).first()
            self.assertIsNotNone(msg.deleted_at)
            self.assertEqual(msg.delete_reason, "user_marked_not_receipt")

    def test_post_not_a_receipt_unknown_returns_404(self):
        resp = self.client.post(
            "/api/feedback/not-a-receipt",
            json={"message_id": "does-not-exist"},
        )
        self.assertEqual(resp.status_code, 404, resp.text)


class NegativeExamplesPromptTest(unittest.TestCase):
    """Verifierar att analyzer._build_system_prompt inkluderar
    not_a_receipt-blocket när negative_examples skickas in."""

    def test_negative_examples_appear_in_system_prompt(self):
        from app.services.receipt_analyzer import _build_system_prompt

        out = _build_system_prompt(
            examples=None,
            negative_examples=[
                {"sender": "Finnair <eticket@finnair.com>"},
            ],
        )
        self.assertIn("Mail som ANVÄNDAREN markerat som icke-kvitto", out)
        self.assertIn("Finnair", out)

    def test_no_examples_returns_unchanged_system_prompt(self):
        from app.services.receipt_analyzer import (
            SYSTEM_PROMPT, _build_system_prompt,
        )
        self.assertEqual(_build_system_prompt(None, None), SYSTEM_PROMPT)
        self.assertEqual(_build_system_prompt([], []), SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
