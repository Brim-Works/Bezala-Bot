"""FAS 11.5.1 — tester för per_diem_calculator service.

Testklasser:
  - FullDygnTest:           kompletta 24h-dygn (kotimaa/ulkomaa)
  - PartialDygnTest:        sista deldygn-regler
  - ShortForeignTripTest:   <10h utomlands → kotimaan-regler
  - MealDeductionTest:      mat-avdrag (toggles)
  - EndToEndTest:           realistiska scenarion (Stockholm-resa etc.)
  - RateLookupTest:         saknade rates, fallback
  - PartialDygnEdgeCasesTest: gränsvärden (exakt 6h/10h etc.)
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from decimal import Decimal

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


def _setup_db():
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
    from app.db import Base, _seed_per_diem_rates
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=db_module.engine)
    _seed_per_diem_rates()
    return db_module


def _trip(departure, ret, country="FI"):
    """Bygg en in-memory Trip för calculator-tester."""
    from app.models import Trip
    from datetime import date as _date
    return Trip(
        title="Test",
        start_date=departure.date() if isinstance(departure, datetime) else _date.today(),
        end_date=ret.date() if isinstance(ret, datetime) else _date.today(),
        base_currency="EUR",
        status="active",
        departure_home_at=departure,
        return_home_at=ret,
        destination_country=country,
    )


class FullDygnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()
        cls.SessionLocal = cls.db_module.SessionLocal

    def test_full_24h_domestic(self):
        """Komplett dygn inrikes — heldag 54 €."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 6, 0),
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertNotIn("error", result)
        self.assertEqual(len(result["dygnet"]), 1)
        self.assertEqual(result["dygnet"][0]["type"], "full_day_domestic")
        self.assertEqual(result["dygnet"][0]["final_amount"], 54.00)
        self.assertEqual(result["total_amount"], 54.00)

    def test_full_24h_abroad_sweden(self):
        """Komplett dygn Sverige — full ulkomaanpäiväraha 70 €."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 6, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(result["dygnet"][0]["type"], "full_day_abroad")
        self.assertEqual(result["dygnet"][0]["final_amount"], 70.00)
        self.assertEqual(result["total_amount"], 70.00)
        self.assertEqual(result["effective_country_used"], "SE")
        self.assertFalse(result["is_short_foreign_trip"])

    def test_two_full_days_abroad(self):
        """Två kompletta dygn Sverige = 140 €."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 3, 6, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 2)
        self.assertEqual(result["total_amount"], 140.00)

    def test_norway_rates(self):
        """Komplett dygn Norge = 78 €."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 6, 0),
            country="NO",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(result["total_amount"], 78.00)


class PartialDygnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()
        cls.SessionLocal = cls.db_module.SessionLocal

    def test_partial_8h_domestic(self):
        """Deldygn 8h inrikes = 25 € (osapäiväraha)."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 14, 0),
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 1)
        self.assertEqual(result["dygnet"][0]["type"], "half_day_domestic")
        self.assertEqual(result["total_amount"], 25.00)

    def test_partial_12h_domestic(self):
        """Deldygn 12h inrikes = 54 € (kokopäiväraha-deldygn)."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 18, 0),
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(result["dygnet"][0]["type"], "full_day_domestic")
        self.assertEqual(result["total_amount"], 54.00)

    def test_partial_4h_abroad(self):
        """Deldygn 4h Sverige (men >10h totalt) → halv ulkomaanpäiväraha 35 €.

        OBS: detta scenario kräver att 4h-deldygnet följer ett komplett
        dygn — annars triggas short-foreign-trip-undantaget.
        """
        from app.services.per_diem_calculator import calculate_per_diem
        # Resa: 24h + 4h = 28h totalt, sista 4h utomlands
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 10, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 2)
        # Dygn 1: 24h Sverige = 70
        # Dygn 2: 4h deldygn Sverige (>2h) = 35 (halv)
        self.assertEqual(result["dygnet"][0]["final_amount"], 70.00)
        self.assertEqual(result["dygnet"][1]["type"], "half_day_abroad")
        self.assertEqual(
            result["dygnet"][1]["rule_applied"],
            "puolikas_ulkomaanpäiväraha_deldygn",
        )
        self.assertEqual(result["dygnet"][1]["final_amount"], 35.00)
        self.assertEqual(result["total_amount"], 105.00)

    def test_partial_12h_abroad(self):
        """Deldygn 12h utomlands = full ulkomaanpäiväraha (>10h)."""
        from app.services.per_diem_calculator import calculate_per_diem
        # 24h + 12h = 36h
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 18, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 2)
        self.assertEqual(result["dygnet"][1]["type"], "full_day_abroad")
        self.assertEqual(
            result["dygnet"][1]["rule_applied"],
            "kokopäiväraha_ulkomaa_deldygn",
        )
        self.assertEqual(result["dygnet"][1]["final_amount"], 70.00)
        self.assertEqual(result["total_amount"], 140.00)

    def test_partial_under_2h_abroad_zero(self):
        """Deldygn ≤2h utomlands = 0 € (inget traktamente)."""
        from app.services.per_diem_calculator import calculate_per_diem
        # 24h + 2h = 26h
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 8, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 1)
        # Bara dygn 1 finns med — deldygnet gav None
        self.assertEqual(result["total_amount"], 70.00)

    def test_partial_under_6h_domestic_zero(self):
        """Deldygn ≤6h inrikes = 0 €."""
        from app.services.per_diem_calculator import calculate_per_diem
        # 5h totalt, inte short-foreign (är inrikes)
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 11, 0),
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 0)
        self.assertEqual(result["total_amount"], 0.0)


class ShortForeignTripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()
        cls.SessionLocal = cls.db_module.SessionLocal

    def test_short_foreign_trip_8h_uses_domestic_rules(self):
        """8h dagstur till Tallinn → kotimaan-regler (25 € halvdag)."""
        from app.services.per_diem_calculator import calculate_per_diem
        # Tallinn finns inte i seed → vi använder LV som proxy
        # Men poängen är att <10h utomlands → kotimaa-regler
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 14, 0),  # 8h
            country="LV",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertTrue(result["is_short_foreign_trip"])
        self.assertEqual(result["effective_country_used"], "FI")
        self.assertEqual(result["total_amount"], 25.00)
        self.assertEqual(result["dygnet"][0]["type"], "half_day_domestic")

    def test_short_foreign_trip_under_6h_zero(self):
        """5h dagstur utomlands → 0 €."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 11, 0),  # 5h
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertTrue(result["is_short_foreign_trip"])
        self.assertEqual(result["total_amount"], 0.0)

    def test_classic_stockholm_business_day_trip(self):
        """Dagstur Stockholm: 06:00 → 20:00 (14h).
        Kort utlandsresa <10h? Nej, 14h.
        Deldygn 14h Sverige = 70 € (full ulkomaanpäiväraha-deldygn >10h)."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 20, 0),  # 14h
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertFalse(result["is_short_foreign_trip"])
        self.assertEqual(result["dygnet"][0]["type"], "full_day_abroad")
        self.assertEqual(result["total_amount"], 70.00)

    def test_short_foreign_trip_just_under_10h(self):
        """9h utomlands → kotimaa-regler. >6h → halvdag inrikes."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 15, 0),  # 9h
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertTrue(result["is_short_foreign_trip"])
        self.assertEqual(result["total_amount"], 25.00)


class MealDeductionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()
        cls.SessionLocal = cls.db_module.SessionLocal

    def test_meal_deduction_full_day(self):
        """Heldag med 2+ måltider → halverat (70€ → 35€)."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 6, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(
                trip, db, meal_toggles={"1": True}
            )
        self.assertTrue(result["dygnet"][0]["meal_deduction"])
        self.assertEqual(result["dygnet"][0]["final_amount"], 35.00)
        self.assertEqual(result["total_amount"], 35.00)
        self.assertTrue(result["user_edited"])

    def test_meal_deduction_not_applied_to_partial_foreign(self):
        """Halv ulkomaanpäiväraha-deldygn halveras INTE av mat."""
        from app.services.per_diem_calculator import calculate_per_diem
        # 24h Sverige + 4h deldygn Sverige
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 10, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(
                trip, db, meal_toggles={"1": False, "2": True}
            )
        # Dygn 1: 70 (oförändrat, mat-toggle av)
        # Dygn 2: 35 (halv ulkomaanpäiväraha — INTE halverat av mat)
        self.assertEqual(result["dygnet"][0]["final_amount"], 70.00)
        self.assertEqual(result["dygnet"][1]["final_amount"], 35.00)
        self.assertFalse(result["dygnet"][1]["meal_deduction"])
        self.assertEqual(result["total_amount"], 105.00)

    def test_meal_deduction_domestic_full_day(self):
        """Heldag inrikes med mat: 54 → 27."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 6, 0),
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(
                trip, db, meal_toggles={"1": True}
            )
        self.assertEqual(result["dygnet"][0]["final_amount"], 27.00)

    def test_apply_meal_deductions_function(self):
        """apply_meal_deductions() returnerar uppdaterad data."""
        from app.services.per_diem_calculator import (
            calculate_per_diem, apply_meal_deductions,
        )
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 3, 6, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            base = calculate_per_diem(trip, db)
        # Originaltotal: 140
        self.assertEqual(base["total_amount"], 140.00)
        updated = apply_meal_deductions(base, {"1": True, "2": False})
        # Dygn 1: 35, Dygn 2: 70 → 105
        self.assertEqual(updated["total_amount"], 105.00)
        self.assertTrue(updated["user_edited"])
        # Original ska inte muteras
        self.assertEqual(base["total_amount"], 140.00)


class EndToEndTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()
        cls.SessionLocal = cls.db_module.SessionLocal

    def test_finnair_stockholm_trip(self):
        """End-to-end: Stockholm-resa
        30 apr 06:00 → 2 maj 20:20
        2 hela dygn (Sverige) + 1 deldygn 14h20min
        = 70 + 70 + 70 = 210 € (deldygn >10h utrikes = full)
        """
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 4, 30, 6, 0),
            datetime(2026, 5, 2, 20, 20),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 3)
        # Sista deldygn 14h20m → full ulkomaanpäiväraha (>10h)
        self.assertEqual(result["dygnet"][2]["rule_applied"],
                         "kokopäiväraha_ulkomaa_deldygn")
        self.assertEqual(result["total_amount"], 210.00)

    def test_return_to_helsinki_after_two_full_days_short_partial(self):
        """Stockholm-resa: 30 apr 09:00 → 2 maj 11:00 (50h)
        Dygn 1: 30 apr 09:00 - 1 maj 09:00 = full 70 €
        Dygn 2: 1 maj 09:00 - 2 maj 09:00 = full 70 €
        Deldygn: 2 maj 09:00 - 11:00 (2h) → 0 € (≤2h utomlands)
        Total: 140 €"""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 4, 30, 9, 0),
            datetime(2026, 5, 2, 11, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 2)
        self.assertEqual(result["total_amount"], 140.00)

    def test_three_day_trip_with_meal_on_middle_day(self):
        """3 hela dygn Sverige med mat på dygn 2: 70 + 35 + 70 = 175."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 4, 6, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(
                trip, db, meal_toggles={"2": True}
            )
        self.assertEqual(len(result["dygnet"]), 3)
        self.assertEqual(result["total_amount"], 175.00)

    def test_calculation_is_deterministic(self):
        """Samma input → samma output (förutom calculated_at)."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 3, 12, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            r1 = calculate_per_diem(trip, db)
            r2 = calculate_per_diem(trip, db)
        self.assertEqual(r1["total_amount"], r2["total_amount"])
        self.assertEqual(r1["dygnet"], r2["dygnet"])


class RateLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()
        cls.SessionLocal = cls.db_module.SessionLocal

    def test_unknown_country_falls_back_to_finland(self):
        """Okänt land → fallback till FI med warning."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 6, 0),
            country="XX",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(result["effective_country_used"], "FI")
        self.assertEqual(result["total_amount"], 54.00)
        self.assertTrue(any("XX" in w for w in result["warnings"]))

    def test_missing_dates_returns_error(self):
        """Saknad avgång eller hemkomst → error."""
        from app.services.per_diem_calculator import calculate_per_diem
        from app.models import Trip
        from datetime import date
        trip = Trip(
            title="X", start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            base_currency="EUR", status="active",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertIn("error", result)

    def test_invalid_date_order_returns_error(self):
        """Hemkomst före avgång → error."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 2, 6, 0),
            datetime(2026, 5, 1, 6, 0),
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertIn("error", result)

    def test_list_supported_countries(self):
        """list_supported_countries returnerar de 4 seedade länderna."""
        from app.services.per_diem_calculator import list_supported_countries
        with self.SessionLocal() as db:
            countries = list_supported_countries(db, 2026)
        codes = {c["country_code"] for c in countries}
        self.assertEqual(codes, {"FI", "SE", "NO", "LV"})


class PartialDygnEdgeCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()
        cls.SessionLocal = cls.db_module.SessionLocal

    def test_exactly_2h_abroad_partial_zero(self):
        """Exakt 2h deldygn utomlands → 0 € (>2h-regel är strikt)."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 2, 8, 0),  # 26h: 24 + 2
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        # Bara dygn 1 finns (deldygn = exakt 2h → ingen)
        self.assertEqual(len(result["dygnet"]), 1)

    def test_exactly_6h_domestic_partial_zero(self):
        """Exakt 6h inrikes → 0 € (>6h-regel är strikt)."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 12, 0),  # 6h
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(len(result["dygnet"]), 0)

    def test_just_over_6h_domestic_half_day(self):
        """6h 1min inrikes → halvdag."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 5, 1, 6, 0),
            datetime(2026, 5, 1, 12, 1),
            country="FI",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(result["total_amount"], 25.00)

    def test_year_picked_from_departure(self):
        """Default year = avgångsårets."""
        from app.services.per_diem_calculator import calculate_per_diem
        trip = _trip(
            datetime(2026, 12, 31, 22, 0),
            datetime(2027, 1, 1, 22, 0),
            country="SE",
        )
        with self.SessionLocal() as db:
            result = calculate_per_diem(trip, db)
        self.assertEqual(result["rules_year"], 2026)


if __name__ == "__main__":
    unittest.main()
