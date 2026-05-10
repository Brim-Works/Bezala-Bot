"""FAS 11.1.1 — tester för:
- ExcludedVendor service (seed + add/remove + match)
- striktare trip_grouper (datum-fönster, kategori, SaaS-exklusion)
- link/unlink/available-trips endpoints
- /api/excluded-vendors CRUD
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime
from unittest.mock import patch

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


# ---------- Pure-funktion tester ----------


class IsVendorExcludedTest(unittest.TestCase):
    def test_substring_match(self):
        from app.services.excluded_vendors import is_vendor_excluded
        self.assertTrue(is_vendor_excluded("Anthropic, PBC", ["anthropic"]))

    def test_case_insensitive(self):
        from app.services.excluded_vendors import is_vendor_excluded
        self.assertTrue(is_vendor_excluded("SPOTIFY AB", ["spotify"]))

    def test_no_match(self):
        from app.services.excluded_vendors import is_vendor_excluded
        self.assertFalse(is_vendor_excluded("Finnair", ["spotify", "anthropic"]))

    def test_empty_vendor(self):
        from app.services.excluded_vendors import is_vendor_excluded
        self.assertFalse(is_vendor_excluded(None, ["spotify"]))
        self.assertFalse(is_vendor_excluded("", ["spotify"]))

    def test_empty_patterns(self):
        from app.services.excluded_vendors import is_vendor_excluded
        self.assertFalse(is_vendor_excluded("Anthropic", []))


# ---------- DB-tester ----------


class ExcludedVendorServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=db_module.engine)
        cls.SessionLocal = db_module.SessionLocal

    def setUp(self):
        from app.models import ExcludedVendor, MaintenanceTask
        with self.SessionLocal() as db:
            db.query(ExcludedVendor).delete()
            db.query(MaintenanceTask).delete()
            db.commit()

    def test_seed_idempotent(self):
        from app.services.excluded_vendors import (
            DEFAULT_EXCLUDED_VENDORS, seed_default_vendors,
        )
        with self.SessionLocal() as db:
            added = seed_default_vendors(db)
        self.assertEqual(added, len(DEFAULT_EXCLUDED_VENDORS))

        with self.SessionLocal() as db:
            second = seed_default_vendors(db)
        self.assertEqual(second, 0)

    def test_seed_inserts_default_list(self):
        from app.models import ExcludedVendor
        from app.services.excluded_vendors import seed_default_vendors
        with self.SessionLocal() as db:
            seed_default_vendors(db)
            patterns = {
                v.vendor_pattern for v in db.query(ExcludedVendor).all()
            }
        self.assertIn("anthropic", patterns)
        self.assertIn("spotify", patterns)
        self.assertIn("aws", patterns)

    def test_add_user_vendor_idempotent(self):
        from app.services.excluded_vendors import add_user_vendor
        with self.SessionLocal() as db:
            row1, already1 = add_user_vendor(db, "Mitt-SaaS")
            row2, already2 = add_user_vendor(db, "MITT-SaaS")
        self.assertFalse(already1)
        self.assertEqual(row1.vendor_pattern, "mitt-saas")
        self.assertEqual(row1.added_by, "user")
        self.assertTrue(already2)
        self.assertEqual(row1.id, row2.id)

    def test_remove_vendor(self):
        from app.services.excluded_vendors import (
            add_user_vendor, remove_vendor,
        )
        with self.SessionLocal() as db:
            row, _ = add_user_vendor(db, "test-vendor")
            self.assertTrue(remove_vendor(db, row.id))
            self.assertFalse(remove_vendor(db, row.id))

    def test_user_pattern_survives_seed_rerun(self):
        """User-tillägg ska INTE tas bort när seed:en kör. Seed:en
        är idempotent och gör inte mer än att garantera default-listan
        är inlagd."""
        from app.models import ExcludedVendor
        from app.services.excluded_vendors import (
            add_user_vendor, seed_default_vendors,
        )
        with self.SessionLocal() as db:
            seed_default_vendors(db)
            add_user_vendor(db, "min-egen-tjanst")
        with self.SessionLocal() as db:
            seed_default_vendors(db)
            patterns = {
                v.vendor_pattern for v in db.query(ExcludedVendor).all()
            }
        self.assertIn("min-egen-tjanst", patterns)


# ---------- trip_grouper-integration ----------


class StricterGroupingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=db_module.engine)
        cls.SessionLocal = db_module.SessionLocal

    def setUp(self):
        from app.models import (
            ExcludedVendor, MaintenanceTask, ProcessedMessage,
            Trip, TripFeedback, TripMessage,
        )
        with self.SessionLocal() as db:
            db.query(TripFeedback).delete()
            db.query(TripMessage).delete()
            db.query(Trip).delete()
            db.query(ProcessedMessage).delete()
            db.query(ExcludedVendor).delete()
            db.query(MaintenanceTask).delete()
            db.commit()

    def _seed(self, **over):
        from app.models import ProcessedMessage
        defaults = dict(
            sender="x@example.com",
            subject="Subject",
            received_at=datetime.utcnow(),
            processed_at=datetime.utcnow(),
            status="saved",
            currency="EUR",
            ai_confidence=90,
        )
        defaults.update(over)
        with self.SessionLocal() as db:
            row = ProcessedMessage(**defaults)
            db.add(row)
            db.commit()
            return row.id

    def test_excluded_vendor_filtered_from_proposal(self):
        """Anthropic-prenumeration inom datum-fönstret ska INTE räknas
        som resekvitto även om kategori råkar vara 'Annat'/null."""
        from app.services.excluded_vendors import add_user_vendor
        from app.services.trip_grouper import (
            persist_suggestions, suggest_trips,
        )
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
        self._seed(message_id="hotel", vendor="Scandic", category="Hotell",
                   amount=200.0, receipt_date="2026-05-02")
        # Anthropic-prenumeration som råkar ha kategori "Mat" — skulle
        # tagits med tidigare. Med excluded_vendor tas den bort.
        self._seed(message_id="anthropic", vendor="Anthropic, PBC",
                   category="Mat", amount=20.0,
                   receipt_date="2026-05-02")

        with self.SessionLocal() as db:
            add_user_vendor(db, "anthropic")
            with patch(
                "app.services.trip_grouper.call_claude_for_trip",
                return_value=None,
            ):
                trips = persist_suggestions(
                    db, suggest_trips(db, lookback_days=90),
                )
            self.assertEqual(len(trips), 1)
            trip_id = trips[0].id
        from app.models import TripMessage
        with self.SessionLocal() as db:
            tm_rows = db.query(TripMessage).filter_by(trip_id=trip_id).all()
            ids = {tm.message_id for tm in tm_rows}
        self.assertIn("flight", ids)
        self.assertIn("hotel", ids)
        self.assertNotIn("anthropic", ids)

    def test_non_travel_category_excluded(self):
        """Kvitton i kategori 'Annat' (t.ex. Clas Ohlson kontorsmaterial)
        ska INTE räknas som resekostnad."""
        from app.services.trip_grouper import (
            persist_suggestions, suggest_trips,
        )
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
        self._seed(message_id="hotel", vendor="Scandic", category="Hotell",
                   amount=200.0, receipt_date="2026-05-02")
        self._seed(message_id="clas", vendor="Clas Ohlson", category="Annat",
                   amount=120.0, receipt_date="2026-05-02")

        with self.SessionLocal() as db:
            with patch(
                "app.services.trip_grouper.call_claude_for_trip",
                return_value=None,
            ):
                trips = persist_suggestions(
                    db, suggest_trips(db, lookback_days=90),
                )
            trip_id = trips[0].id
        from app.models import TripMessage
        with self.SessionLocal() as db:
            ids = {
                tm.message_id for tm in
                db.query(TripMessage).filter_by(trip_id=trip_id).all()
            }
        self.assertNotIn("clas", ids)
        self.assertIn("hotel", ids)

    def test_window_uses_outbound_to_inbound(self):
        """Datum-fönstret bygger på flygets outbound→inbound (+1d
        marginal). Kvitton 12 dagar efter sista flyg ska INTE inkluderas."""
        from app.services.trip_grouper import (
            persist_suggestions, suggest_trips,
        )
        self._seed(message_id="outbound", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
        self._seed(message_id="inbound", vendor="Finnair", category="Flyg",
                   amount=420.0, receipt_date="2026-05-03")
        # Kvitto 10 dagar efter hemresan — utanför fönstret nu.
        self._seed(message_id="late_taxi", vendor="Uber", category="Taxi",
                   amount=30.0, receipt_date="2026-05-13")
        # Kvitto inom fönstret
        self._seed(message_id="hotel", vendor="Scandic", category="Hotell",
                   amount=200.0, receipt_date="2026-05-02")

        with self.SessionLocal() as db:
            with patch(
                "app.services.trip_grouper.call_claude_for_trip",
                return_value=None,
            ):
                trips = persist_suggestions(
                    db, suggest_trips(db, lookback_days=90),
                )
            trip_id = trips[0].id
        from app.models import TripMessage
        with self.SessionLocal() as db:
            ids = {
                tm.message_id for tm in
                db.query(TripMessage).filter_by(trip_id=trip_id).all()
            }
        self.assertIn("hotel", ids)
        self.assertNotIn("late_taxi", ids)

    def test_recurring_travel_vendor_still_included(self):
        """Återkommande resekvitton (Skånetrafiken, Arlanda Express)
        ska INKLUDERAS — de räknas inte som SaaS, bara fasta vendors
        för kollektivtrafik."""
        from app.services.trip_grouper import (
            persist_suggestions, suggest_trips,
        )
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
        self._seed(message_id="skanetraf",
                   vendor="Skånetrafiken AB",
                   category="Kollektivtrafik",
                   amount=45.0,
                   receipt_date="2026-05-02")
        with self.SessionLocal() as db:
            with patch(
                "app.services.trip_grouper.call_claude_for_trip",
                return_value=None,
            ):
                trips = persist_suggestions(
                    db, suggest_trips(db, lookback_days=90),
                )
            trip_id = trips[0].id
        from app.models import TripMessage
        with self.SessionLocal() as db:
            ids = {
                tm.message_id for tm in
                db.query(TripMessage).filter_by(trip_id=trip_id).all()
            }
        self.assertIn("skanetraf", ids)


# ---------- Endpoint-tester ----------


class TaggingEndpointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
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

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        from app.models import (
            ExcludedVendor, MaintenanceTask, ProcessedMessage,
            Trip, TripFeedback, TripMessage,
        )
        with self.SessionLocal() as db:
            db.query(TripFeedback).delete()
            db.query(TripMessage).delete()
            db.query(Trip).delete()
            db.query(ProcessedMessage).delete()
            db.query(ExcludedVendor).delete()
            db.query(MaintenanceTask).delete()
            db.commit()

    def _seed_msg(self, message_id="msg-1", **over):
        from app.models import ProcessedMessage
        defaults = dict(
            message_id=message_id,
            sender="x@example.com",
            subject="Subject",
            received_at=datetime(2026, 5, 1, 12, 0, 0),
            processed_at=datetime(2026, 5, 1, 12, 0, 0),
            status="saved",
            vendor="Restaurant ABC",
            amount=85.0,
            currency="EUR",
            receipt_date="2026-05-01",
            category="Mat",
            ai_confidence=92,
        )
        defaults.update(over)
        with self.SessionLocal() as db:
            row = ProcessedMessage(**defaults)
            db.add(row)
            db.commit()

    def _seed_trip(self, status="active", start="2026-04-30", end="2026-05-02"):
        from datetime import date as _date
        from app.models import Trip
        with self.SessionLocal() as db:
            trip = Trip(
                title="Stockholm",
                destination="Stockholm",
                start_date=_date.fromisoformat(start),
                end_date=_date.fromisoformat(end),
                status=status,
                base_currency="EUR",
            )
            db.add(trip)
            db.commit()
            return trip.id

    def test_link_message_to_trip(self):
        self._seed_msg("msg-1")
        trip_id = self._seed_trip()
        resp = self.client.post(
            "/api/messages/msg-1/link-to-trip",
            json={"trip_id": trip_id},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["already_linked"])

        from app.models import TripMessage
        with self.SessionLocal() as db:
            tm = (
                db.query(TripMessage)
                .filter_by(trip_id=trip_id, message_id="msg-1")
                .first()
            )
        self.assertIsNotNone(tm)
        self.assertEqual(tm.added_by, "manual")

    def test_link_idempotent(self):
        self._seed_msg("msg-1")
        trip_id = self._seed_trip()
        self.client.post("/api/messages/msg-1/link-to-trip",
                         json={"trip_id": trip_id})
        resp = self.client.post(
            "/api/messages/msg-1/link-to-trip",
            json={"trip_id": trip_id},
        )
        self.assertTrue(resp.json()["already_linked"])

    def test_relink_after_unlink_marks_manual(self):
        from app.models import TripMessage
        self._seed_msg("msg-1")
        trip_id = self._seed_trip()

        # Skapa AI-suggestion-koppling
        with self.SessionLocal() as db:
            db.add(TripMessage(
                trip_id=trip_id, message_id="msg-1",
                added_by="ai_suggestion",
            ))
            db.commit()

        # Unlink
        resp = self.client.delete(
            f"/api/messages/msg-1/unlink-from-trip/{trip_id}",
        )
        self.assertEqual(resp.status_code, 200)

        # Re-link → måste bli manual
        resp = self.client.post(
            "/api/messages/msg-1/link-to-trip",
            json={"trip_id": trip_id},
        )
        self.assertEqual(resp.status_code, 200)
        with self.SessionLocal() as db:
            tm = (
                db.query(TripMessage)
                .filter_by(trip_id=trip_id, message_id="msg-1")
                .first()
            )
        self.assertEqual(tm.added_by, "manual")
        self.assertIsNone(tm.removed_at)

    def test_unlink_404_when_not_linked(self):
        self._seed_msg("msg-1")
        trip_id = self._seed_trip()
        resp = self.client.delete(
            f"/api/messages/msg-1/unlink-from-trip/{trip_id}",
        )
        self.assertEqual(resp.status_code, 404)

    def test_available_trips_filters_by_date_window(self):
        self._seed_msg("msg-1", receipt_date="2026-05-01")
        near_id = self._seed_trip(
            start="2026-04-30", end="2026-05-02",
        )
        # Resa långt borta — utanför fönstret
        far_id = self._seed_trip(
            start="2026-08-01", end="2026-08-05",
        )

        resp = self.client.get("/api/messages/msg-1/available-trips")
        self.assertEqual(resp.status_code, 200)
        ids = [t["id"] for t in resp.json()["trips"]]
        self.assertIn(near_id, ids)
        self.assertNotIn(far_id, ids)

    def test_available_trips_marks_is_linked(self):
        self._seed_msg("msg-1")
        trip_id = self._seed_trip()
        self.client.post("/api/messages/msg-1/link-to-trip",
                         json={"trip_id": trip_id})
        resp = self.client.get("/api/messages/msg-1/available-trips")
        trip = next(t for t in resp.json()["trips"] if t["id"] == trip_id)
        self.assertTrue(trip["is_linked"])
        self.assertEqual(trip["added_by"], "manual")

    def test_excluded_vendors_crud(self):
        # Tom från start
        resp = self.client.get("/api/excluded-vendors")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["vendors"], [])

        # Lägg till
        resp = self.client.post(
            "/api/excluded-vendors",
            json={"pattern": "MyCorp", "description": "Min egen"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        vid = body["id"]
        self.assertEqual(body["pattern"], "mycorp")
        self.assertEqual(body["added_by"], "user")

        # Idempotent — andra POST returnerar already_exists
        resp2 = self.client.post(
            "/api/excluded-vendors",
            json={"pattern": "mycorp"},
        )
        self.assertTrue(resp2.json()["already_exists"])

        # Lista visar vendor
        resp = self.client.get("/api/excluded-vendors")
        patterns = [v["pattern"] for v in resp.json()["vendors"]]
        self.assertIn("mycorp", patterns)

        # Ta bort
        resp = self.client.delete(f"/api/excluded-vendors/{vid}")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.delete(f"/api/excluded-vendors/{vid}")
        self.assertEqual(resp.status_code, 404)

    def test_excluded_vendors_post_400_on_empty(self):
        resp = self.client.post(
            "/api/excluded-vendors", json={"pattern": "   "},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
