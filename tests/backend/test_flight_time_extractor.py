"""FAS 11.5.1 — tester för flight_time_extractor.

Använder en injicerad mock-extractor istället för riktig Claude-API.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta

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
    from app.db import Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=db_module.engine)
    return db_module


def _seed_trip_with_flights(db_module, n_flights=2):
    """Skapa en Trip + flygkvitton för testet."""
    from datetime import date
    from app.models import ProcessedMessage, Trip, TripMessage

    with db_module.SessionLocal() as db:
        trip = Trip(
            title="Stockholm",
            start_date=date(2026, 4, 30),
            end_date=date(2026, 5, 2),
            base_currency="EUR",
            status="active",
        )
        db.add(trip)
        db.flush()

        msg_ids = []
        for i in range(n_flights):
            mid = f"flight-{i}"
            db.add(ProcessedMessage(
                message_id=mid,
                sender="finnair@finnair.com",
                subject=f"Finnair-bokning {i}",
                vendor="Finnair",
                category="Flyg",
                summary="Flygkvitto",
                receipt_date="2026-04-30",
                status="saved",
                amount=200.0,
                currency="EUR",
            ))
            db.add(TripMessage(
                trip_id=trip.id, message_id=mid,
                added_by="manual",
            ))
            msg_ids.append(mid)
        db.commit()
        return trip.id, msg_ids


class FlightExtractorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_module = _setup_db()

    def setUp(self):
        from app.models import (
            ProcessedMessage, Trip, TripMessage, TripFeedback,
        )
        with self.db_module.SessionLocal() as db:
            db.query(TripFeedback).delete()
            db.query(TripMessage).delete()
            db.query(Trip).delete()
            db.query(ProcessedMessage).delete()
            db.commit()

    def test_no_flights_returns_warning(self):
        from app.services.flight_time_extractor import (
            extract_flight_times_from_trip,
        )
        from app.models import Trip
        from datetime import date
        with self.db_module.SessionLocal() as db:
            trip = Trip(
                title="X", start_date=date(2026, 5, 1),
                end_date=date(2026, 5, 1), base_currency="EUR",
                status="active",
            )
            db.add(trip)
            db.commit()
            db.refresh(trip)
            result = extract_flight_times_from_trip(trip, db)
        self.assertIn("warnings", result)
        self.assertEqual(result["flights_extracted"], [])

    def test_finnair_helsinki_stockholm_round_trip(self):
        from app.services.flight_time_extractor import (
            extract_flight_times_from_trip,
        )
        from app.models import Trip
        trip_id, msg_ids = _seed_trip_with_flights(self.db_module, n_flights=2)

        # Mock-extractor returnerar olika svar per message
        def mock_extract(msg):
            if msg.message_id == "flight-0":
                return {
                    "departure_airport": "HEL",
                    "departure_city": "Helsinki",
                    "departure_country": "FI",
                    "departure_time": "2026-04-30T07:15:00+03:00",
                    "arrival_airport": "ARN",
                    "arrival_city": "Stockholm",
                    "arrival_country": "SE",
                    "arrival_time": "2026-04-30T07:55:00+02:00",
                    "is_outbound": True,
                    "booking_reference": "AAA111",
                }
            return {
                "departure_airport": "ARN",
                "departure_city": "Stockholm",
                "departure_country": "SE",
                "departure_time": "2026-05-02T16:40:00+02:00",
                "arrival_airport": "HEL",
                "arrival_city": "Helsinki",
                "arrival_country": "FI",
                "arrival_time": "2026-05-02T19:20:00+03:00",
                "is_outbound": False,
                "booking_reference": "AAA111",
            }

        with self.db_module.SessionLocal() as db:
            trip = db.query(Trip).filter(Trip.id == trip_id).first()
            result = extract_flight_times_from_trip(
                trip, db, extractor=mock_extract,
            )

        self.assertEqual(result["destination_country_suggestion"], "SE")
        self.assertEqual(
            result["trip_route"], "Helsinki - Stockholm - Helsinki",
        )
        self.assertEqual(len(result["flights_extracted"]), 2)
        # departure_home_at = outbound -1h = 06:15 +03:00
        self.assertIn("2026-04-30T06:15", result["departure_home_at"])
        # return_home_at = inbound +1h = 20:20 +03:00
        self.assertIn("2026-05-02T20:20", result["return_home_at"])

    def test_country_fallback_via_iata(self):
        """Om Claude inte returnerar land-koder fyller IATA-fallback i."""
        from app.services.flight_time_extractor import (
            extract_flight_times_from_trip,
        )
        from app.models import Trip
        trip_id, _ = _seed_trip_with_flights(self.db_module, n_flights=1)

        def mock_extract(msg):
            return {
                "departure_airport": "HEL",
                "departure_city": "Helsinki",
                "departure_time": "2026-04-30T07:00:00+03:00",
                "arrival_airport": "ARN",
                "arrival_city": "Stockholm",
                "arrival_time": "2026-04-30T08:00:00+02:00",
                # Ingen explicit country
                "is_outbound": True,
            }

        with self.db_module.SessionLocal() as db:
            trip = db.query(Trip).filter(Trip.id == trip_id).first()
            result = extract_flight_times_from_trip(
                trip, db, extractor=mock_extract,
            )
        self.assertEqual(result["destination_country_suggestion"], "SE")
        self.assertEqual(
            result["flights_extracted"][0]["departure_country"], "FI",
        )

    def test_unparseable_flight_skipped_with_warning(self):
        from app.services.flight_time_extractor import (
            extract_flight_times_from_trip,
        )
        from app.models import Trip
        trip_id, _ = _seed_trip_with_flights(self.db_module, n_flights=2)

        # Första returnerar ogiltig (tider saknas), andra är OK
        def mock_extract(msg):
            if msg.message_id == "flight-0":
                return {
                    "departure_airport": "HEL",
                    "is_outbound": True,
                }  # saknar tider
            return {
                "departure_airport": "HEL",
                "departure_city": "Helsinki",
                "departure_country": "FI",
                "departure_time": "2026-04-30T07:00:00+03:00",
                "arrival_airport": "ARN",
                "arrival_city": "Stockholm",
                "arrival_country": "SE",
                "arrival_time": "2026-04-30T08:00:00+02:00",
                "is_outbound": True,
            }

        with self.db_module.SessionLocal() as db:
            trip = db.query(Trip).filter(Trip.id == trip_id).first()
            result = extract_flight_times_from_trip(
                trip, db, extractor=mock_extract,
            )
        self.assertEqual(len(result["flights_extracted"]), 1)
        self.assertTrue(any("Kunde inte tolka" in w for w in result["warnings"]))

    def test_extractor_returns_null_for_all_flights(self):
        from app.services.flight_time_extractor import (
            extract_flight_times_from_trip,
        )
        from app.models import Trip
        trip_id, _ = _seed_trip_with_flights(self.db_module, n_flights=1)

        with self.db_module.SessionLocal() as db:
            trip = db.query(Trip).filter(Trip.id == trip_id).first()
            result = extract_flight_times_from_trip(
                trip, db, extractor=lambda msg: None,
            )
        self.assertEqual(result["flights_extracted"], [])
        self.assertTrue(any("manuellt" in w for w in result["warnings"]))

    def test_extractor_exception_is_caught(self):
        """En extractor som kastar exception bryter inte hela flödet."""
        from app.services.flight_time_extractor import (
            extract_flight_times_from_trip,
        )
        from app.models import Trip
        trip_id, _ = _seed_trip_with_flights(self.db_module, n_flights=1)

        def boom(msg):
            raise RuntimeError("simulerad krasch")

        with self.db_module.SessionLocal() as db:
            trip = db.query(Trip).filter(Trip.id == trip_id).first()
            result = extract_flight_times_from_trip(
                trip, db, extractor=boom,
            )
        self.assertEqual(result["flights_extracted"], [])


if __name__ == "__main__":
    unittest.main()
