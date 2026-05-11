"""Tester för Match Health-rapporten (analysvy som klassificerar varför
varje Bezala bill_line inte är matchat).

Strategi:
  - 8 pure-function-tester mot match_health.build_match_health_report med
    fejk Bezala/Gmail-klienter (injicerade via kwargs)
  - 2 endpoint-integrationstester (cache + refresh) via TestClient
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

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
        bind=db_module.engine, autoflush=False, autocommit=False,
    )
    return db_module


def _fake_normalize(raw):
    """Förenklad version av main._normalize_missing_receipt för tester."""
    return {
        "id": raw.get("id"),
        "description": raw.get("description") or raw.get("merchant"),
        "amount": raw.get("amount"),
        "currency": raw.get("currency"),
        "date": raw.get("date"),
    }


def _fake_serialize(row):
    return {
        "id": getattr(row, "id", None),
        "message_id": getattr(row, "message_id", None),
        "vendor": getattr(row, "vendor", None),
        "amount": getattr(row, "amount", None),
        "currency": getattr(row, "currency", None),
        "receipt_date": getattr(row, "receipt_date", None),
        "received_at": None,
        "file_name": getattr(row, "file_name", None),
    }


class MatchHealthPureFunctionTest(unittest.TestCase):
    """Testar build_match_health_report med fejk-klienter — ingen DB."""

    def setUp(self):
        from app.services import match_health
        match_health.clear_cache()
        self.svc = match_health

    # ----- 1: schema-kontroll på svaret -----
    def test_report_has_expected_top_level_schema(self):
        bezala = MagicMock()
        bezala.list_missing_receipts.return_value = []
        gmail = MagicMock()
        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.\
            filter.return_value.filter.return_value.order_by.return_value.\
            limit.return_value.all.return_value = []
        report = self.svc.build_match_health_report(
            db, bezala_client=bezala, gmail_client=gmail,
            normalize_missing_receipt=_fake_normalize,
            serialize_message=_fake_serialize,
        )
        self.assertIn("generated_at", report)
        self.assertIn("rows", report)
        self.assertIn("stats", report)
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["stats"]["total"], 0)

    def _build(self, *, missing, candidates, gmail_with, gmail_without):
        bezala = MagicMock()
        bezala.list_missing_receipts.return_value = missing
        gmail = MagicMock()

        def fake_list(query: str = "", max_results: int = 20):
            return list(
                gmail_with if "has:attachment" in (query or "") else gmail_without,
            )
        gmail.list_candidate_message_ids.side_effect = fake_list

        db = MagicMock()
        db.query.return_value.filter.return_value.filter.return_value.\
            filter.return_value.filter.return_value.order_by.return_value.\
            limit.return_value.all.return_value = candidates
        return self.svc.build_match_health_report(
            db, bezala_client=bezala, gmail_client=gmail,
            normalize_missing_receipt=_fake_normalize,
            serialize_message=_fake_serialize,
            refresh=True,
        )

    # ----- 2: matched_correctly när score >= 80 + stark vendor -----
    def test_matched_correctly_high_score(self):
        # Samma belopp, samma datum, samma vendor → score ≈ 110
        missing = [{
            "id": 1, "description": "MIKKO: ANTHROPIC, US 112.95 EUR",
            "amount": 112.95, "currency": "EUR", "date": "2026-04-14",
        }]
        cand = MagicMock(
            id=10, message_id="m-1", vendor="Anthropic",
            amount=112.95, currency="EUR", receipt_date="2026-04-14",
            file_name="anthropic.pdf",
        )
        report = self._build(
            missing=missing, candidates=[cand],
            gmail_with=["x"], gmail_without=["x"],
        )
        row = report["rows"][0]
        self.assertEqual(row["verdict"]["category"], "matched_correctly")
        self.assertGreaterEqual(row["best_match"]["score"], 80)

    # ----- 3: gmail_miss när has:attachment filtrerar bort -----
    def test_gmail_miss_when_attachment_filter_hides_hits(self):
        missing = [{
            "id": 2, "description": "MIKKO: SKANETRAFIKEN, MALMO 50.00 SEK",
            "amount": 50.00, "currency": "SEK", "date": "2026-04-10",
        }]
        # Inga kandidater i DB → fuzzy=0. Gmail har 5 träffar utan
        # attachment, 0 med → category='filtered' → verdict gmail_miss.
        report = self._build(
            missing=missing, candidates=[],
            gmail_with=[], gmail_without=["a", "b", "c", "d", "e"],
        )
        row = report["rows"][0]
        self.assertEqual(row["verdict"]["category"], "gmail_miss")
        self.assertEqual(row["gmail_status"]["category"], "filtered")
        self.assertEqual(
            row["gmail_status"]["would_match_without_attachment_filter"], 5,
        )

    # ----- 4: no_receipt_exists när 0 Gmail-träffar -----
    def test_no_receipt_exists_when_zero_gmail_hits(self):
        missing = [{
            "id": 3, "description": "MIKKO: LOVABLE, US 100.00 EUR",
            "amount": 100.00, "currency": "EUR", "date": "2026-05-05",
        }]
        report = self._build(
            missing=missing, candidates=[],
            gmail_with=[], gmail_without=[],
        )
        row = report["rows"][0]
        self.assertEqual(row["verdict"]["category"], "no_receipt_exists")
        self.assertEqual(row["gmail_status"]["category"], "no_hits")

    # ----- 5: ai_extraction_wrong när fuzzy finns men match är under tröskeln -----
    def test_ai_extraction_wrong_when_fuzzy_no_strong_match(self):
        # Belopps-mismatch på 7.5%: utanför matcherens ±5%-tolerans
        # (score 0) men inom fuzzy-räknarens ±10% (räknas). Datum 30 dagar
        # fel ger 0 i datum-score. Vendor olikt → ~ 0-5 i vendor-bonus.
        # Total < MIN_DISPLAY_SCORE (50) → find_matches returnerar [].
        # Fuzzy by_amount = 1 → verdict ai_extraction_wrong.
        missing = [{
            "id": 4, "description": "MIKKO: SOMEVENDOR, US 200.00 EUR",
            "amount": 200.00, "currency": "EUR", "date": "2026-04-01",
        }]
        cand = MagicMock(
            id=20, message_id="m-2", vendor="CompletelyDifferent",
            amount=215.00, currency="EUR", receipt_date="2026-03-01",
            file_name="other.pdf",
        )
        report = self._build(
            missing=missing, candidates=[cand],
            gmail_with=[], gmail_without=[],
        )
        row = report["rows"][0]
        self.assertGreater(
            row["fuzzy_candidates"]["by_amount_window_10pct"], 0,
        )
        self.assertIsNone(row["best_match"])
        self.assertEqual(row["verdict"]["category"], "ai_extraction_wrong")

    # ----- 6: match_algorithm_failed när score mellan 50-79 -----
    def test_match_algorithm_failed_when_score_mid_range(self):
        # Belopp matchar (+50) + vendor svag (~0) + datum 2 dagar off (+15)
        # → score ~65, fuzzy finns, vendor-score låg → verdict
        # match_algorithm_failed.
        missing = [{
            "id": 5, "description": "MIKKO: VENDORX 300.00 EUR",
            "amount": 300.00, "currency": "EUR", "date": "2026-04-01",
        }]
        cand = MagicMock(
            id=30, message_id="m-3", vendor="UnrelatedVendor",
            amount=300.00, currency="EUR", receipt_date="2026-04-03",
            file_name="other2.pdf",
        )
        report = self._build(
            missing=missing, candidates=[cand],
            gmail_with=["mail"], gmail_without=["mail"],
        )
        row = report["rows"][0]
        self.assertIsNotNone(row["best_match"])
        score = row["best_match"]["score"]
        # Förvänta oss en score som ligger i match_algorithm_failed-zonen
        self.assertLess(score, 80)
        self.assertGreaterEqual(score, 50)
        self.assertEqual(row["verdict"]["category"], "match_algorithm_failed")

    # ----- 7: Gmail-query bygger med datum-fönster + has:attachment -----
    def test_gmail_query_format(self):
        from datetime import datetime
        q = self.svc._build_gmail_query_for_vendor(
            "Anthropic", datetime(2026, 4, 14), with_attachment=True,
        )
        self.assertIn("from:anthropic", q)
        self.assertIn("after:2026/04/07", q)
        self.assertIn("before:2026/04/22", q)
        self.assertIn("has:attachment", q)
        q_no = self.svc._build_gmail_query_for_vendor(
            "Anthropic", datetime(2026, 4, 14), with_attachment=False,
        )
        self.assertNotIn("has:attachment", q_no)

    # ----- 8: cache fungerar -----
    def test_cache_returns_same_object_until_ttl(self):
        missing = [{
            "id": 99, "description": "MIKKO: VENDORZ 9.00 EUR",
            "amount": 9.00, "currency": "EUR", "date": "2026-04-01",
        }]
        first = self._build(
            missing=missing, candidates=[],
            gmail_with=[], gmail_without=[],
        )
        # Andra anropet UTAN refresh ska returnera cachen och Bezala/Gmail
        # inte ska anropas igen. Bygg en NY MagicMock som skulle krascha
        # vid anrop.
        bezala = MagicMock()
        bezala.list_missing_receipts.side_effect = AssertionError(
            "Bezala-klient anropades trots cache",
        )
        gmail = MagicMock()
        db = MagicMock()
        cached = self.svc.build_match_health_report(
            db, bezala_client=bezala, gmail_client=gmail,
            normalize_missing_receipt=_fake_normalize,
            serialize_message=_fake_serialize,
            refresh=False,
        )
        self.assertEqual(
            len(cached["rows"]), len(first["rows"]),
        )
        self.assertGreaterEqual(cached["cache_age_seconds"], 0)

    # ----- 9: ?refresh=true bypassar cachen -----
    def test_refresh_bypasses_cache(self):
        # Första: 1 missing
        self._build(
            missing=[{
                "id": 1, "description": "MIKKO: A 1.00 EUR",
                "amount": 1.00, "currency": "EUR", "date": "2026-04-01",
            }],
            candidates=[], gmail_with=[], gmail_without=[],
        )
        # Andra med refresh=True och 2 missings — ska se de nya:
        report = self._build(
            missing=[
                {"id": 1, "description": "MIKKO: A 1.00 EUR",
                 "amount": 1.00, "currency": "EUR", "date": "2026-04-01"},
                {"id": 2, "description": "MIKKO: B 2.00 EUR",
                 "amount": 2.00, "currency": "EUR", "date": "2026-04-02"},
            ],
            candidates=[], gmail_with=[], gmail_without=[],
        )
        self.assertEqual(report["stats"]["total"], 2)

    # ----- 10: vendor-extraktion ur merchant-strängen -----
    def test_normalize_merchant_to_vendor(self):
        self.assertEqual(
            self.svc._normalize_merchant_to_vendor(
                "MIKKO KEINONEN: LOVABLE, DOVER, US 100.00 EUR",
            ),
            "LOVABLE",
        )
        self.assertEqual(
            self.svc._normalize_merchant_to_vendor(
                "ANTHROPIC INC 112.95 EUR",
            ),
            "ANTHROPIC INC",
        )
        self.assertIsNone(self.svc._normalize_merchant_to_vendor(None))
        self.assertIsNone(self.svc._normalize_merchant_to_vendor(""))


# ---------- Endpoint-integration ----------


class MatchHealthEndpointTest(unittest.TestCase):
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

        app_module.app.dependency_overrides[app_module.require_auth] = (
            fake_require_auth
        )
        cls.client = TestClient(app_module.app)
        cls.app_module = app_module

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        # Rensa cachen så varje test börjar fräscht.
        from app.services import match_health
        match_health.clear_cache()

    def test_endpoint_returns_schema(self):
        from unittest.mock import patch
        fake_bezala = MagicMock()
        fake_bezala.list_missing_receipts.return_value = []
        fake_bezala.close.return_value = None
        with patch.object(self.app_module, "BezalaClient",
                          return_value=fake_bezala), \
                patch.object(self.app_module, "_get_gmail_client_safe",
                             return_value=None), \
                patch.object(self.app_module, "make_db_rate_provider",
                             return_value=None):
            r = self.client.get("/api/debug/match-health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("rows", body)
        self.assertIn("stats", body)
        self.assertIn("generated_at", body)
        self.assertEqual(body["stats"]["total"], 0)

    def test_refresh_param_bypasses_cache(self):
        from unittest.mock import patch
        fake_bezala = MagicMock()
        fake_bezala.list_missing_receipts.return_value = []
        fake_bezala.close.return_value = None
        with patch.object(self.app_module, "BezalaClient",
                          return_value=fake_bezala), \
                patch.object(self.app_module, "_get_gmail_client_safe",
                             return_value=None), \
                patch.object(self.app_module, "make_db_rate_provider",
                             return_value=None):
            r1 = self.client.get("/api/debug/match-health")
            self.assertEqual(r1.status_code, 200)
            # Refresh ska ge ny generated_at (eller åtminstone inte krascha)
            r2 = self.client.get("/api/debug/match-health?refresh=true")
            self.assertEqual(r2.status_code, 200)
        # Båda ska ha minst BezalaClient instansierad
        self.assertGreaterEqual(fake_bezala.list_missing_receipts.call_count, 2)


if __name__ == "__main__":
    unittest.main()
