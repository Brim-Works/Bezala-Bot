"""FAS 11.1 — tester för trip_grouper service + /api/trips endpoints.

Testklasser:
  - FlightAnchorsTest:       _is_flight + find_flight_anchors
  - RelatedReceiptsTest:     find_related_receipts (datumfönster, stora belopp)
  - SuggestTripsTest:        suggest_trips (no anchors, dedupe, lookback)
  - PersistAndCRUDTest:      persist_suggestions, accept, edit, recalc
  - TripsEndpointTest:       /api/trips/* via FastAPI TestClient
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
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


def _msg(message_id, **over):
    """Bygg en ProcessedMessage utan att skriva till DB."""
    from app.models import ProcessedMessage
    defaults = dict(
        message_id=message_id,
        sender="invoice@example.com",
        subject="Kvitto",
        received_at=datetime(2026, 5, 1, 12, 0, 0),
        processed_at=datetime(2026, 5, 1, 12, 0, 0),
        status="saved",
        vendor="Example",
        amount=100.0,
        currency="EUR",
        receipt_date="2026-05-01",
        category="Annat",
        ai_confidence=90,
    )
    defaults.update(over)
    return ProcessedMessage(**defaults)


# ---------- Pure-funktion tester ----------


class FlightAnchorsTest(unittest.TestCase):
    def test_finnair_vendor_is_flight(self):
        from app.services.trip_grouper import find_flight_anchors
        m = _msg("m1", vendor="Finnair", category="Annat")
        self.assertEqual(find_flight_anchors([m]), [m])

    def test_sas_lufthansa_klm_recognized(self):
        from app.services.trip_grouper import find_flight_anchors
        msgs = [
            _msg("a", vendor="SAS"),
            _msg("b", vendor="Lufthansa"),
            _msg("c", vendor="KLM Royal Dutch"),
        ]
        self.assertEqual(len(find_flight_anchors(msgs)), 3)

    def test_category_flyg_is_flight_even_if_unknown_vendor(self):
        from app.services.trip_grouper import find_flight_anchors
        m = _msg("x", vendor="Småflyget", category="Flyg")
        self.assertEqual(find_flight_anchors([m]), [m])

    def test_non_flight_excluded(self):
        from app.services.trip_grouper import find_flight_anchors
        msgs = [
            _msg("h", vendor="Scandic", category="Hotell"),
            _msg("t", vendor="Uber", category="Taxi"),
        ]
        self.assertEqual(find_flight_anchors(msgs), [])


class RelatedReceiptsTest(unittest.TestCase):
    def _make_anchor(self, dt="2026-05-01"):
        return _msg(
            "anchor",
            vendor="Finnair",
            category="Flyg",
            receipt_date=dt,
            amount=400.0,
        )

    def test_within_window_included(self):
        from app.services.trip_grouper import find_related_receipts
        anchor = self._make_anchor()
        hotel = _msg("h", vendor="Scandic", category="Hotell",
                     receipt_date="2026-05-02", amount=900.0)
        related = find_related_receipts(anchor, [anchor, hotel])
        self.assertIn(hotel, related)
        self.assertIn(anchor, related)

    def test_outside_window_excluded(self):
        from app.services.trip_grouper import find_related_receipts
        anchor = self._make_anchor("2026-05-01")
        far = _msg("far", receipt_date="2026-06-15", amount=50.0)
        self.assertNotIn(far, find_related_receipts(anchor, [anchor, far]))

    def test_two_days_before_included(self):
        """Taxi till flygplats kan dyka upp 2 dagar före flyget."""
        from app.services.trip_grouper import find_related_receipts
        anchor = self._make_anchor("2026-05-01")
        taxi = _msg("taxi", vendor="Uber", category="Taxi",
                    receipt_date="2026-04-29", amount=30.0)
        self.assertIn(taxi, find_related_receipts(anchor, [anchor, taxi]))

    def test_three_days_before_excluded(self):
        from app.services.trip_grouper import find_related_receipts
        anchor = self._make_anchor("2026-05-01")
        too_early = _msg(
            "early", receipt_date="2026-04-28", amount=30.0,
        )
        self.assertNotIn(
            too_early, find_related_receipts(anchor, [anchor, too_early]),
        )

    def test_large_receipt_excluded(self):
        from app.services.trip_grouper import find_related_receipts
        anchor = self._make_anchor()
        big = _msg("big", receipt_date="2026-05-03", amount=8000.0)
        self.assertNotIn(big, find_related_receipts(anchor, [anchor, big]))

    def test_anchor_always_included(self):
        from app.services.trip_grouper import find_related_receipts
        anchor = self._make_anchor()
        self.assertIn(anchor, find_related_receipts(anchor, [anchor]))


class MergeProposalsTest(unittest.TestCase):
    def test_overlapping_proposals_merged(self):
        from datetime import date
        from app.services.trip_grouper import (
            TripProposal, _merge_overlapping_proposals,
        )
        a = TripProposal("a", ["m1", "m2"], date(2026, 5, 1), date(2026, 5, 3))
        b = TripProposal("b", ["m3"], date(2026, 5, 3), date(2026, 5, 4))
        merged = _merge_overlapping_proposals([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0].message_ids), {"m1", "m2", "m3"})
        self.assertEqual(merged[0].end_date, date(2026, 5, 4))

    def test_disjoint_proposals_kept(self):
        from datetime import date
        from app.services.trip_grouper import (
            TripProposal, _merge_overlapping_proposals,
        )
        a = TripProposal("a", ["m1"], date(2026, 4, 1), date(2026, 4, 3))
        b = TripProposal("b", ["m2"], date(2026, 5, 1), date(2026, 5, 3))
        self.assertEqual(len(_merge_overlapping_proposals([a, b])), 2)


# ---------- Service-tester med DB ----------


class SuggestAndPersistTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=db_module.engine)
        cls.SessionLocal = db_module.SessionLocal

    def setUp(self):
        from app.models import (
            ProcessedMessage, Trip, TripFeedback, TripMessage,
        )
        with self.SessionLocal() as db:
            db.query(TripFeedback).delete()
            db.query(TripMessage).delete()
            db.query(Trip).delete()
            db.query(ProcessedMessage).delete()
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

    def test_no_flights_returns_empty(self):
        from app.services.trip_grouper import suggest_trips
        self._seed(message_id="h", vendor="Scandic", category="Hotell",
                   amount=900.0, receipt_date="2026-05-01")
        with self.SessionLocal() as db:
            self.assertEqual(suggest_trips(db, lookback_days=90), [])

    def test_single_flight_only_excluded(self):
        """Bara flygbiljett utan andra kvitton → < 2 → inget förslag."""
        from app.services.trip_grouper import suggest_trips
        self._seed(message_id="f", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
        with self.SessionLocal() as db:
            self.assertEqual(suggest_trips(db, lookback_days=90), [])

    def test_flight_plus_hotel_creates_proposal(self):
        from app.services.trip_grouper import suggest_trips
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
        self._seed(message_id="hotel", vendor="Scandic", category="Hotell",
                   amount=1500.0, receipt_date="2026-05-02")
        with self.SessionLocal() as db:
            with patch(
                "app.services.trip_grouper.call_claude_for_trip",
                return_value=None,
            ):
                suggestions = suggest_trips(db, lookback_days=90)
        self.assertEqual(len(suggestions), 1)
        s = suggestions[0]
        self.assertIn("flight", s["message_ids"])
        self.assertIn("hotel", s["message_ids"])
        self.assertEqual(s["confidence"], 40)  # fallback-confidence

    def test_persist_skips_overlapping(self):
        from app.services.trip_grouper import (
            persist_suggestions, suggest_trips,
        )
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
        self._seed(message_id="hotel", vendor="Scandic", category="Hotell",
                   amount=1500.0, receipt_date="2026-05-02")
        with self.SessionLocal() as db:
            with patch(
                "app.services.trip_grouper.call_claude_for_trip",
                return_value=None,
            ):
                first = persist_suggestions(
                    db, suggest_trips(db, lookback_days=90),
                )
                second = persist_suggestions(
                    db, suggest_trips(db, lookback_days=90),
                )
        self.assertEqual(len(first), 1)
        # Andra körningen ska inte skapa duplicate (kvittona är redan
        # kopplade till första resan)
        self.assertEqual(len(second), 0)

    def test_recalc_total_with_same_currency(self):
        from app.services.trip_grouper import (
            persist_suggestions, recalculate_trip_total, suggest_trips,
        )
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, currency="EUR", receipt_date="2026-05-01")
        self._seed(message_id="hotel", vendor="Scandic", category="Hotell",
                   amount=200.0, currency="EUR", receipt_date="2026-05-02")
        with self.SessionLocal() as db:
            with patch(
                "app.services.trip_grouper.call_claude_for_trip",
                return_value=None,
            ):
                trips = persist_suggestions(
                    db, suggest_trips(db, lookback_days=90),
                )
            self.assertEqual(len(trips), 1)
            trip = trips[0]
            recalculate_trip_total(db, trip)
            self.assertEqual(float(trip.total_amount), 600.0)

    def test_edit_logs_feedback_and_recalcs(self):
        from app.models import Trip, TripFeedback
        from app.services.trip_grouper import (
            edit_trip, persist_suggestions, suggest_trips,
        )
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, currency="EUR", receipt_date="2026-05-01")
        self._seed(message_id="hotel", vendor="Scandic", category="Hotell",
                   amount=200.0, currency="EUR", receipt_date="2026-05-02")
        self._seed(message_id="extra", vendor="Restaurang Kvarnen",
                   category="Mat", amount=80.0, currency="EUR",
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
            edit_trip(
                db, trips[0],
                title="Stockholm-resa",
                add_message_ids=["extra"],
                remove_message_ids=["hotel"],
            )

            refreshed = db.query(Trip).filter(Trip.id == trip_id).first()
            self.assertEqual(refreshed.title, "Stockholm-resa")
            self.assertTrue(refreshed.user_edited)
            # Total: 400 (flight) + 80 (extra) = 480 (hotel borttaget)
            self.assertEqual(float(refreshed.total_amount), 480.0)

            feedback_rows = db.query(TripFeedback).filter(
                TripFeedback.trip_id == trip_id,
            ).all()
            edited = [fb for fb in feedback_rows if fb.feedback_type == "edited"]
            self.assertEqual(len(edited), 1)
            details = edited[0].details
            self.assertIn("title", details)
            self.assertIn("messages", details)

    def test_accept_changes_status(self):
        from app.models import Trip, TripFeedback
        from app.services.trip_grouper import (
            accept_trip, persist_suggestions, suggest_trips,
        )
        self._seed(message_id="flight", vendor="Finnair", category="Flyg",
                   amount=400.0, receipt_date="2026-05-01")
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
            accept_trip(db, trips[0])
            refreshed = db.query(Trip).filter(Trip.id == trips[0].id).first()
            self.assertEqual(refreshed.status, "active")
            self.assertIsNotNone(refreshed.user_decision_at)
            self.assertEqual(
                db.query(TripFeedback).filter(
                    TripFeedback.feedback_type == "accepted",
                    TripFeedback.trip_id == trips[0].id,
                ).count(), 1,
            )


# ---------- Endpoint-tester ----------


class TripsEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import (
            ProcessedMessage, Trip, TripFeedback, TripMessage,
        )
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

    def _seed_flight_and_hotel(self):
        with self.SessionLocal() as db:
            db.add(self.ProcessedMessage(
                message_id="flight",
                sender="finnair@finnair.com",
                subject="Boarding pass",
                received_at=datetime.utcnow(),
                processed_at=datetime.utcnow(),
                status="saved",
                vendor="Finnair", category="Flyg",
                amount=400.0, currency="EUR", receipt_date="2026-05-01",
                ai_confidence=95,
            ))
            db.add(self.ProcessedMessage(
                message_id="hotel",
                sender="reservations@scandic.com",
                subject="Bokningsbekräftelse",
                received_at=datetime.utcnow(),
                processed_at=datetime.utcnow(),
                status="saved",
                vendor="Scandic", category="Hotell",
                amount=1500.0, currency="EUR", receipt_date="2026-05-02",
                ai_confidence=92,
            ))
            db.commit()

    def test_refresh_creates_suggestion(self):
        self._seed_flight_and_hotel()
        with patch(
            "app.services.trip_grouper.call_claude_for_trip",
            return_value=None,
        ):
            resp = self.client.post("/api/trips/refresh-suggestions")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(resp.json()["generated"], 1)

        listing = self.client.get("/api/trips/suggestions").json()
        self.assertEqual(len(listing["trips"]), 1)
        trip = listing["trips"][0]
        self.assertEqual(trip["status"], "suggested")
        self.assertEqual(len(trip["messages"]), 2)

    def test_accept_flow(self):
        self._seed_flight_and_hotel()
        with patch(
            "app.services.trip_grouper.call_claude_for_trip",
            return_value=None,
        ):
            self.client.post("/api/trips/refresh-suggestions")
        trip_id = self.client.get("/api/trips/suggestions").json()["trips"][0]["id"]

        resp = self.client.post(f"/api/trips/{trip_id}/accept")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "active")

        active = self.client.get("/api/trips/active").json()["trips"]
        self.assertEqual(len(active), 1)

    def test_patch_edit(self):
        self._seed_flight_and_hotel()
        with patch(
            "app.services.trip_grouper.call_claude_for_trip",
            return_value=None,
        ):
            self.client.post("/api/trips/refresh-suggestions")
        trip_id = self.client.get("/api/trips/suggestions").json()["trips"][0]["id"]

        resp = self.client.patch(
            f"/api/trips/{trip_id}",
            json={"title": "Min Stockholm-resa"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["title"], "Min Stockholm-resa")
        self.assertTrue(resp.json()["user_edited"])

    def test_feedback_endpoint(self):
        self._seed_flight_and_hotel()
        with patch(
            "app.services.trip_grouper.call_claude_for_trip",
            return_value=None,
        ):
            self.client.post("/api/trips/refresh-suggestions")
        trip_id = self.client.get("/api/trips/suggestions").json()["trips"][0]["id"]

        resp = self.client.post(
            f"/api/trips/{trip_id}/feedback",
            json={"feedback_type": "wrong_grouping",
                  "details": {"comment": "Hotellet hörde inte hit"}},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["saved"])

        with self.SessionLocal() as db:
            rows = db.query(self.TripFeedback).filter(
                self.TripFeedback.trip_id == trip_id,
                self.TripFeedback.feedback_type == "wrong_grouping",
            ).all()
            self.assertEqual(len(rows), 1)

    def test_404_for_missing_trip(self):
        resp = self.client.get("/api/trips/9999")
        self.assertEqual(resp.status_code, 404)

    def test_stats_endpoint(self):
        self._seed_flight_and_hotel()
        with patch(
            "app.services.trip_grouper.call_claude_for_trip",
            return_value=None,
        ):
            self.client.post("/api/trips/refresh-suggestions")
        stats = self.client.get("/api/trips/stats").json()
        self.assertEqual(stats["suggested"], 1)
        self.assertEqual(stats["active"], 0)


if __name__ == "__main__":
    unittest.main()
