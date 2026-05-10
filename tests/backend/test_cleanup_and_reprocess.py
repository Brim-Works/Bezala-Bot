"""Tester för Cleanup-PR (FAS Cleanup): excluded_vendors retroaktivt
+ reprocess-full-endpointen."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "")
os.environ.setdefault("DRIVE_REFRESH_TOKEN", "")
os.environ.setdefault("BEZALA_USERNAME", "x")
os.environ.setdefault("BEZALA_PASSWORD", "x")
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
        bind=db_module.engine, autoflush=False, autocommit=False,
    )
    return db_module


class _BaseEndpointFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import (
            AppSettings, ProcessedMessage, Trip, TripMessage,
        )
        from fastapi.testclient import TestClient
        from contextlib import contextmanager

        Base.metadata.create_all(bind=db_module.engine)
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
        cls.AppSettings = AppSettings
        cls.ProcessedMessage = ProcessedMessage
        cls.Trip = Trip
        cls.TripMessage = TripMessage

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.TripMessage).delete()
            db.query(self.Trip).delete()
            db.query(self.ProcessedMessage).delete()
            db.query(self.AppSettings).delete()
            db.commit()

    def _set_excluded(self, vendors):
        with self.SessionLocal() as db:
            row = self.AppSettings(
                id=1,
                excluded_vendors=list(vendors),
                include_senders=[],
                exclude_senders=[],
                exclude_subjects=[],
                link_fetch_senders=[],
            )
            db.add(row)
            db.commit()

    def _seed_msg(self, message_id, vendor, **over):
        defaults = dict(
            message_id=message_id,
            sender="x@x",
            subject="s",
            status="saved",
            vendor=vendor,
            file_name=f"{message_id}.pdf",
            drive_file_id=f"drv-{message_id}",
            received_at=datetime(2026, 4, 1),
        )
        defaults.update(over)
        with self.SessionLocal() as db:
            row = self.ProcessedMessage(**defaults)
            db.add(row)
            db.flush()
            mid = row.id
            db.commit()
        return mid

    def _seed_trip_with_messages(self, title, message_ids):
        with self.SessionLocal() as db:
            t = self.Trip(
                title=title,
                start_date=date(2026, 4, 1),
                end_date=date(2026, 4, 5),
                status="active",
                base_currency="EUR",
            )
            db.add(t)
            db.flush()
            for mid in message_ids:
                db.add(self.TripMessage(
                    trip_id=t.id, message_id=mid, added_by="manual",
                ))
            db.commit()
            return t.id


class FilterTripsTest(_BaseEndpointFixture):
    """A: Reaktivt UI-filter."""

    def test_returns_only_unfiltered_messages(self):
        # 5 messages, 2 av "Anthropic" (excluded)
        for i in range(3):
            self._seed_msg(f"m-good-{i}", "Finnair")
        for i in range(2):
            self._seed_msg(f"m-bad-{i}", "Anthropic")
        self._set_excluded(["Anthropic"])
        trip_id = self._seed_trip_with_messages(
            "T", [f"m-good-{i}" for i in range(3)] + [f"m-bad-{i}" for i in range(2)],
        )
        resp = self.client.get(f"/api/trips/{trip_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body["messages"]), 3)
        self.assertEqual(body["message_count"], 3)
        for m in body["messages"]:
            self.assertEqual(m["vendor"], "Finnair")

    def test_message_count_reflects_filtered(self):
        self._seed_msg("a", "Anthropic")
        self._seed_msg("b", "Finnair")
        self._set_excluded(["anthropic"])
        trip_id = self._seed_trip_with_messages("T", ["a", "b"])
        resp = self.client.get(f"/api/trips/{trip_id}")
        self.assertEqual(resp.json()["message_count"], 1)

    def test_empty_trip_dropped_from_list(self):
        self._seed_msg("a", "Anthropic")
        self._seed_trip_with_messages("Empty", ["a"])
        # Andra resa med kvarvarande mail
        self._seed_msg("b", "Finnair")
        self._seed_trip_with_messages("Keep", ["b"])
        self._set_excluded(["Anthropic"])
        resp = self.client.get("/api/trips/active")
        body = resp.json()
        titles = [t["title"] for t in body["trips"]]
        self.assertEqual(titles, ["Keep"])


class CleanupExcludedVendorsTest(_BaseEndpointFixture):
    """B: Cleanup-endpoint."""

    def test_removes_matching_trip_messages(self):
        self._seed_msg("a", "Anthropic")
        self._seed_msg("b", "Finnair")
        trip_id = self._seed_trip_with_messages("T", ["a", "b"])
        self._set_excluded(["Anthropic"])
        resp = self.client.post("/api/trips/cleanup-excluded-vendors")
        body = resp.json()
        self.assertEqual(body["removed_messages"], 1)
        self.assertEqual(body["affected_trips"], 1)
        self.assertEqual(body["deleted_empty_trips"], 0)
        with self.SessionLocal() as db:
            tms = db.query(self.TripMessage).filter_by(trip_id=trip_id).all()
            self.assertEqual([tm.message_id for tm in tms], ["b"])

    def test_deletes_empty_trips(self):
        self._seed_msg("a", "Anthropic")
        self._seed_trip_with_messages("Empty", ["a"])
        self._set_excluded(["Anthropic"])
        resp = self.client.post("/api/trips/cleanup-excluded-vendors")
        body = resp.json()
        self.assertEqual(body["removed_messages"], 1)
        self.assertEqual(body["deleted_empty_trips"], 1)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(self.Trip).count(), 0)

    def test_keeps_trips_with_remaining_messages(self):
        self._seed_msg("a", "Anthropic")
        self._seed_msg("b", "Finnair")
        self._seed_msg("c", "Scandic")
        self._seed_trip_with_messages("T", ["a", "b", "c"])
        self._set_excluded(["Anthropic"])
        self.client.post("/api/trips/cleanup-excluded-vendors")
        with self.SessionLocal() as db:
            self.assertEqual(db.query(self.Trip).count(), 1)
            self.assertEqual(db.query(self.TripMessage).count(), 2)

    def test_idempotent_second_run(self):
        self._seed_msg("a", "Anthropic")
        self._seed_msg("b", "Finnair")
        self._seed_trip_with_messages("T", ["a", "b"])
        self._set_excluded(["Anthropic"])
        first = self.client.post("/api/trips/cleanup-excluded-vendors").json()
        second = self.client.post("/api/trips/cleanup-excluded-vendors").json()
        self.assertEqual(first["removed_messages"], 1)
        self.assertEqual(second["removed_messages"], 0)
        self.assertEqual(second["deleted_empty_trips"], 0)

    def test_case_insensitive_matching(self):
        # Excluded är versalt, vendor är gement → ska matcha
        self._seed_msg("a", "anthropic")
        self._seed_msg("b", "ANTHROPIC")
        self._seed_msg("c", "Anthropic")
        self._seed_trip_with_messages("T", ["a", "b", "c"])
        self._set_excluded(["AnThRoPic"])
        resp = self.client.post("/api/trips/cleanup-excluded-vendors").json()
        self.assertEqual(resp["removed_messages"], 3)

    def test_empty_excluded_returns_zero(self):
        self._seed_msg("a", "Anthropic")
        self._seed_trip_with_messages("T", ["a"])
        self._set_excluded([])
        resp = self.client.post("/api/trips/cleanup-excluded-vendors").json()
        self.assertEqual(resp["removed_messages"], 0)


class ReprocessFullTest(_BaseEndpointFixture):
    """C: Reprocess-full endpoint."""

    def setUp(self):
        super().setUp()
        # Default: råsig drive_client + run_scan så endpoint inte triggar
        # riktiga sidoeffekter under tester.
        self._patcher_drive = patch.object(
            self.app_module, "_get_drive_client_safe",
            return_value=MagicMock(delete_file=MagicMock()),
        )
        self._mock_drive_getter = self._patcher_drive.start()
        self.mock_drive = self._mock_drive_getter.return_value

        self._patcher_gmail = patch.object(
            self.app_module, "_get_gmail_client_safe",
            return_value=MagicMock(remove_done=MagicMock()),
        )
        self._mock_gmail_getter = self._patcher_gmail.start()

        self._patcher_scan = patch.object(self.app_module, "run_scan")
        self.mock_run_scan = self._patcher_scan.start()

    def tearDown(self):
        self._patcher_drive.stop()
        self._patcher_gmail.stop()
        self._patcher_scan.stop()

    def test_uncoupled_deletes_row_and_drive(self):
        mid = self._seed_msg("g-1", "Finnair")
        resp = self.client.post(f"/api/messages/{mid}/reprocess-full")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["had_coupling"])
        self.assertTrue(body["had_drive"])
        # Drive-fil raderad
        self.mock_drive.delete_file.assert_called_with("drv-g-1")
        # ProcessedMessage borta
        with self.SessionLocal() as db:
            self.assertIsNone(
                db.query(self.ProcessedMessage).filter_by(id=mid).first(),
            )

    def test_coupled_without_force_returns_warning(self):
        mid = self._seed_msg(
            "g-2", "Finnair",
            bezala_transaction_id="bz-1",
            bezala_upload_status="success",
        )
        resp = self.client.post(f"/api/messages/{mid}/reprocess-full")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["warning"])
        self.assertTrue(body["is_coupled"])
        # Inga sidoeffekter
        self.mock_drive.delete_file.assert_not_called()
        with self.SessionLocal() as db:
            self.assertIsNotNone(
                db.query(self.ProcessedMessage).filter_by(id=mid).first(),
            )

    def test_coupled_with_force_proceeds(self):
        mid = self._seed_msg(
            "g-3", "Finnair",
            bezala_transaction_id="bz-1",
            bezala_upload_status="success",
        )
        resp = self.client.post(
            f"/api/messages/{mid}/reprocess-full?force=true",
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["had_coupling"])

    def test_clears_trip_message_coupling(self):
        mid = self._seed_msg("g-4", "Finnair")
        self._seed_trip_with_messages("T", ["g-4"])
        with self.SessionLocal() as db:
            self.assertEqual(db.query(self.TripMessage).count(), 1)
        self.client.post(f"/api/messages/{mid}/reprocess-full")
        with self.SessionLocal() as db:
            self.assertEqual(db.query(self.TripMessage).count(), 0)

    def test_missing_message_returns_404(self):
        resp = self.client.post("/api/messages/9999/reprocess-full")
        self.assertEqual(resp.status_code, 404)

    def test_drive_failure_does_not_block(self):
        """Drive-fel → logga warning, fortsätt med radering ändå."""
        self.mock_drive.delete_file.side_effect = RuntimeError("drive boom")
        mid = self._seed_msg("g-5", "Finnair")
        resp = self.client.post(f"/api/messages/{mid}/reprocess-full")
        self.assertEqual(resp.status_code, 200)
        with self.SessionLocal() as db:
            self.assertIsNone(
                db.query(self.ProcessedMessage).filter_by(id=mid).first(),
            )


if __name__ == "__main__":
    unittest.main()
