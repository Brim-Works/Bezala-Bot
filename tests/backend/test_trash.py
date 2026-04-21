"""Backend-tester för soft-delete + papperskorg.

Körs med:
    python -m unittest tests.backend.test_trash

Använder stdlib `unittest` + FastAPI TestClient (httpx redan dep).
Sätter upp en tillfällig SQLite-DB per test och override:ar Gmail/Drive
så att trash-endpoints körs utan externa beroenden.
"""

import os
import unittest

# Säkerställ minimalt testmiljö INNAN app-import
os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "")
os.environ.setdefault("DRIVE_REFRESH_TOKEN", "")
os.environ.setdefault("BEZALA_USERNAME", "")
os.environ.setdefault("BEZALA_PASSWORD", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


def _configure_memory_engine():
    """SQLAlchemy memory-DB delas inte mellan connections per default.
    Vi byter engine till en StaticPool så alla sessions ser samma DB.
    Måste köras EFTER app.db-importen."""
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


class TrashEndpointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()

        # Importera main SIST så dependencies använder nya engine
        from app import main as app_module
        from app.models import ProcessedMessage
        from fastapi.testclient import TestClient

        # Skapa tabeller på nya engine:n
        from app.db import Base
        from app import models  # noqa: F401 — ladda modeller
        Base.metadata.create_all(bind=db_module.engine)

        # Patch session_scope + get_db att använda nya SessionLocal
        from app.services import (
            trash_scheduler as ts_module,
            settings_service as ss_module,
        )
        cls._patch_sessions(db_module, app_module, ts_module, ss_module)

        cls.app_module = app_module
        cls.SessionLocal = db_module.SessionLocal
        cls.ProcessedMessage = ProcessedMessage

        async def fake_require_auth():
            return None

        app_module.app.dependency_overrides[app_module.require_auth] = fake_require_auth
        cls.client = TestClient(app_module.app)

    @staticmethod
    def _patch_sessions(db_module, app_module, ts_module, ss_module):
        # Säkerställ att alla referenser använder den nya SessionLocal
        SessionLocal = db_module.SessionLocal

        from contextlib import contextmanager

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
        # main.py har importerat get_db direkt — patch:a attribut
        app_module.get_db = get_db
        app_module.session_scope = session_scope
        # servicelager som importerat session_scope
        ts_module.session_scope = session_scope
        # settings_service använder inte session_scope, men load_settings tar db-argument

        # Uppdatera FastAPI:s dependency-overrides för get_db
        try:
            from app.db import get_db as original_get_db
            app_module.app.dependency_overrides[original_get_db] = get_db
        except Exception:
            pass

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        # Rensa messages mellan tester så raderna inte hänger kvar
        with self.SessionLocal() as db:
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _seed(self, count=3):
        ids = []
        with self.SessionLocal() as db:
            for i in range(count):
                row = self.ProcessedMessage(
                    message_id=f"gm-{i}",
                    sender=f"sender{i}@x.com",
                    subject=f"Subject {i}",
                    status="saved",
                    vendor=f"Vendor {i}",
                    amount=100.0 + i,
                    currency="EUR",
                )
                db.add(row)
                db.flush()
                ids.append(row.id)
            db.commit()
        return ids

    # ---- Test-cases ----

    def test_soft_delete_sets_deleted_at(self):
        [mid] = self._seed(1)
        resp = self.client.request(
            "DELETE",
            f"/api/messages/{mid}",
            json={"reason": "calendar"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIsNotNone(body["deleted_at"])
        self.assertEqual(body["delete_reason"], "calendar")

    def test_list_excludes_soft_deleted_by_default(self):
        ids = self._seed(2)
        # soft-delete första
        self.client.request("DELETE", f"/api/messages/{ids[0]}", json={"reason": "manual"})

        resp = self.client.get("/api/messages?limit=50")
        self.assertEqual(resp.status_code, 200)
        returned = resp.json()
        returned_ids = {r["id"] for r in returned}
        self.assertNotIn(ids[0], returned_ids)
        self.assertIn(ids[1], returned_ids)

    def test_trash_list_returns_deleted_only(self):
        ids = self._seed(2)
        self.client.request("DELETE", f"/api/messages/{ids[0]}", json={"reason": "manual"})
        resp = self.client.get("/api/messages/trash")
        self.assertEqual(resp.status_code, 200)
        returned = resp.json()
        returned_ids = {r["id"] for r in returned}
        self.assertEqual(returned_ids, {ids[0]})

    def test_restore_clears_deleted_at(self):
        [mid] = self._seed(1)
        self.client.request("DELETE", f"/api/messages/{mid}", json={"reason": "manual"})
        resp = self.client.post(f"/api/messages/{mid}/restore")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["deleted_at"])
        self.assertIsNone(body["delete_reason"])

    def test_permanent_delete_removes_row(self):
        [mid] = self._seed(1)
        # soft-delete först
        self.client.request("DELETE", f"/api/messages/{mid}", json={"reason": "manual"})
        # sedan hard-delete
        resp = self.client.request(
            "DELETE", f"/api/messages/{mid}?permanent=true&purge_drive=false"
        )
        self.assertEqual(resp.status_code, 200)
        # Kolla i DB att raden är borta
        with self.SessionLocal() as db:
            row = db.query(self.ProcessedMessage).filter_by(id=mid).first()
            self.assertIsNone(row)

    def test_bulk_soft_delete_marks_all(self):
        ids = self._seed(3)
        resp = self.client.post(
            "/api/messages/bulk-delete",
            json={"ids": ids, "reason": "calendar"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["deleted"], 3)
        self.assertFalse(body["permanent"])
        # Verifiera i DB
        with self.SessionLocal() as db:
            rows = db.query(self.ProcessedMessage).filter(
                self.ProcessedMessage.id.in_(ids)
            ).all()
            for row in rows:
                self.assertIsNotNone(row.deleted_at)
                self.assertEqual(row.delete_reason, "calendar")

    def test_empty_trash_hard_deletes_all(self):
        ids = self._seed(3)
        for mid in ids:
            self.client.request(
                "DELETE", f"/api/messages/{mid}", json={"reason": "manual"}
            )
        # töm papperskorg
        resp = self.client.request("DELETE", "/api/messages/trash")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["deleted"], 3)
        with self.SessionLocal() as db:
            count = db.query(self.ProcessedMessage).count()
            self.assertEqual(count, 0)

    def test_trash_count_endpoint(self):
        ids = self._seed(3)
        self.client.request("DELETE", f"/api/messages/{ids[0]}", json={"reason": "manual"})
        self.client.request("DELETE", f"/api/messages/{ids[1]}", json={"reason": "spam"})
        resp = self.client.get("/api/messages/trash/count")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["count"], 2)


class AuthRequiredTest(unittest.TestCase):
    """Verifiera att trash-endpoints kräver auth när require_auth är aktiv."""

    @classmethod
    def setUpClass(cls):
        from app import main as app_module
        from fastapi.testclient import TestClient

        cls.app_module = app_module
        app_module.app.dependency_overrides.clear()
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_trash_list_requires_auth(self):
        resp = self.client.get("/api/messages/trash")
        self.assertEqual(resp.status_code, 401)

    def test_delete_requires_auth(self):
        resp = self.client.request(
            "DELETE", "/api/messages/1", json={"reason": "manual"}
        )
        self.assertEqual(resp.status_code, 401)

    def test_trash_count_requires_auth(self):
        resp = self.client.get("/api/messages/trash/count")
        self.assertEqual(resp.status_code, 401)


if __name__ == "__main__":
    unittest.main()
