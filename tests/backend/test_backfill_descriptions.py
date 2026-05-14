"""FAS 5.22 — tester för POST /api/messages/backfill-descriptions.

Verifierar service-lagret (DescriptionBackfiller + backfill_rows) samt
API-endpointen med en mockad Claude-klient så vi inte träffar nätverket.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
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


def _make_text_block(text: str):
    """Mock-block som efterliknar anthropic.types.TextBlock."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_claude_response(text: str):
    resp = MagicMock()
    resp.content = [_make_text_block(text)]
    return resp


def _make_row(
    *,
    id: int = 1,
    message_id: str | None = None,
    vendor: str | None = "Finavia",
    summary: str | None = "Parkering Helsinki-Vantaa",
    receipt_date: str | None = "2026-04-22",
    category: str | None = "Parkering",
    amount: float | None = 48.0,
    currency: str | None = "EUR",
    ai_description_en: str | None = None,
    status: str = "saved",
    deleted_at=None,
):
    from app.models import ProcessedMessage

    return ProcessedMessage(
        id=id,
        message_id=message_id or f"m-{id}",
        sender="noreply@finavia.fi",
        subject="Kvitto",
        received_at=datetime(2026, 4, 22, tzinfo=timezone.utc),
        file_name="20260422 Finavia Parkering.pdf",
        status=status,
        vendor=vendor,
        amount=amount,
        currency=currency,
        receipt_date=receipt_date,
        category=category,
        summary=summary,
        ai_description_en=ai_description_en,
        deleted_at=deleted_at,
    )


# ---------- Service-lager ----------


class DescriptionBackfillServiceTest(unittest.TestCase):
    def test_sanitize_strips_quotes_and_trailing_period(self):
        from app.services.description_backfill import _sanitize

        self.assertEqual(
            _sanitize('"Flight Helsinki-Stockholm round trip, 30 April 2026."'),
            "Flight Helsinki-Stockholm round trip, 30 April 2026",
        )
        self.assertIsNone(_sanitize(""))
        self.assertIsNone(_sanitize("   "))

    def test_sanitize_truncates_at_500(self):
        from app.services.description_backfill import _sanitize

        out = _sanitize("A" * 800)
        self.assertEqual(len(out), 500)

    def test_describe_returns_cleaned_text(self):
        from app.services.description_backfill import DescriptionBackfiller

        client = MagicMock()
        client.messages.create.return_value = _make_claude_response(
            '"Parking at Helsinki-Vantaa Airport P2, 22 April 2026"\n'
        )
        bf = DescriptionBackfiller(client=client, model="claude-sonnet-4-6")
        row = _make_row()
        self.assertEqual(
            bf.describe(row),
            "Parking at Helsinki-Vantaa Airport P2, 22 April 2026",
        )
        # Verifierar att prompten innehåller raddata
        kwargs = client.messages.create.call_args.kwargs
        user_msg = kwargs["messages"][0]["content"]
        self.assertIn("Finavia", user_msg)
        self.assertIn("2026-04-22", user_msg)
        self.assertIn("Parkering Helsinki-Vantaa", user_msg)

    def test_backfill_skips_rows_with_existing_description(self):
        from app.services.description_backfill import (
            DescriptionBackfiller,
            backfill_rows,
        )

        client = MagicMock()
        client.messages.create.return_value = _make_claude_response("ignored")
        bf = DescriptionBackfiller(client=client, model="m")

        row = _make_row(
            id=42,
            ai_description_en="Existing English description, 1 April 2026",
        )
        results = backfill_rows([row], bf, sleep_seconds=0)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "skipped")
        self.assertEqual(
            results[0].new, "Existing English description, 1 April 2026"
        )
        # Viktigt: Claude ska INTE ha anropats
        client.messages.create.assert_not_called()
        # Raden ska vara orörd
        self.assertEqual(
            row.ai_description_en, "Existing English description, 1 April 2026"
        )

    def test_backfill_populates_ai_description_en(self):
        from app.services.description_backfill import (
            DescriptionBackfiller,
            backfill_rows,
        )

        client = MagicMock()
        client.messages.create.return_value = _make_claude_response(
            "Parking at Helsinki-Vantaa Airport P2, 22 April 2026"
        )
        bf = DescriptionBackfiller(client=client, model="m")

        row = _make_row(id=596)
        sleep_calls: list[float] = []
        results = backfill_rows(
            [row], bf, sleep_seconds=0.2,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "ok")
        self.assertEqual(results[0].id, 596)
        self.assertIsNone(results[0].old)
        self.assertEqual(
            results[0].new,
            "Parking at Helsinki-Vantaa Airport P2, 22 April 2026",
        )
        self.assertEqual(
            row.ai_description_en,
            "Parking at Helsinki-Vantaa Airport P2, 22 April 2026",
        )
        # Enda raden → ingen sleep mellan rader
        self.assertEqual(sleep_calls, [])

    def test_backfill_sleeps_between_rows(self):
        from app.services.description_backfill import (
            DescriptionBackfiller,
            backfill_rows,
        )

        client = MagicMock()
        client.messages.create.side_effect = [
            _make_claude_response("Description A, 1 April 2026"),
            _make_claude_response("Description B, 2 April 2026"),
            _make_claude_response("Description C, 3 April 2026"),
        ]
        bf = DescriptionBackfiller(client=client, model="m")

        sleep_calls: list[float] = []
        rows = [_make_row(id=i) for i in (1, 2, 3)]
        results = backfill_rows(
            rows, bf, sleep_seconds=0.2,
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        self.assertEqual([r.status for r in results], ["ok", "ok", "ok"])
        # 3 rader → sleep mellan första-andra och andra-tredje = 2 sleeps
        self.assertEqual(sleep_calls, [0.2, 0.2])

    def test_backfill_handles_claude_failure_gracefully(self):
        from app.services.description_backfill import (
            DescriptionBackfiller,
            backfill_rows,
        )

        client = MagicMock()
        client.messages.create.side_effect = [
            RuntimeError("rate limit"),
            _make_claude_response("Description B, 2 April 2026"),
        ]
        bf = DescriptionBackfiller(client=client, model="m")

        rows = [_make_row(id=1), _make_row(id=2)]
        results = backfill_rows(rows, bf, sleep_seconds=0)

        self.assertEqual(results[0].status, "failed")
        self.assertIn("rate limit", results[0].error)
        self.assertIsNone(results[0].new)
        # Andra raden ska processas trots första failet
        self.assertEqual(results[1].status, "ok")
        self.assertEqual(rows[1].ai_description_en, "Description B, 2 April 2026")
        # Första raden ska INTE ha fått ai_description_en
        self.assertIsNone(rows[0].ai_description_en)

    def test_backfill_empty_claude_response_marked_failed(self):
        from app.services.description_backfill import (
            DescriptionBackfiller,
            backfill_rows,
        )

        client = MagicMock()
        client.messages.create.return_value = _make_claude_response("   ")
        bf = DescriptionBackfiller(client=client, model="m")

        row = _make_row(id=1)
        results = backfill_rows([row], bf, sleep_seconds=0)
        self.assertEqual(results[0].status, "failed")
        self.assertIsNone(row.ai_description_en)


# ---------- API-endpoint ----------


class BackfillDescriptionsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        db_module = _configure_memory_engine()
        from app.db import Base
        from app import models  # noqa: F401

        Base.metadata.create_all(bind=db_module.engine)
        cls.SessionLocal = db_module.SessionLocal

        from app.main import app, require_auth

        async def _bypass_auth():
            return None

        app.dependency_overrides[require_auth] = _bypass_auth
        cls._app = app
        cls._require_auth = require_auth
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls._app.dependency_overrides.pop(cls._require_auth, None)

    def setUp(self):
        from app.models import ProcessedMessage

        with self.SessionLocal() as db:
            db.query(ProcessedMessage).delete()
            db.commit()

    def _seed(self, rows):
        with self.SessionLocal() as db:
            for row in rows:
                db.add(row)
            db.commit()

    def _patch_backfiller(self, responses):
        """Patchar DescriptionBackfiller där endpointen importerar den."""
        client = MagicMock()
        client.messages.create.side_effect = [
            _make_claude_response(r) if isinstance(r, str) else r
            for r in responses
        ]

        # Skapa en pre-configured backfiller och returnera den från
        # konstruktorn så endpointen får vår mock.
        from app.services.description_backfill import DescriptionBackfiller
        instance = DescriptionBackfiller(client=client, model="test-model")
        return patch(
            "app.services.description_backfill.DescriptionBackfiller",
            return_value=instance,
        ), client

    def test_endpoint_rejects_empty_body(self):
        resp = self.client.post(
            "/api/messages/backfill-descriptions", json={}
        )
        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertIn("message_ids", resp.json()["detail"])

    def test_endpoint_rejects_both_fields_set(self):
        resp = self.client.post(
            "/api/messages/backfill-descriptions",
            json={"message_ids": [1], "all_missing": True},
        )
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_endpoint_rejects_empty_message_ids_list(self):
        resp = self.client.post(
            "/api/messages/backfill-descriptions",
            json={"message_ids": []},
        )
        # Tom lista → has_ids är False, all_missing också False → 400
        # "Ange exakt EN av ..."
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_endpoint_message_ids_rejects_string(self):
        """message_ids måste vara list[int] — Pydantic 422 vid fel typ."""
        resp = self.client.post(
            "/api/messages/backfill-descriptions",
            json={"message_ids": "abc"},
        )
        self.assertEqual(resp.status_code, 422)

    def test_endpoint_all_missing_filters_correctly(self):
        from app.models import ProcessedMessage

        # En rad med saknad description (saved, ej deleted) → ska processas
        # En rad med existing description → ska EJ inkluderas i resultat
        # En soft-deleted rad → ska EJ inkluderas
        # En rad med status=error → ska EJ inkluderas
        rows_to_seed = [
            _make_row(id=1, message_id="a", ai_description_en=None),
            _make_row(
                id=2, message_id="b",
                ai_description_en="Already English, 1 April 2026",
            ),
            _make_row(
                id=3, message_id="c", ai_description_en=None,
                deleted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            ),
            _make_row(
                id=4, message_id="d", ai_description_en=None, status="error",
            ),
        ]
        self._seed(rows_to_seed)

        patcher, client = self._patch_backfiller(
            ["Parking at Helsinki-Vantaa, 22 April 2026"]
        )
        with patcher:
            resp = self.client.post(
                "/api/messages/backfill-descriptions",
                json={"all_missing": True},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["processed"], 1)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(body["skipped"], 0)
        self.assertEqual(len(body["details"]), 1)
        self.assertEqual(body["details"][0]["id"], 1)
        self.assertEqual(
            body["details"][0]["new"],
            "Parking at Helsinki-Vantaa, 22 April 2026",
        )
        # Verifiera persistens i DB
        with self.SessionLocal() as db:
            row1 = db.query(ProcessedMessage).filter_by(id=1).first()
            self.assertEqual(
                row1.ai_description_en,
                "Parking at Helsinki-Vantaa, 22 April 2026",
            )
            # Existing row är orörd
            row2 = db.query(ProcessedMessage).filter_by(id=2).first()
            self.assertEqual(
                row2.ai_description_en, "Already English, 1 April 2026"
            )
            # Deleted & error orörda
            row3 = db.query(ProcessedMessage).filter_by(id=3).first()
            self.assertIsNone(row3.ai_description_en)
        # Bara EN Claude-anrop trots 4 rader i DB
        self.assertEqual(client.messages.create.call_count, 1)

    def test_endpoint_message_ids_processes_only_listed(self):
        from app.models import ProcessedMessage

        self._seed(
            [
                _make_row(id=10, message_id="x"),
                _make_row(id=11, message_id="y"),
                _make_row(id=12, message_id="z"),
            ]
        )

        patcher, client = self._patch_backfiller(
            [
                "Description 10, 1 April 2026",
                "Description 12, 3 April 2026",
            ]
        )
        with patcher:
            resp = self.client.post(
                "/api/messages/backfill-descriptions",
                json={"message_ids": [10, 12]},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["processed"], 2)
        ids_processed = sorted(d["id"] for d in body["details"])
        self.assertEqual(ids_processed, [10, 12])
        # 11 ska vara orörd
        with self.SessionLocal() as db:
            row11 = db.query(ProcessedMessage).filter_by(id=11).first()
            self.assertIsNone(row11.ai_description_en)

    def test_endpoint_handles_claude_failure_gracefully(self):
        self._seed(
            [
                _make_row(id=20),
                _make_row(id=21),
            ]
        )

        client = MagicMock()
        client.messages.create.side_effect = [
            RuntimeError("anthropic 429"),
            _make_claude_response("OK description, 2 April 2026"),
        ]
        from app.services.description_backfill import DescriptionBackfiller
        instance = DescriptionBackfiller(client=client, model="m")

        with patch(
            "app.services.description_backfill.DescriptionBackfiller",
            return_value=instance,
        ):
            resp = self.client.post(
                "/api/messages/backfill-descriptions",
                json={"all_missing": True},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["processed"], 1)
        self.assertEqual(body["failed"], 1)
        self.assertEqual(body["skipped"], 0)
        details = {d["id"]: d for d in body["details"]}
        self.assertEqual(details[20]["status"], "failed")
        self.assertIn("anthropic 429", details[20]["error"])
        self.assertEqual(details[21]["status"], "ok")

    def test_endpoint_skips_rows_with_existing_description(self):
        # När message_ids pekar på rader som redan har description ska de
        # markeras 'skipped' (inte processas).
        self._seed(
            [
                _make_row(
                    id=30, ai_description_en="Already filled, 1 April 2026"
                ),
            ]
        )
        patcher, client = self._patch_backfiller([])
        with patcher:
            resp = self.client.post(
                "/api/messages/backfill-descriptions",
                json={"message_ids": [30]},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["skipped"], 1)
        self.assertEqual(body["processed"], 0)
        client.messages.create.assert_not_called()

    def test_endpoint_returns_empty_when_no_rows_match(self):
        # Inga rader → tom OK-respons (ingen 503/500 trots saknad API-nyckel)
        resp = self.client.post(
            "/api/messages/backfill-descriptions",
            json={"all_missing": True},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(
            resp.json(),
            {"processed": 0, "failed": 0, "skipped": 0, "details": []},
        )


if __name__ == "__main__":
    unittest.main()
