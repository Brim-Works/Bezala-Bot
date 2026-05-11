"""PR 1 — utöka Bezala-mappning. Tester för:

- _ALLOWED_CATEGORIES innehåller alla 17 kategorier (16 + Annat).
- AI-prompten nämner de nya kategorierna.
- _normalize_category (case, alias, fallback).
- get_account_id_for_category (case-insensitive, ÅÄÖ, fallback, None-värde).
- get_default_vat_for_country (FI/SE/NO + okänt).
- tax_percentage_for_vat_code med country-fallback.
- VAT_PERCENTAGE_BY_CODE lookups.

Regression-testerna i test_receipt_analyzer / test_bezala_mapping
fortsätter köras separat.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("BEZALA_USERNAME", "x")
os.environ.setdefault("BEZALA_PASSWORD", "x")
os.environ.setdefault("SCAN_ENABLED", "false")


class AllowedCategoriesTest(unittest.TestCase):
    def test_all_seventeen_categories_present(self):
        from app.services.receipt_analyzer import _ALLOWED_CATEGORIES
        expected = {
            "Flyg", "Tåg", "Kollektivtrafik", "Taxi", "Bilhyra",
            "Parkering", "Hotell", "Mat", "AI-verktyg", "Mjukvara",
            "Telefon", "Datakommunikation", "Böcker", "Utbildning",
            "Representation", "Kontorsmaterial", "Annat",
        }
        self.assertEqual(_ALLOWED_CATEGORIES, expected)

    def test_system_prompt_lists_new_categories(self):
        from app.services.receipt_analyzer import SYSTEM_PROMPT
        for cat in ("Tåg", "Bilhyra", "AI-verktyg", "Telefon",
                    "Datakommunikation", "Böcker", "Utbildning",
                    "Representation", "Kontorsmaterial"):
            self.assertIn(cat, SYSTEM_PROMPT,
                          f"SYSTEM_PROMPT saknar {cat!r}")


class NormalizeCategoryTest(unittest.TestCase):
    def test_none_returns_none(self):
        from app.services.receipt_analyzer import _normalize_category
        self.assertIsNone(_normalize_category(None))
        self.assertIsNone(_normalize_category(""))
        self.assertIsNone(_normalize_category("   "))

    def test_exact_match_preserved(self):
        from app.services.receipt_analyzer import _normalize_category
        self.assertEqual(_normalize_category("Flyg"), "Flyg")
        self.assertEqual(_normalize_category("AI-verktyg"), "AI-verktyg")

    def test_case_insensitive(self):
        from app.services.receipt_analyzer import _normalize_category
        self.assertEqual(_normalize_category("flyg"), "Flyg")
        self.assertEqual(_normalize_category("HOTELL"), "Hotell")
        self.assertEqual(_normalize_category("Tåg"), "Tåg")

    def test_english_aliases(self):
        from app.services.receipt_analyzer import _normalize_category
        self.assertEqual(_normalize_category("flight"), "Flyg")
        self.assertEqual(_normalize_category("parking"), "Parkering")
        self.assertEqual(_normalize_category("hotel"), "Hotell")
        self.assertEqual(_normalize_category("car rental"), "Bilhyra")
        self.assertEqual(_normalize_category("software"), "Mjukvara")
        self.assertEqual(_normalize_category("phone"), "Telefon")

    def test_finnish_aliases(self):
        from app.services.receipt_analyzer import _normalize_category
        self.assertEqual(_normalize_category("pysäköinti"), "Parkering")
        self.assertEqual(_normalize_category("pysakointi"), "Parkering")
        self.assertEqual(_normalize_category("hotelli"), "Hotell")
        self.assertEqual(_normalize_category("taksi"), "Taxi")
        self.assertEqual(_normalize_category("ruoka"), "Mat")
        self.assertEqual(_normalize_category("tekoaly"), "AI-verktyg")
        self.assertEqual(_normalize_category("koulutus"), "Utbildning")

    def test_unknown_falls_back_to_annat(self):
        from app.services.receipt_analyzer import _normalize_category
        self.assertEqual(_normalize_category("Underwater Basket Weaving"), "Annat")


class AccountIdLookupTest(unittest.TestCase):
    def test_verified_categories_map_correctly(self):
        from app.services.bezala_field_mapper import get_account_id_for_category
        # Verifierade i prod
        self.assertEqual(get_account_id_for_category("Flyg"), 67100)
        self.assertEqual(get_account_id_for_category("Taxi"), 67101)
        self.assertEqual(get_account_id_for_category("Hotell"), 67102)
        self.assertEqual(get_account_id_for_category("Parkering"), 67113)
        self.assertEqual(get_account_id_for_category("Mat"), 148404)
        self.assertEqual(get_account_id_for_category("Representation"), 67097)
        self.assertEqual(get_account_id_for_category("Kontorsmaterial"), 67107)
        self.assertEqual(get_account_id_for_category("AI-verktyg"), 166648)
        self.assertEqual(get_account_id_for_category("Mjukvara"), 82612)
        self.assertEqual(get_account_id_for_category("Annat"), 67110)

    def test_case_insensitive(self):
        from app.services.bezala_field_mapper import get_account_id_for_category
        self.assertEqual(get_account_id_for_category("flyg"), 67100)
        self.assertEqual(get_account_id_for_category("FLYG"), 67100)
        self.assertEqual(get_account_id_for_category(" Flyg "), 67100)

    def test_aoa_aliases(self):
        from app.services.bezala_field_mapper import get_account_id_for_category
        # Tåg ↔ tag är aliases utan diakritik
        self.assertEqual(get_account_id_for_category("Tåg"),
                         get_account_id_for_category("tag"))
        # Böcker har inget verifierat ID (None) — båda formerna går till
        # fallback (Muut matkakulut = 67110).
        self.assertEqual(get_account_id_for_category("Böcker"), 67110)
        self.assertEqual(get_account_id_for_category("bocker"), 67110)

    def test_unmapped_category_uses_fallback(self):
        from app.services.bezala_field_mapper import get_account_id_for_category
        # "Telefon" har None som värde — ska falla tillbaka till annat
        self.assertEqual(get_account_id_for_category("Telefon"), 67110)
        self.assertEqual(get_account_id_for_category("Datakommunikation"), 67110)
        self.assertEqual(get_account_id_for_category("Utbildning"), 67110)
        self.assertEqual(get_account_id_for_category("Bilhyra"), 67110)

    def test_unknown_category_uses_fallback(self):
        from app.services.bezala_field_mapper import get_account_id_for_category
        self.assertEqual(get_account_id_for_category("Helt påhittad kategori"),
                         67110)
        self.assertEqual(get_account_id_for_category(None), 67110)
        self.assertEqual(get_account_id_for_category(""), 67110)

    def test_legacy_wrapper_returns_same_id(self):
        from app.services.bezala_field_mapper import (
            category_to_account_id, get_account_id_for_category,
        )
        self.assertEqual(
            category_to_account_id("Parkering"),
            get_account_id_for_category("Parkering"),
        )


class CountryVatFallbackTest(unittest.TestCase):
    def test_known_countries(self):
        from app.services.bezala_field_mapper import get_default_vat_for_country
        self.assertEqual(get_default_vat_for_country("fi"), "0.255")
        self.assertEqual(get_default_vat_for_country("se"), "0.25")
        self.assertEqual(get_default_vat_for_country("no"), "0.25")
        self.assertEqual(get_default_vat_for_country("dk"), "0.25")
        self.assertEqual(get_default_vat_for_country("ee"), "0.22")

    def test_sender_to_country_keys_supported(self):
        """get_default_vat_for_country tar emot både ISO-koder och
        sender_to_country-värden ('fi'/'eu'/'non-eu')."""
        from app.services.bezala_field_mapper import get_default_vat_for_country
        self.assertEqual(get_default_vat_for_country("eu"), "0.0")
        self.assertEqual(get_default_vat_for_country("non-eu"), "0.0")

    def test_case_insensitive(self):
        from app.services.bezala_field_mapper import get_default_vat_for_country
        self.assertEqual(get_default_vat_for_country("FI"), "0.255")
        self.assertEqual(get_default_vat_for_country("SE"), "0.25")

    def test_unknown_country_falls_back_to_fi(self):
        from app.services.bezala_field_mapper import get_default_vat_for_country
        # Okänt land → FI-default (säkrare än 0%)
        self.assertEqual(get_default_vat_for_country("jp"), "0.255")
        self.assertEqual(get_default_vat_for_country(None), "0.255")
        self.assertEqual(get_default_vat_for_country(""), "0.255")


class VatPercentageLookupTest(unittest.TestCase):
    def test_verified_fi_codes(self):
        from app.services.bezala_field_mapper import tax_percentage_for_vat_code
        self.assertEqual(tax_percentage_for_vat_code(1355), "0.255")
        self.assertEqual(tax_percentage_for_vat_code(864), "0.14")
        self.assertEqual(tax_percentage_for_vat_code(1), "0.0")

    def test_string_code_is_coerced(self):
        from app.services.bezala_field_mapper import tax_percentage_for_vat_code
        self.assertEqual(tax_percentage_for_vat_code("1355"), "0.255")

    def test_none_uses_country_fallback(self):
        from app.services.bezala_field_mapper import tax_percentage_for_vat_code
        self.assertEqual(
            tax_percentage_for_vat_code(None, country="fi"), "0.255",
        )
        self.assertEqual(
            tax_percentage_for_vat_code(None, country="se"), "0.25",
        )

    def test_unknown_code_uses_country_fallback(self):
        from app.services.bezala_field_mapper import tax_percentage_for_vat_code
        self.assertEqual(
            tax_percentage_for_vat_code(999999, country="se"), "0.25",
        )
        # Utan country: FI-default
        self.assertEqual(tax_percentage_for_vat_code(999999), "0.255")

    def test_invalid_string_uses_fallback(self):
        from app.services.bezala_field_mapper import tax_percentage_for_vat_code
        self.assertEqual(
            tax_percentage_for_vat_code("abc", country="no"), "0.25",
        )


if __name__ == "__main__":
    unittest.main()
