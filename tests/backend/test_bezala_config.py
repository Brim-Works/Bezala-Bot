"""Tester för Bezala config-admin (vendor→account+VAT-mappning).

Täcker:
- CRUD via service-lagret
- CRUD via FastAPI-endpoints (/api/bezala-config)
- Seed-data (Moovy/Finavia → konto 67113, vat 25.5%)
"""

from __future__ import annotations

import os
import unittest
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


# ---------- Service-lager ----------


class BezalaConfigServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=db_module.engine)
        cls.SessionLocal = db_module.SessionLocal

    def setUp(self):
        from app.models import BezalaVendorMapping, MaintenanceTask
        with self.SessionLocal() as db:
            db.query(BezalaVendorMapping).delete()
            db.query(MaintenanceTask).delete()
            db.commit()

    def test_create_mapping(self):
        from app.services.bezala_config import create_mapping
        with self.SessionLocal() as db:
            row = create_mapping(
                db,
                vendor_pattern="Moovy",
                bezala_account_id=67113,
                vat_rate="25.5",
                description_override="Parkering",
            )
            self.assertEqual(row.vendor_pattern, "moovy")
            self.assertEqual(row.bezala_account_id, 67113)
            self.assertEqual(Decimal(str(row.vat_rate)), Decimal("25.50"))
            self.assertEqual(row.description_override, "Parkering")

    def test_create_rejects_duplicate(self):
        from app.services.bezala_config import create_mapping
        with self.SessionLocal() as db:
            create_mapping(
                db, vendor_pattern="Moovy",
                bezala_account_id=67113, vat_rate="25.5",
            )
            with self.assertRaises(ValueError):
                create_mapping(
                    db, vendor_pattern="MOOVY",
                    bezala_account_id=67113, vat_rate="25.5",
                )

    def test_create_rejects_invalid_vat(self):
        from app.services.bezala_config import create_mapping
        with self.SessionLocal() as db:
            with self.assertRaises(ValueError):
                create_mapping(
                    db, vendor_pattern="X",
                    bezala_account_id=67113, vat_rate="-1",
                )
            with self.assertRaises(ValueError):
                create_mapping(
                    db, vendor_pattern="Y",
                    bezala_account_id=67113, vat_rate="101",
                )

    def test_list_mappings(self):
        from app.services.bezala_config import (
            create_mapping, list_mappings,
        )
        with self.SessionLocal() as db:
            create_mapping(
                db, vendor_pattern="Moovy",
                bezala_account_id=67113, vat_rate="25.5",
            )
            create_mapping(
                db, vendor_pattern="Finavia",
                bezala_account_id=67113, vat_rate="25.5",
            )
            rows = list_mappings(db)
        patterns = [r.vendor_pattern for r in rows]
        self.assertEqual(sorted(patterns), ["finavia", "moovy"])

    def test_update_mapping(self):
        from app.services.bezala_config import (
            create_mapping, update_mapping,
        )
        with self.SessionLocal() as db:
            row = create_mapping(
                db, vendor_pattern="Moovy",
                bezala_account_id=67113, vat_rate="14.0",
            )
            updated = update_mapping(
                db, row.id,
                vat_rate="25.5",
                description_override="Parking HEL",
                description_override_set=True,
            )
            self.assertIsNotNone(updated)
            self.assertEqual(Decimal(str(updated.vat_rate)), Decimal("25.50"))
            self.assertEqual(updated.description_override, "Parking HEL")
            # vendor_pattern oförändrad
            self.assertEqual(updated.vendor_pattern, "moovy")

    def test_update_returns_none_for_missing(self):
        from app.services.bezala_config import update_mapping
        with self.SessionLocal() as db:
            self.assertIsNone(update_mapping(db, 999, vat_rate="25.5"))

    def test_delete_mapping(self):
        from app.services.bezala_config import (
            create_mapping, delete_mapping, get_mapping,
        )
        with self.SessionLocal() as db:
            row = create_mapping(
                db, vendor_pattern="Moovy",
                bezala_account_id=67113, vat_rate="25.5",
            )
            self.assertTrue(delete_mapping(db, row.id))
            self.assertIsNone(get_mapping(db, row.id))
            self.assertFalse(delete_mapping(db, row.id))

    def test_seed_data_present_after_migration(self):
        from app.models import BezalaVendorMapping
        from app.services.bezala_config import seed_default_mappings
        with self.SessionLocal() as db:
            added = seed_default_mappings(db)
        self.assertEqual(added, 5)

        with self.SessionLocal() as db:
            rows = db.query(BezalaVendorMapping).all()
            by_pattern = {r.vendor_pattern: r for r in rows}

        self.assertIn("moovy", by_pattern)
        self.assertIn("finavia", by_pattern)
        for pattern in ("moovy", "finavia"):
            row = by_pattern[pattern]
            self.assertEqual(row.bezala_account_id, 67113)
            self.assertEqual(Decimal(str(row.vat_rate)), Decimal("25.50"))
            self.assertEqual(
                row.description_override,
                "Parking at Helsinki-Vantaa Airport P2",
            )

    def test_lovable_mapping_seeded(self):
        from app.models import BezalaVendorMapping
        from app.services.bezala_config import seed_default_mappings
        with self.SessionLocal() as db:
            seed_default_mappings(db)
            row = (
                db.query(BezalaVendorMapping)
                .filter(BezalaVendorMapping.vendor_pattern == "lovable")
                .one()
            )
        self.assertEqual(row.bezala_account_id, 166648)
        self.assertEqual(Decimal(str(row.vat_rate)), Decimal("0.00"))
        self.assertIsNone(row.description_override)

    def test_anthropic_mapping_seeded(self):
        from app.models import BezalaVendorMapping
        from app.services.bezala_config import seed_default_mappings
        with self.SessionLocal() as db:
            seed_default_mappings(db)
            row = (
                db.query(BezalaVendorMapping)
                .filter(BezalaVendorMapping.vendor_pattern == "anthropic")
                .one()
            )
        self.assertEqual(row.bezala_account_id, 166648)
        self.assertEqual(Decimal(str(row.vat_rate)), Decimal("0.00"))
        self.assertIsNone(row.description_override)

    def test_cursor_mapping_seeded(self):
        from app.models import BezalaVendorMapping
        from app.services.bezala_config import seed_default_mappings
        with self.SessionLocal() as db:
            seed_default_mappings(db)
            row = (
                db.query(BezalaVendorMapping)
                .filter(BezalaVendorMapping.vendor_pattern == "cursor")
                .one()
            )
        self.assertEqual(row.bezala_account_id, 166648)
        self.assertEqual(Decimal(str(row.vat_rate)), Decimal("0.00"))
        self.assertIsNone(row.description_override)

    def test_existing_moovy_finavia_seed_not_affected(self):
        """Användarens egna ändringar på Moovy/Finavia ska bevaras när
        v2-seeden körs och bara fyller på de tre nya AI-leverantörerna."""
        from app.models import BezalaVendorMapping
        from app.services.bezala_config import seed_default_mappings
        with self.SessionLocal() as db:
            # Simulera redan-existerande Moovy-rad med modifierad VAT.
            db.add(BezalaVendorMapping(
                vendor_pattern="moovy",
                bezala_account_id=99999,
                vat_rate=Decimal("14.00"),
                description_override="custom",
            ))
            db.commit()

            added = seed_default_mappings(db)
            # Fyra nya: finavia, lovable, anthropic, cursor (moovy skippas).
            self.assertEqual(added, 4)

            row = (
                db.query(BezalaVendorMapping)
                .filter(BezalaVendorMapping.vendor_pattern == "moovy")
                .one()
            )
            self.assertEqual(row.bezala_account_id, 99999)
            self.assertEqual(Decimal(str(row.vat_rate)), Decimal("14.00"))
            self.assertEqual(row.description_override, "custom")

    def test_seed_is_idempotent(self):
        from app.services.bezala_config import seed_default_mappings
        with self.SessionLocal() as db:
            seed_default_mappings(db)
            second = seed_default_mappings(db)
        self.assertEqual(second, 0)

    def test_find_mapping_for_vendor(self):
        from app.services.bezala_config import (
            create_mapping, find_mapping_for_vendor, list_mappings,
        )
        with self.SessionLocal() as db:
            create_mapping(
                db, vendor_pattern="Moovy",
                bezala_account_id=67113, vat_rate="25.5",
            )
            mappings = list_mappings(db)
        match = find_mapping_for_vendor("Moovy Finland Oy", mappings)
        self.assertIsNotNone(match)
        self.assertEqual(match.vendor_pattern, "moovy")
        self.assertIsNone(find_mapping_for_vendor("Random Vendor", mappings))


# ---------- API-endpoints ----------


class BezalaConfigApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401
        Base.metadata.create_all(bind=db_module.engine)
        cls.SessionLocal = db_module.SessionLocal
        from app.main import app
        from app.routers import bezala_config as router_module

        async def _bypass_auth():
            return None

        app.dependency_overrides[router_module.require_auth] = _bypass_auth
        cls._app = app
        cls._router_module = router_module
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls._app.dependency_overrides.pop(cls._router_module.require_auth, None)

    def setUp(self):
        from app.models import BezalaVendorMapping, MaintenanceTask
        with self.SessionLocal() as db:
            db.query(BezalaVendorMapping).delete()
            db.query(MaintenanceTask).delete()
            db.commit()

    def test_create_and_list(self):
        resp = self.client.post(
            "/api/bezala-config",
            json={
                "vendor_pattern": "Moovy",
                "bezala_account_id": 67113,
                "vat_rate": 25.5,
                "description_override": "Parkering Helsinki",
            },
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        created = resp.json()
        self.assertEqual(created["vendor_pattern"], "moovy")
        self.assertEqual(created["bezala_account_id"], 67113)
        self.assertEqual(Decimal(created["vat_rate"]), Decimal("25.50"))

        list_resp = self.client.get("/api/bezala-config")
        self.assertEqual(list_resp.status_code, 200)
        mappings = list_resp.json()["mappings"]
        self.assertEqual(len(mappings), 1)
        self.assertEqual(mappings[0]["vendor_pattern"], "moovy")

    def test_create_rejects_invalid_payload(self):
        resp = self.client.post(
            "/api/bezala-config",
            json={
                "vendor_pattern": "X",
                "bezala_account_id": 67113,
                "vat_rate": 250,
            },
        )
        self.assertEqual(resp.status_code, 422)

    def test_update_mapping_via_api(self):
        created = self.client.post(
            "/api/bezala-config",
            json={
                "vendor_pattern": "Moovy",
                "bezala_account_id": 67113,
                "vat_rate": 14.0,
            },
        ).json()
        resp = self.client.patch(
            f"/api/bezala-config/{created['id']}",
            json={"vat_rate": 25.5, "description_override": "Parking"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        updated = resp.json()
        self.assertEqual(Decimal(updated["vat_rate"]), Decimal("25.50"))
        self.assertEqual(updated["description_override"], "Parking")

    def test_update_missing_returns_404(self):
        resp = self.client.patch(
            "/api/bezala-config/9999",
            json={"vat_rate": 25.5},
        )
        self.assertEqual(resp.status_code, 404)

    def test_delete_mapping_via_api(self):
        created = self.client.post(
            "/api/bezala-config",
            json={
                "vendor_pattern": "Moovy",
                "bezala_account_id": 67113,
                "vat_rate": 25.5,
            },
        ).json()
        resp = self.client.delete(f"/api/bezala-config/{created['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])

        # Andra anropet → 404
        resp2 = self.client.delete(f"/api/bezala-config/{created['id']}")
        self.assertEqual(resp2.status_code, 404)

    def test_requires_authentication(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.routers import bezala_config as router_module
        # Plocka bort overriden tillfälligt så vi ser 401 på riktigt.
        original = app.dependency_overrides.pop(
            router_module.require_auth, None,
        )
        try:
            anon = TestClient(app)
            resp = anon.get("/api/bezala-config")
            self.assertEqual(resp.status_code, 401)
        finally:
            if original is not None:
                app.dependency_overrides[router_module.require_auth] = original


if __name__ == "__main__":
    unittest.main()
