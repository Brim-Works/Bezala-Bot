"""FAS 11.5.1 — endpoint-tester via FastAPI TestClient.

Verifierar:
  POST /api/trips/{id}/extract-flight-times
  POST /api/trips/{id}/calculate-per-diem
  GET  /api/trips/{id}/per-diem
  PATCH /api/trips/{id}/per-diem
  GET  /api/per-diem-rates
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime

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


class PerDiemEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        from app.db import Base, _seed_per_diem_rates
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import (
            ProcessedMessage, Trip, TripFeedback, TripMessage,
        )
        from fastapi.testclient import TestClient

        Base.metadata.create_all(bind=db_module.engine)
        _seed_per_diem_rates()

        SessionLocal = db_module.SessionLocal

        def get_db():
            s = SessionLocal()
            try:
                yield s
            finally:
                s.close()

        from app.db import get_db as original_get_db
        app_module.app.dependency_overrides[original_get_db] = get_db

        async def fake_require_auth():
            return None

        app_module.app.dependency_overrides[app_module.require_auth] = fake_require_auth
        cls.client = TestClient(app_module.app)
        cls.app_module = app_module
        cls.SessionLocal = SessionLocal
        cls.ProcessedMessage = ProcessedMessage
        cls.Trip = Trip
        cls.TripMessage = TripMessage
        cls.TripFeedback = TripFeedback

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.TripFeedback).delete()
            db.query(self.TripMessage).delete()
            db.query(self.Trip).delete()
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _make_trip(self, **overrides):
        defaults = dict(
            title="Stockholm",
            start_date=date(2026, 4, 30),
            end_date=date(2026, 5, 2),
            base_currency="EUR",
            status="active",
        )
        defaults.update(overrides)
        with self.SessionLocal() as db:
            trip = self.Trip(**defaults)
            db.add(trip)
            db.commit()
            db.refresh(trip)
            return trip.id

    def test_calculate_per_diem_full_flow(self):
        trip_id = self._make_trip()
        resp = self.client.post(
            f"/api/trips/{trip_id}/calculate-per-diem",
            json={
                "departure_home_at": "2026-04-30T06:00:00",
                "return_home_at": "2026-05-02T20:00:00",
                "destination_country": "SE",
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["destination_country"], "SE")
        # 2 hela dygn + 14h deldygn (>10h) → 70 + 70 + 70 = 210
        self.assertEqual(data["total_amount"], 210.0)

        # Verifiera persistens
        get_resp = self.client.get(f"/api/trips/{trip_id}/per-diem")
        self.assertEqual(get_resp.status_code, 200)
        get_data = get_resp.json()
        self.assertEqual(get_data["per_diem_amount"], 210.0)
        self.assertEqual(get_data["destination_country"], "SE")
        self.assertEqual(get_data["calculation"]["total_amount"], 210.0)

    def test_calculate_missing_dates_returns_400(self):
        trip_id = self._make_trip()
        resp = self.client.post(
            f"/api/trips/{trip_id}/calculate-per-diem",
            json={"destination_country": "SE"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_calculate_invalid_country_400(self):
        trip_id = self._make_trip()
        resp = self.client.post(
            f"/api/trips/{trip_id}/calculate-per-diem",
            json={
                "departure_home_at": "2026-05-01T06:00:00",
                "return_home_at": "2026-05-02T06:00:00",
                "destination_country": "FOO",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_patch_meal_toggles_recalculates(self):
        trip_id = self._make_trip()
        # Initial calculate
        self.client.post(
            f"/api/trips/{trip_id}/calculate-per-diem",
            json={
                "departure_home_at": "2026-05-01T06:00:00",
                "return_home_at": "2026-05-02T06:00:00",
                "destination_country": "SE",
            },
        )
        # Toggle mat på dygn 1 → halverat
        resp = self.client.patch(
            f"/api/trips/{trip_id}/per-diem",
            json={"meal_toggles": {"1": True}},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["total_amount"], 35.0)
        self.assertTrue(data["user_edited"])

    def test_get_per_diem_empty_when_not_calculated(self):
        trip_id = self._make_trip()
        resp = self.client.get(f"/api/trips/{trip_id}/per-diem")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["per_diem_amount"])
        self.assertIsNone(data["calculation"])

    def test_extract_flight_times_no_flights(self):
        trip_id = self._make_trip()
        resp = self.client.post(
            f"/api/trips/{trip_id}/extract-flight-times",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("warnings", data)
        self.assertEqual(data["flights_extracted"], [])

    def test_get_per_diem_rates(self):
        resp = self.client.get("/api/per-diem-rates")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        codes = {r["country_code"] for r in data["rates"]}
        self.assertEqual(codes, {"FI", "SE", "NO", "LV"})

    def test_get_per_diem_rates_filtered_by_year(self):
        resp = self.client.get("/api/per-diem-rates?year=2026")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["year"], 2026)
        self.assertEqual(len(data["rates"]), 4)

    def test_calculate_per_diem_with_meal_toggles_inline(self):
        trip_id = self._make_trip()
        resp = self.client.post(
            f"/api/trips/{trip_id}/calculate-per-diem",
            json={
                "departure_home_at": "2026-05-01T06:00:00",
                "return_home_at": "2026-05-03T06:00:00",
                "destination_country": "SE",
                "meal_toggles": {"1": False, "2": True},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        # 70 + 35 = 105
        self.assertEqual(resp.json()["total_amount"], 105.0)

    def test_calculate_404_for_unknown_trip(self):
        resp = self.client.post(
            "/api/trips/99999/calculate-per-diem",
            json={
                "departure_home_at": "2026-05-01T06:00:00",
                "return_home_at": "2026-05-02T06:00:00",
                "destination_country": "FI",
            },
        )
        self.assertEqual(resp.status_code, 404)

    def test_serialize_trip_includes_per_diem(self):
        trip_id = self._make_trip()
        self.client.post(
            f"/api/trips/{trip_id}/calculate-per-diem",
            json={
                "departure_home_at": "2026-05-01T06:00:00",
                "return_home_at": "2026-05-02T06:00:00",
                "destination_country": "SE",
            },
        )
        resp = self.client.get(f"/api/trips/{trip_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["destination_country"], "SE")
        self.assertEqual(data["per_diem_amount"], 70.0)
        self.assertIn("per_diem_calculation", data)


if __name__ == "__main__":
    unittest.main()
