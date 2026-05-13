"""FAS 5.10 — applicering av bezala_vendor_mappings i upload-flödet.

Verifierar att build_receipt_params:
- forcerar bezala_account_id från mapping (Moovy/Finavia → 67113)
- forcerar tax_percentage från mapping.vat_rate (25.50 → "0.255")
- prioriterar mapping.description_override > description_override > filnamn
- faller tillbaka på kategori-baserad logik när vendor inte matchar
- loggar "Applied vendor mapping: ..." när override appliceras
"""

from __future__ import annotations

import logging
import os
import unittest
from decimal import Decimal

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


def _moovy_mapping():
    return {
        "vendor_pattern": "moovy",
        "bezala_account_id": 67113,
        "vat_rate": Decimal("25.50"),
        "description_override": "Parking at Helsinki-Vantaa Airport P2",
    }


def _finavia_mapping():
    return {
        "vendor_pattern": "finavia",
        "bezala_account_id": 67113,
        "vat_rate": Decimal("25.50"),
        "description_override": "Parking at Helsinki-Vantaa Airport P2",
    }


def _accounts():
    # Bezala-prod-konton (utdrag): default_vat_id för 67113 är 864 (=14%)
    # → mappnings-VAT 25.5% måste forcera över den här.
    return [
        {"id": 67100, "name": "Matkaliput", "default_vat_id": 1355},
        {"id": 67113, "name": "Paikoituskulut", "default_vat_id": 864},
        {"id": 67110, "name": "Muut matkakulut", "default_vat_id": 864},
    ]


class VendorMappingApplicationTest(unittest.TestCase):

    def test_moovy_upload_uses_mapping_account_and_vat(self):
        from app.services.bezala_field_mapper import build_receipt_params

        params = build_receipt_params(
            file_name="20260422 Moovy.pdf",
            sender="noreply@moovy.fi",
            vendor="Moovy",
            category="Parkering",
            amount=12.0,
            currency="EUR",
            receipt_date="2026-04-22",
            subject="Kvitto",
            accounts=_accounts(),
            cost_centers=[],
            vat_rates=[],
            vendor_mappings=[_moovy_mapping()],
        )
        lines = params["vat_lines_attributes"]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["expense_account_id"], 67113)
        self.assertEqual(lines[0]["tax_percentage"], "0.255")
        # Mappad rate 25.5% matchar verifierad FI-kod 1355
        self.assertEqual(lines[0]["vat_code_id"], 1355)

    def test_finavia_upload_uses_mapping(self):
        from app.services.bezala_field_mapper import build_receipt_params

        params = build_receipt_params(
            file_name="20260422 Finavia.pdf",
            sender="noreply@finavia.fi",
            vendor="Finavia",
            category="Parkering",
            amount=48.0,
            currency="EUR",
            receipt_date="2026-04-22",
            subject="Kvitto",
            accounts=_accounts(),
            cost_centers=[],
            vat_rates=[],
            vendor_mappings=[_moovy_mapping(), _finavia_mapping()],
        )
        lines = params["vat_lines_attributes"]
        self.assertEqual(lines[0]["expense_account_id"], 67113)
        self.assertEqual(lines[0]["tax_percentage"], "0.255")
        self.assertEqual(
            params["description"], "Parking at Helsinki-Vantaa Airport P2",
        )

    def test_unmapped_vendor_falls_back_to_category_logic(self):
        """Finnair har ingen mapping → kategori-baserad lookup ska gälla
        (kategori 'flyg' → konto 67100, default_vat_id=1355)."""
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
            accounts=_accounts(),
            cost_centers=[],
            vat_rates=[],
            vendor_mappings=[_moovy_mapping(), _finavia_mapping()],
        )
        lines = params["vat_lines_attributes"]
        self.assertEqual(lines[0]["expense_account_id"], 67100)
        # Inget mapping → tax_percentage från default_vat_id 1355 = "0.255"
        # (råkar vara samma siffra men via olika väg — verifiera ID)
        self.assertEqual(lines[0]["vat_code_id"], 1355)
        # Beskrivningen ska INTE vara mapping-override eftersom ingen mapping
        self.assertNotIn("Parking at Helsinki-Vantaa", params["description"])

    def test_description_override_takes_priority_over_ai_description_en(self):
        """När mapping har description_override ska den vinna även om
        ai_description_en finns (description_override-param)."""
        from app.services.bezala_field_mapper import build_receipt_params

        params = build_receipt_params(
            file_name="20260422 Moovy.pdf",
            sender="noreply@moovy.fi",
            vendor="Moovy",
            category="Parkering",
            amount=12.0,
            currency="EUR",
            receipt_date="2026-04-22",
            subject="Kvitto",
            accounts=_accounts(),
            cost_centers=[],
            vat_rates=[],
            description_override="AI: Parking at HEL airport, 22 April 2026",
            vendor_mappings=[_moovy_mapping()],
        )
        self.assertEqual(
            params["description"], "Parking at Helsinki-Vantaa Airport P2",
        )

    def test_ai_description_en_used_when_no_override(self):
        """Mapping utan description_override → description_override-param
        (ai_description_en) ska användas."""
        from app.services.bezala_field_mapper import build_receipt_params

        mapping = dict(_moovy_mapping())
        mapping["description_override"] = None  # explicit ingen override

        params = build_receipt_params(
            file_name="20260422 Moovy.pdf",
            sender="noreply@moovy.fi",
            vendor="Moovy",
            category="Parkering",
            amount=12.0,
            currency="EUR",
            receipt_date="2026-04-22",
            subject="Kvitto",
            accounts=_accounts(),
            cost_centers=[],
            vat_rates=[],
            description_override="AI: Parking at HEL airport, 22 April 2026",
            vendor_mappings=[mapping],
        )
        self.assertEqual(
            params["description"],
            "AI: Parking at HEL airport, 22 April 2026",
        )
        # Account-override ska fortfarande appliceras
        self.assertEqual(
            params["vat_lines_attributes"][0]["expense_account_id"], 67113,
        )

    def test_mapping_logs_applied_message(self):
        from app.services.bezala_field_mapper import build_receipt_params

        with self.assertLogs(
            "app.services.bezala_field_mapper", level="INFO",
        ) as captured:
            build_receipt_params(
                file_name="20260422 Moovy.pdf",
                sender="noreply@moovy.fi",
                vendor="Moovy Helsinki Oy",  # längre vendor-sträng
                category="Parkering",
                amount=12.0,
                currency="EUR",
                receipt_date="2026-04-22",
                subject="Kvitto",
                accounts=_accounts(),
                cost_centers=[],
                vat_rates=[],
                vendor_mappings=[_moovy_mapping()],
            )
        self.assertTrue(
            any(
                "Applied vendor mapping" in m
                and "moovy" in m
                and "67113" in m
                and "25.5" in m
                for m in captured.output
            ),
            f"Expected 'Applied vendor mapping' log line; got: {captured.output}",
        )


class FindVendorMappingTest(unittest.TestCase):
    """Substring-match (case-insensitive) mot vendor_pattern."""

    def test_case_insensitive_substring_match(self):
        from app.services.bezala_field_mapper import find_vendor_mapping

        m = find_vendor_mapping("Moovy Helsinki Oy", [_moovy_mapping()])
        self.assertIsNotNone(m)
        self.assertEqual(m["bezala_account_id"], 67113)

    def test_returns_none_when_vendor_missing(self):
        from app.services.bezala_field_mapper import find_vendor_mapping

        self.assertIsNone(find_vendor_mapping(None, [_moovy_mapping()]))
        self.assertIsNone(find_vendor_mapping("", [_moovy_mapping()]))

    def test_returns_none_when_no_match(self):
        from app.services.bezala_field_mapper import find_vendor_mapping

        self.assertIsNone(
            find_vendor_mapping("Finnair", [_moovy_mapping()]),
        )

    def test_empty_mappings(self):
        from app.services.bezala_field_mapper import find_vendor_mapping

        self.assertIsNone(find_vendor_mapping("Moovy", []))
        self.assertIsNone(find_vendor_mapping("Moovy", None))


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    unittest.main()
