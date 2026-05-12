"""FAS 5.9 — tester för engelsk AI-beskrivning (ai_description_en).

Verifierar att:
1. Receipt-analyzern plockar upp och normaliserar `description_en`
   från Claude:s JSON-svar och fyller fältet i ReceiptAnalysis.
2. _attempt_bezala_upload skickar engelsk beskrivning som
   `description` när AI:n producerade en.
3. Manuell Bezala-upload faller tillbaka på row.summary (svenska)
   när ai_description_en är NULL — för legacy-rader som skapades
   innan kolumnen fanns.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake"


class AnalyzerEnglishDescriptionTest(unittest.TestCase):
    """_normalize plockar upp description_en och trunkerar defensivt."""

    def test_ai_generates_english_description_with_correct_format(self):
        from app.services.receipt_analyzer import _normalize

        payload = {
            "is_receipt": True,
            "confidence": 92,
            "filename": "20260422 Finavia Parkering.pdf",
            "vendor": "Finavia",
            "amount": 48.0,
            "currency": "EUR",
            "date": "2026-04-22",
            "category": "Parkering",
            "summary": "Parkeringskvitto från Finavia P2",
            "description_en": (
                "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026"
            ),
        }
        analysis = _normalize(
            payload,
            received_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
            sender="noreply@finavia.fi",
            subject="Kvitto",
        )
        self.assertEqual(
            analysis.description_en,
            "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026",
        )
        # summary (svenska) ska inte påverkas
        self.assertEqual(analysis.summary, "Parkeringskvitto från Finavia P2")

    def test_description_en_missing_in_payload_falls_to_none(self):
        from app.services.receipt_analyzer import _normalize

        payload = {
            "is_receipt": True,
            "confidence": 50,
            "filename": "x.pdf",
            "vendor": "X",
            "amount": 1.0,
            "currency": "EUR",
            "date": "2026-04-22",
            "category": "Annat",
            "summary": "x",
        }
        analysis = _normalize(
            payload, received_at=None, sender="a@b.com", subject="s",
        )
        self.assertIsNone(analysis.description_en)

    def test_description_en_truncates_to_500_chars(self):
        from app.services.receipt_analyzer import _normalize

        payload = {
            "is_receipt": True,
            "confidence": 50,
            "filename": "x.pdf",
            "vendor": "X",
            "amount": 1.0,
            "currency": "EUR",
            "date": "2026-04-22",
            "category": "Annat",
            "summary": "x",
            "description_en": "A" * 800,
        }
        analysis = _normalize(
            payload, received_at=None, sender="a@b.com", subject="s",
        )
        self.assertEqual(len(analysis.description_en), 500)
        self.assertEqual(analysis.description_en, "A" * 500)

    def test_description_en_strips_trailing_period(self):
        from app.services.receipt_analyzer import _normalize

        payload = {
            "is_receipt": True,
            "confidence": 50,
            "filename": "x.pdf",
            "vendor": "X",
            "amount": 1.0,
            "currency": "EUR",
            "date": "2026-04-22",
            "category": "Annat",
            "summary": "x",
            "description_en": "Flight Helsinki-Stockholm round trip, 30 April 2026.",
        }
        analysis = _normalize(
            payload, received_at=None, sender="a@b.com", subject="s",
        )
        self.assertEqual(
            analysis.description_en,
            "Flight Helsinki-Stockholm round trip, 30 April 2026",
        )


class BezalaUploaderUsesEnglishFieldTest(unittest.TestCase):
    """_attempt_bezala_upload prioriterar analysis.description_en
    framför filnamns-baserad fallback."""

    def _make_msg(self):
        from app.services.gmail_client import GmailMessage

        return GmailMessage(
            message_id="m1",
            thread_id="t1",
            sender="noreply@finavia.fi",
            subject="Kvitto Finavia",
            received_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
            snippet="",
        )

    def _metadata(self):
        return {
            "accounts": [
                {"id": 67100, "name": "Matkaliput", "default_vat_id": 1355},
            ],
            "cost_centers": [{"id": 927151, "name": "VIS128"}],
            "vat_rates": [],
        }

    def test_bezala_uploader_uses_english_field(self):
        from app.services.pipeline import _attempt_bezala_upload
        from app.services.receipt_analyzer import ReceiptAnalysis

        analysis = ReceiptAnalysis(
            is_receipt=True,
            confidence=95,
            filename="20260422 Finavia Parkering.pdf",
            vendor="Finavia",
            amount=48.0,
            currency="EUR",
            date="2026-04-22",
            category="Parkering",
            summary="Parkeringskvitto från Finavia P2",
            description_en=(
                "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026"
            ),
        )

        fake_bezala = MagicMock()
        fake_receipt = MagicMock()
        fake_receipt.attachment_id = "r-1"
        fake_bezala.upload_receipt.return_value = fake_receipt

        status, _, err = _attempt_bezala_upload(
            fake_bezala,
            analysis,
            self._make_msg(),
            PDF_BYTES,
            "20260422 Finavia Parkering.pdf",
            auto_upload=True,
            confidence_threshold=90,
            metadata=self._metadata(),
        )

        self.assertEqual(status, "success")
        self.assertIsNone(err)
        kwargs = fake_bezala.upload_receipt.call_args.kwargs
        self.assertEqual(
            kwargs["description"],
            "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026",
        )

    def test_bezala_uploader_falls_back_to_filename_when_no_english(self):
        """Om AI:n inte producerar description_en (None) ska den
        existerande filnamn-baserade fallbacken användas."""
        from app.services.pipeline import _attempt_bezala_upload
        from app.services.receipt_analyzer import ReceiptAnalysis

        analysis = ReceiptAnalysis(
            is_receipt=True,
            confidence=95,
            filename="20260422 Finnair HEL-CPH.pdf",
            vendor="Finnair",
            amount=503.0,
            currency="EUR",
            date="2026-04-22",
            category="Flyg",
            summary="Flyg HEL-CPH",
            description_en=None,
        )

        fake_bezala = MagicMock()
        fake_receipt = MagicMock()
        fake_receipt.attachment_id = "r-2"
        fake_bezala.upload_receipt.return_value = fake_receipt

        _attempt_bezala_upload(
            fake_bezala,
            analysis,
            self._make_msg(),
            PDF_BYTES,
            "20260422 Finnair HEL-CPH.pdf",
            auto_upload=True,
            confidence_threshold=90,
            metadata=self._metadata(),
        )

        kwargs = fake_bezala.upload_receipt.call_args.kwargs
        # Filnamn-fallback (utan .pdf) — samma som tidigare beteende
        self.assertEqual(kwargs["description"], "20260422 Finnair HEL-CPH")


class FieldMapperFallbackTest(unittest.TestCase):
    """build_receipt_params(description_override=...) — verifierar
    legacy-fallback: när ai_description_en saknas men row.summary
    finns ska summary användas som description-override."""

    def _accounts(self):
        return [{"id": 67100, "name": "Matkaliput", "default_vat_id": 1355}]

    def test_fallback_to_swedish_for_legacy_records(self):
        """Manuell upload-path: row.ai_description_en=None,
        row.summary='Parkeringskvitto ...' → summary används som
        description (override) i stället för build_description()."""
        from app.services.bezala_field_mapper import build_receipt_params

        # Simulera caller-logiken i main.py: ai_description_en or summary
        legacy_row = {
            "ai_description_en": None,
            "summary": "Parkeringskvitto från Finavia P2",
        }
        override = legacy_row["ai_description_en"] or legacy_row["summary"]

        params = build_receipt_params(
            file_name="20260422 Finavia.pdf",
            sender="noreply@finavia.fi",
            vendor="Finavia",
            category="Parkering",
            amount=48.0,
            currency="EUR",
            receipt_date="2026-04-22",
            subject="Kvitto",
            accounts=self._accounts(),
            cost_centers=[],
            vat_rates=[],
            description_override=override,
        )
        self.assertEqual(
            params["description"], "Parkeringskvitto från Finavia P2"
        )

    def test_english_override_wins_over_filename(self):
        from app.services.bezala_field_mapper import build_receipt_params

        params = build_receipt_params(
            file_name="20260422 Finavia Parkering.pdf",
            sender="noreply@finavia.fi",
            vendor="Finavia",
            category="Parkering",
            amount=48.0,
            currency="EUR",
            receipt_date="2026-04-22",
            subject="Kvitto",
            accounts=self._accounts(),
            cost_centers=[],
            vat_rates=[],
            description_override=(
                "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026"
            ),
        )
        self.assertEqual(
            params["description"],
            "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026",
        )

    def test_empty_override_falls_back_to_filename(self):
        """description_override='' (tomt) ska INTE användas — fall tillbaka
        på existerande build_description-kedja."""
        from app.services.bezala_field_mapper import build_receipt_params

        params = build_receipt_params(
            file_name="20260422 Finnair HEL-CPH.pdf",
            sender="noreply@finnair.com",
            vendor="Finnair",
            category="Flyg",
            amount=503.0,
            currency="EUR",
            receipt_date="2026-04-22",
            subject="Kvitto",
            accounts=self._accounts(),
            cost_centers=[],
            vat_rates=[],
            description_override="   ",
        )
        self.assertEqual(params["description"], "20260422 Finnair HEL-CPH")


if __name__ == "__main__":
    unittest.main()
