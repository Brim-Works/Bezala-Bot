"""Backend-tester för Gate 0-groundwork: Bezala field-mapper + metadata-
endpoints + förbättrad 422-loggning.

Täcker:
- category_to_account_name: Flyg/Resa/AI/Hotell/okänt → rätt finska namn
- sender_to_country: .fi/.se/.com (inkl. anthropic.com override)
- select_account: substring-matchar Bezala-kontonamn
- select_default_cost_center: default-flagga > env-preferens > första
- select_vat_rate: FI transport/standard + EU/non-EU
- build_transaction_extras: slutlig dict innehåller mappade IDs
- BezalaClient._log_response loggar response.text vid 422
- list_accounts/list_cost_centers/list_vat_rates parsar olika
  response-former (lista vs {items: [...]})
- list_vat_rates faller tillbaka på /vat_codes vid 404
"""

from __future__ import annotations

import logging
import os
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")


# ============================================================
# field-mapper — pure functions
# ============================================================


class CategoryToAccountTest(unittest.TestCase):
    def test_flyg_maps_to_matkaliput(self):
        from app.services.bezala_field_mapper import category_to_account_name

        self.assertEqual(category_to_account_name("Flyg"), "Matkaliput")
        self.assertEqual(category_to_account_name("FLYG"), "Matkaliput")
        self.assertEqual(category_to_account_name(" flyg "), "Matkaliput")

    def test_programvara_and_ai_map_to_atk(self):
        from app.services.bezala_field_mapper import (
            category_to_account_id,
            category_to_account_name,
        )

        # Programvara/SaaS → Atk-ohjelmistot (id 82612)
        self.assertEqual(category_to_account_id("Programvara"), 82612)
        self.assertEqual(category_to_account_id("SaaS"), 82612)
        # AI-tjänster → dedikerad AI-työkalut-post (id 166648)
        self.assertEqual(category_to_account_id("AI"), 166648)
        # Namn-API (bakåtkompat)
        self.assertEqual(
            category_to_account_name("Programvara"),
            "Atk-ohjelmistot, päivitykset ja yp",
        )
        self.assertEqual(category_to_account_name("AI"), "AI työkalut")

    def test_hotell_and_boende_map_to_hotelli(self):
        from app.services.bezala_field_mapper import category_to_account_name

        self.assertEqual(category_to_account_name("Hotell"), "Hotelli-ym. majoitus")
        self.assertEqual(category_to_account_name("Boende"), "Hotelli-ym. majoitus")

    def test_resa_transport_map_to_matkaliput(self):
        """Spec: resa + transport räknas som 'Matkaliput' (matkabiljetter)
        — inte 'Muut matkakulut'. 67100 är den breda resa-kontot."""
        from app.services.bezala_field_mapper import (
            category_to_account_id,
            category_to_account_name,
        )

        self.assertEqual(category_to_account_id("Resa"), 67100)
        self.assertEqual(category_to_account_id("Transport"), 67100)
        self.assertEqual(category_to_account_name("Resa"), "Matkaliput")

    def test_unknown_or_empty_defaults_to_muut(self):
        from app.services.bezala_field_mapper import (
            DEFAULT_ACCOUNT_ID,
            DEFAULT_ACCOUNT_NAME,
            category_to_account_id,
            category_to_account_name,
        )

        self.assertEqual(category_to_account_name(None), DEFAULT_ACCOUNT_NAME)
        self.assertEqual(category_to_account_name(""), DEFAULT_ACCOUNT_NAME)
        # "Annat" är explicit mappad till default
        self.assertEqual(category_to_account_id("Annat"), DEFAULT_ACCOUNT_ID)
        # Helt okänd kategori → default
        self.assertEqual(category_to_account_id("bananpaj"), DEFAULT_ACCOUNT_ID)


class SenderToCountryTest(unittest.TestCase):
    def test_finnish_tld_is_fi(self):
        from app.services.bezala_field_mapper import sender_to_country

        self.assertEqual(sender_to_country("noreply@moovy.fi"), "fi")
        self.assertEqual(sender_to_country("Moovy <kvitto@moovy.fi>"), "fi")

    def test_swedish_tld_is_eu(self):
        from app.services.bezala_field_mapper import sender_to_country

        self.assertEqual(sender_to_country("noreply@sl.se"), "eu")
        self.assertEqual(sender_to_country("noreply@skanetrafiken.se"), "eu")

    def test_norwegian_tld_is_non_eu(self):
        """Norge är INTE EU — ska hamna i non-eu."""
        from app.services.bezala_field_mapper import sender_to_country

        self.assertEqual(sender_to_country("flytoget@flytoget.no"), "non-eu")

    def test_anthropic_override_is_non_eu(self):
        """anthropic.com är US → non-eu (override, annars hade .com blivit non-eu ändå)."""
        from app.services.bezala_field_mapper import sender_to_country

        self.assertEqual(
            sender_to_country("invoice+statements@mail.anthropic.com"),
            "non-eu",
        )
        self.assertEqual(sender_to_country("billing@anthropic.com"), "non-eu")

    def test_finnair_override_is_fi(self):
        """finnair.com (.com) vet vi är FI via override."""
        from app.services.bezala_field_mapper import sender_to_country

        self.assertEqual(sender_to_country("noreply@finnair.com"), "fi")

    def test_unknown_com_is_non_eu(self):
        from app.services.bezala_field_mapper import sender_to_country

        self.assertEqual(sender_to_country("receipts@example.com"), "non-eu")

    def test_empty_sender_defaults_to_non_eu(self):
        from app.services.bezala_field_mapper import sender_to_country

        self.assertEqual(sender_to_country(None), "non-eu")
        self.assertEqual(sender_to_country(""), "non-eu")


class SelectAccountTest(unittest.TestCase):
    """select_account gör ID-lookup primärt och namn-fallback sekundärt.
    Konto-IDs här matchar produktionens live-värden (67100, 67102, etc.)."""

    def _accounts(self):
        return [
            {"id": 67100, "name": "Matkaliput", "default_vat_id": 1355},
            {"id": 67101, "name": "Taksikulut", "default_vat_id": 1355},
            {"id": 67102, "name": "Hotelli-ym. majoitus", "default_vat_id": 1355},
            {"id": 67110, "name": "Muut matkakulut", "default_vat_id": None},
            {"id": 67113, "name": "Paikoituskulut", "default_vat_id": 864},
            {"id": 82612, "name": "Atk-ohjelmistot, päivitykset ja yp", "default_vat_id": None},
            {"id": 166648, "name": "AI työkalut", "default_vat_id": None},
        ]

    def test_flyg_picks_matkaliput(self):
        from app.services.bezala_field_mapper import select_account

        row = select_account(self._accounts(), "Flyg")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 67100)

    def test_ai_picks_ai_työkalut(self):
        from app.services.bezala_field_mapper import select_account

        row = select_account(self._accounts(), "AI")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 166648)
        self.assertEqual(row["name"], "AI työkalut")

    def test_programvara_picks_atk(self):
        from app.services.bezala_field_mapper import select_account

        row = select_account(self._accounts(), "Programvara")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 82612)

    def test_parkering_picks_paikoituskulut(self):
        """Moovy/parkering har eget konto separat från taxi."""
        from app.services.bezala_field_mapper import select_account

        row = select_account(self._accounts(), "Parkering")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 67113)

    def test_unknown_picks_muut_matkakulut(self):
        from app.services.bezala_field_mapper import select_account

        row = select_account(self._accounts(), "bananpaj")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 67110)

    def test_name_fallback_when_id_not_in_live_list(self):
        """Om Bezala byter ID på ett konto (sällsynt) → namn-match som
        säkerhetsnät. Här har 'AI työkalut' nytt ID 999999 — namn-match
        hittar det ändå."""
        from app.services.bezala_field_mapper import select_account

        accounts = [{"id": 999999, "name": "AI työkalut"}]
        row = select_account(accounts, "AI")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 999999)


class SelectCostCenterTest(unittest.TestCase):
    def test_default_flag_wins(self):
        from app.services.bezala_field_mapper import select_default_cost_center

        rows = [
            {"id": 1, "name": "Huvudkontor"},
            {"id": 2, "name": "Stockholm", "default": True},
            {"id": 3, "name": "Göteborg"},
        ]
        row = select_default_cost_center(rows)
        self.assertEqual(row["id"], 2)

    def test_preferred_name_matches(self):
        from app.services.bezala_field_mapper import select_default_cost_center

        rows = [
            {"id": 1, "name": "Huvudkontor"},
            {"id": 2, "name": "Stockholm"},
        ]
        row = select_default_cost_center(rows, preferred_name="Stockholm")
        self.assertEqual(row["id"], 2)

    def test_falls_back_to_first(self):
        from app.services.bezala_field_mapper import select_default_cost_center

        rows = [
            {"id": 10, "name": "A"},
            {"id": 11, "name": "B"},
        ]
        row = select_default_cost_center(rows)
        self.assertEqual(row["id"], 10)

    def test_preferred_id_picks_by_id(self):
        """DEFAULT_COST_CENTER_ID matchar via ID innan namn/first."""
        from app.services.bezala_field_mapper import select_default_cost_center

        rows = [
            {"id": 1, "name": "A"},
            {"id": 927151, "name": "VIS128 Visma HRM Sverige AB"},
            {"id": 3, "name": "C"},
        ]
        row = select_default_cost_center(rows)  # default_id = 927151 via env
        self.assertEqual(row["id"], 927151)

    def test_empty_returns_none(self):
        from app.services.bezala_field_mapper import select_default_cost_center

        self.assertIsNone(select_default_cost_center([]))


class BuildVatLinesTest(unittest.TestCase):
    """Ny VAT-strategi: account.default_vat_id är primärt."""

    def test_account_with_default_vat_id(self):
        from app.services.bezala_field_mapper import build_vat_lines

        account = {"id": 67100, "name": "Matkaliput", "default_vat_id": 1355}
        lines = build_vat_lines(503.0, account=account)
        self.assertEqual(lines, [{"amount": 503.0, "vat_code_id": 1355}])

    def test_account_with_null_default_vat_id_returns_empty(self):
        """default_vat_id = None → vat_lines utelämnas (Bezala plockar själv)."""
        from app.services.bezala_field_mapper import build_vat_lines

        account = {"id": 82612, "name": "Atk-ohjelmistot", "default_vat_id": None}
        self.assertEqual(build_vat_lines(100.0, account=account), [])

    def test_vat_rate_row_used_as_fallback(self):
        from app.services.bezala_field_mapper import build_vat_lines

        vat_rate = {"id": 1355, "name": "Finland Transport 13.5%"}
        lines = build_vat_lines(503.0, vat_rate=vat_rate)
        self.assertEqual(lines, [{"amount": 503.0, "vat_code_id": 1355}])

    def test_account_default_vat_id_wins_over_vat_rate_fallback(self):
        from app.services.bezala_field_mapper import build_vat_lines

        account = {"id": 67100, "default_vat_id": 1355}
        vat_rate = {"id": 999, "name": "fallback"}
        lines = build_vat_lines(503.0, account=account, vat_rate=vat_rate)
        self.assertEqual(lines[0]["vat_code_id"], 1355)

    def test_positional_account_dict_detected_via_default_vat_id(self):
        """build_vat_lines(amount, account_row) — positionell form."""
        from app.services.bezala_field_mapper import build_vat_lines

        account = {"id": 67100, "default_vat_id": 1355}
        self.assertEqual(
            build_vat_lines(10.0, account)[0]["vat_code_id"], 1355,
        )

    def test_missing_amount_returns_empty(self):
        from app.services.bezala_field_mapper import build_vat_lines

        account = {"id": 1, "default_vat_id": 1355}
        self.assertEqual(build_vat_lines(None, account=account), [])


class SelectVatRateTest(unittest.TestCase):
    def _vat_rates(self):
        return [
            {"id": 1, "name": "Finland 25.5%"},
            {"id": 2, "name": "Finland Transport 13.5%"},
            {"id": 3, "name": "Purchases Abroad (EU)"},
            {"id": 4, "name": "Purchases Abroad (Non-EU)"},
        ]

    def test_finnish_transport(self):
        from app.services.bezala_field_mapper import select_vat_rate

        row = select_vat_rate(self._vat_rates(), country="fi", category="Flyg")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 2)

    def test_finnish_standard(self):
        from app.services.bezala_field_mapper import select_vat_rate

        row = select_vat_rate(self._vat_rates(), country="fi", category="AI")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 1)

    def test_eu(self):
        from app.services.bezala_field_mapper import select_vat_rate

        row = select_vat_rate(self._vat_rates(), country="eu", category="Hotell")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 3)

    def test_non_eu(self):
        from app.services.bezala_field_mapper import select_vat_rate

        row = select_vat_rate(self._vat_rates(), country="non-eu", category="AI")
        self.assertIsNotNone(row)
        self.assertEqual(row["id"], 4)

    def test_empty_vat_list_returns_none(self):
        from app.services.bezala_field_mapper import select_vat_rate

        self.assertIsNone(select_vat_rate([], country="fi", category="Flyg"))


class BuildDescriptionTest(unittest.TestCase):
    def test_strips_pdf_suffix(self):
        from app.services.bezala_field_mapper import build_description

        self.assertEqual(
            build_description("20260422 Finnair HEL-CPH.pdf"),
            "20260422 Finnair HEL-CPH",
        )
        self.assertEqual(
            build_description("20260422 Finnair HEL-CPH.PDF"),
            "20260422 Finnair HEL-CPH",
        )

    def test_subject_fallback_when_filename_missing(self):
        from app.services.bezala_field_mapper import build_description

        self.assertEqual(
            build_description(None, subject="Kvitto Finnair HEL-CPH"),
            "Kvitto Finnair HEL-CPH",
        )

    def test_vendor_plus_date_fallback(self):
        from app.services.bezala_field_mapper import build_description

        self.assertEqual(
            build_description(None, vendor="Finnair", receipt_date="2026-04-22"),
            "Finnair 2026-04-22",
        )

    def test_never_returns_empty_string(self):
        """Belt-and-suspenders: Bezala 422:ar på tom description."""
        from app.services.bezala_field_mapper import build_description

        self.assertEqual(build_description(None), "Kvitto")
        self.assertEqual(build_description(""), "Kvitto")
        self.assertEqual(build_description("   "), "Kvitto")
        self.assertEqual(build_description(".pdf"), "Kvitto")
        self.assertEqual(build_description(None, fallback=""), "Kvitto")

    def test_explicit_fallback_used(self):
        from app.services.bezala_field_mapper import build_description

        self.assertEqual(build_description(None, fallback="Kvitto X"), "Kvitto X")
        self.assertEqual(build_description("", fallback="Kvitto X"), "Kvitto X")


class BuildTransactionExtrasTest(unittest.TestCase):
    def _accounts(self):
        return [
            {"id": 101, "name": "Matkaliput"},
            {"id": 102, "name": "Muut Matkakulut"},
        ]

    def _cost_centers(self):
        return [{"id": 77, "name": "Default", "default": True}]

    def _vat_rates(self):
        return [
            {"id": 11, "name": "Finland Transport 13.5%"},
            {"id": 12, "name": "Finland 25.5%"},
            {"id": 13, "name": "Purchases Abroad (Non-EU)"},
        ]

    def test_finnish_flight_produces_full_extras(self):
        from app.services.bezala_field_mapper import build_transaction_extras

        extras = build_transaction_extras(
            file_name="20260422 Finnair HEL-CPH flyg.pdf",
            sender="noreply@finnair.com",
            vendor="Finnair",
            category="Flyg",
            receipt_date="2026-04-22",
            accounts=self._accounts(),
            cost_centers=self._cost_centers(),
            vat_rates=self._vat_rates(),
        )
        self.assertEqual(extras["account_id"], 101)  # Matkaliput
        self.assertEqual(extras["cost_center_id"], 77)
        self.assertEqual(extras["vat_rate_id"], 11)  # FI transport 13.5
        self.assertEqual(extras["purchase_date"], "2026-04-22")
        self.assertEqual(extras["description"], "20260422 Finnair HEL-CPH flyg")

    def test_anthropic_us_uses_non_eu_vat(self):
        from app.services.bezala_field_mapper import build_transaction_extras

        extras = build_transaction_extras(
            file_name="20260422 Anthropic API.pdf",
            sender="invoice+statements@mail.anthropic.com",
            vendor="Anthropic",
            category="AI",
            receipt_date="2026-04-22",
            accounts=self._accounts(),
            cost_centers=self._cost_centers(),
            vat_rates=self._vat_rates(),
        )
        # AI → account = Muut (ingen ATK i listan här) OR None — vi har bara
        # Matkaliput/Muut — acceptera Muut Matkakulut som fallback via
        # default-namnet ('ATK…' finns inte → select_account returnerar None).
        # VAT → non-eu
        self.assertEqual(extras["vat_rate_id"], 13)

    def test_missing_accounts_list_skips_account_id(self):
        from app.services.bezala_field_mapper import build_transaction_extras

        extras = build_transaction_extras(
            file_name="x.pdf",
            sender=None,
            vendor=None,
            category="Flyg",
            receipt_date=None,
            accounts=[],
            cost_centers=[],
            vat_rates=[],
        )
        self.assertNotIn("account_id", extras)
        self.assertNotIn("cost_center_id", extras)
        self.assertNotIn("vat_rate_id", extras)
        # description ska fortfarande byggas
        self.assertEqual(extras["description"], "x")


# ============================================================
# BezalaClient — 422-loggning och metadata-parsning
# ============================================================


def _make_client():
    """Skapar en BezalaClient utan att autentisera mot nätet."""
    from app.services.bezala_client import BezalaClient

    client = BezalaClient.__new__(BezalaClient)
    client._email = "test@example.com"
    client._password = "secret"
    client._base_url = "https://mock.bezala"
    client._client = MagicMock()
    client._token = "fake-token"
    client._token_expires_at = 9e18  # aldrig löper ut under testet
    return client


class BezalaLoggingTest(unittest.TestCase):
    """Verifiera att 422-responsen loggas med full response.text."""

    def test_log_response_422_includes_full_body(self):
        from app.services.bezala_client import _log_response

        resp = MagicMock()
        resp.status_code = 422
        resp.headers = {"content-type": "application/json", "x-request-id": "abc-123"}
        resp.text = (
            '{"errors":[{"field":"account_id","message":"required"},'
            '{"field":"cost_center_id","message":"required"}]}'
        )

        with self.assertLogs("app.services.bezala_client", level="ERROR") as cm:
            _log_response(resp, "POST", "/transactions", payload_keys=["attachment_ids", "amount"])

        joined = "\n".join(cm.output)
        self.assertIn("422", joined)
        self.assertIn("account_id", joined)
        self.assertIn("cost_center_id", joined)
        self.assertIn("x-request-id", joined)
        self.assertIn("attachment_ids", joined)

    def test_log_response_2xx_not_error_level(self):
        from app.services.bezala_client import _log_response

        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = '{"id": 1}'

        logger = logging.getLogger("app.services.bezala_client")
        before_level = logger.level
        try:
            logger.setLevel(logging.DEBUG)
            with self.assertLogs("app.services.bezala_client", level="DEBUG") as cm:
                _log_response(resp, "GET", "/accounts")
        finally:
            logger.setLevel(before_level)

        for line in cm.output:
            # 2xx ska INTE vara ERROR
            self.assertNotIn("ERROR", line)

    def test_create_transaction_422_bubbles_full_body(self):
        """Vid 422 ska BezalaError.body innehålla hela response.text."""
        from app.services.bezala_client import BezalaError

        client = _make_client()
        resp = MagicMock()
        resp.status_code = 422
        resp.headers = {"content-type": "application/json"}
        # 800 tecken — tidigare skulle trunkerats vid 500
        resp.text = '{"detail":"' + ("x" * 800) + '"}'

        def fake_request(method, url, **kwargs):
            return resp

        client._client.request = fake_request

        with self.assertRaises(BezalaError) as ctx:
            client.create_transaction(
                description="Test",
                date="2026-04-22",
                credit_account_id=67100,
            )
        err = ctx.exception
        self.assertEqual(err.status_code, 422)
        # BODY_LOG_LIMIT = 4000 → all ~800-tecken text ska rymmas
        self.assertGreater(len(err.body), 700)
        self.assertIn("xxxx", err.body)

    def test_create_transaction_sends_nested_body_with_new_field_names(self):
        """Senaste API-docs: POST /transactions body är
        {'transaction': {description, date, credit_account_id,
        vat_lines_attributes: [...]}}. amount/currency/vendor/cost_center
        ligger INTE top-level."""
        captured = {}

        client = _make_client()
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"content-type": "application/json"}
        resp.text = '{"id": "tx-42"}'
        resp.json = MagicMock(return_value={"id": "tx-42"})

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return resp

        client._client.request = fake_request

        vat_line = {
            "taxable": "503.00",
            "tax_percentage": "0.255",
            "currency": "EUR",
            "expense_account_id": 67100,
            "cost_center_ids": [927151],
            "vat_code_id": 1355,
        }
        result = client.create_transaction(
            description="Finnair HEL-CPH",
            date="2026-04-22",
            credit_account_id=67100,
            vat_lines_attributes=[vat_line],
        )
        self.assertEqual(result.transaction_id, "tx-42")
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["url"].endswith("/transactions"))

        payload = captured["json"]
        self.assertIn("transaction", payload)
        tx = payload["transaction"]
        self.assertEqual(tx["description"], "Finnair HEL-CPH")
        self.assertEqual(tx["date"], "2026-04-22")
        self.assertEqual(tx["credit_account_id"], 67100)
        self.assertEqual(tx["vat_lines_attributes"], [vat_line])
        # Gamla fält ska INTE finnas top-level
        self.assertNotIn("amount", tx)
        self.assertNotIn("currency", tx)
        self.assertNotIn("vendor", tx)
        self.assertNotIn("cost_center_id", tx)
        self.assertNotIn("account_id", tx)
        self.assertNotIn("vat_lines", tx)


class BezalaMetadataEndpointsTest(unittest.TestCase):
    """Verifiera att list_accounts/list_cost_centers/list_vat_rates
    parsar olika response-former."""

    def _mock_get(self, client, payload, *, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.headers = {"content-type": "application/json"}
        resp.text = str(payload)
        resp.json = MagicMock(return_value=payload)

        def fake_request(method, url, **kwargs):
            return resp

        client._client.request = fake_request
        return resp

    def test_list_accounts_toplevel_array(self):
        client = _make_client()
        self._mock_get(client, [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
        rows = client.list_accounts()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], 1)

    def test_list_accounts_wrapped_in_items(self):
        client = _make_client()
        self._mock_get(client, {"items": [{"id": 10, "name": "X"}]})
        rows = client.list_accounts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 10)

    def test_list_accounts_wrapped_in_data(self):
        client = _make_client()
        self._mock_get(client, {"data": [{"id": 99, "name": "Z"}]})
        rows = client.list_accounts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 99)

    def test_list_cost_centers_empty_on_unexpected_shape(self):
        client = _make_client()
        self._mock_get(client, {"weird": "response"})
        rows = client.list_cost_centers()
        self.assertEqual(rows, [])

    def test_list_vat_rates_falls_back_to_vat_codes_on_404(self):
        """Om /vat_rates returnerar 404 → försök /vat_codes."""
        client = _make_client()
        calls: list[str] = []

        def fake_request(method, url, **kwargs):
            calls.append(url)
            resp = MagicMock()
            resp.headers = {"content-type": "application/json"}
            if url.endswith("/vat_rates"):
                resp.status_code = 404
                resp.text = "not found"
                resp.json = MagicMock(side_effect=ValueError("no json"))
            else:
                resp.status_code = 200
                resp.text = "[]"
                resp.json = MagicMock(return_value=[{"id": 7, "name": "FI 25.5%"}])
            return resp

        client._client.request = fake_request
        rows = client.list_vat_rates()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 7)
        # Båda endpoints ska ha provats
        self.assertTrue(any("/vat_rates" in c for c in calls))
        self.assertTrue(any("/vat_codes" in c for c in calls))

    def test_list_vat_rates_raises_on_non_404_error(self):
        """500 eller annat fel → höjs, INTE fallback."""
        from app.services.bezala_client import BezalaError

        client = _make_client()
        resp = MagicMock()
        resp.status_code = 500
        resp.headers = {"content-type": "application/json"}
        resp.text = "server error"
        resp.json = MagicMock(side_effect=ValueError("no json"))

        def fake_request(method, url, **kwargs):
            return resp

        client._client.request = fake_request
        with self.assertRaises(BezalaError) as ctx:
            client.list_vat_rates()
        self.assertEqual(ctx.exception.status_code, 500)


if __name__ == "__main__":
    unittest.main()
