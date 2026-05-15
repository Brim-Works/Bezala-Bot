"""Tester för POST /api/messages/force-process + pipeline.force_process_message_ids.

Täcker:
- Tvångsprocessar explicita message_ids (förbi Gmail-queryns filter)
- Tar bort Bezala-Klar-etiketten i Gmail innan körning
- Raderar befintlig ProcessedMessage-rad så pipeline-dedupen släpper igenom
- Returnerar per-mail details med outcome + vendor/subject
- Validerar body (tomt → 400, >50 → 400)
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

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


class ForceProcessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db_module = _configure_memory_engine()

        from app.db import Base
        from app import models  # noqa: F401
        from app import main as app_module
        from app.models import ProcessedMessage
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

        from app.services import pipeline as pipeline_module
        pipeline_module.session_scope = session_scope

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

        cls.app_module = app_module
        cls.pipeline_module = pipeline_module
        cls.SessionLocal = SessionLocal
        cls.ProcessedMessage = ProcessedMessage
        cls.client = TestClient(app_module.app)

    @classmethod
    def tearDownClass(cls):
        cls.app_module.app.dependency_overrides.clear()

    def setUp(self):
        with self.SessionLocal() as db:
            db.query(self.ProcessedMessage).delete()
            db.commit()

    def _seed(self, message_id: str, **kwargs) -> None:
        with self.SessionLocal() as db:
            db.add(self.ProcessedMessage(
                message_id=message_id,
                status=kwargs.pop("status", "saved"),
                **kwargs,
            ))
            db.commit()

    def test_force_process_runs_pipeline_on_explicit_ids(self):
        """Pipeline körs för varje skickad id, oavsett om Gmail-queryn
        skulle ha hittat dem."""
        fake_gmail = MagicMock()
        called: list[str] = []

        def fake_process(mid, gmail, drive, namer, analyzer, bezala, result,
                         **kwargs):
            called.append(mid)
            result.processed += 1

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer",
            return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/messages/force-process",
                json={"message_ids": ["19e18b64479119b4", "19e173ecefd8b014"]},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["found"], 2)
        self.assertEqual(body["processed"], 2)
        self.assertEqual(body["failed"], 0)
        self.assertEqual(
            set(called),
            {"19e18b64479119b4", "19e173ecefd8b014"},
        )
        # Bezala-Klar tas bort i Gmail för båda
        self.assertEqual(fake_gmail.remove_done.call_count, 2)

    def test_force_process_deletes_existing_rows(self):
        """En existerande ProcessedMessage-rad raderas så pipeline-dedupen
        släpper igenom körningen."""
        self._seed("19e1xxx", status="skipped:html_pdf_failed")

        fake_gmail = MagicMock()
        seen_in_pipeline: list[bool] = []

        def fake_process(mid, gmail, drive, namer, analyzer, bezala, result,
                         **kwargs):
            with self.SessionLocal() as db:
                row = db.query(self.ProcessedMessage).filter_by(
                    message_id=mid,
                ).first()
                seen_in_pipeline.append(row is None)
            result.processed += 1

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer",
            return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/messages/force-process",
                json={"message_ids": ["19e1xxx"]},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(seen_in_pipeline, [True],
                         "raden skulle ha raderats innan pipelinen körs")

    def test_force_process_keeps_row_when_delete_existing_row_false(self):
        """delete_existing_row=False → raden behålls (caller får ansvara
        för dedup-checken)."""
        self._seed("19e1keep", status="saved", file_name="x.pdf")
        fake_gmail = MagicMock()

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer",
            return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            return_value=None,
        ):
            resp = self.client.post(
                "/api/messages/force-process",
                json={
                    "message_ids": ["19e1keep"],
                    "delete_existing_row": False,
                    "remove_done_label": False,
                },
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        # Raden ligger fortfarande kvar
        with self.SessionLocal() as db:
            row = db.query(self.ProcessedMessage).filter_by(
                message_id="19e1keep",
            ).first()
            self.assertIsNotNone(row)
        # remove_done_label=False → Gmail.remove_done aldrig anropad
        self.assertFalse(fake_gmail.remove_done.called)

    def test_force_process_handles_pipeline_error_per_message(self):
        """En kraschad bearbetning räknas som failed; resten fortsätter."""
        fake_gmail = MagicMock()

        def fake_process(mid, gmail, drive, namer, analyzer, bezala, result,
                         **kwargs):
            if mid == "boom":
                raise RuntimeError("nope")
            result.processed += 1

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer",
            return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/messages/force-process",
                json={"message_ids": ["ok", "boom", "ok2"]},
            )

        body = resp.json()
        self.assertEqual(body["found"], 3)
        self.assertEqual(body["processed"], 2)
        self.assertEqual(body["failed"], 1)
        outcomes = {d["message_id"]: d["outcome"] for d in body["details"]}
        self.assertEqual(outcomes["boom"], "error")
        self.assertEqual(outcomes["ok"], "processed")

    def test_force_process_validates_empty_payload(self):
        resp = self.client.post(
            "/api/messages/force-process", json={"message_ids": []},
        )
        self.assertEqual(resp.status_code, 400)

    def test_force_process_caps_at_50_ids(self):
        resp = self.client.post(
            "/api/messages/force-process",
            json={"message_ids": [f"id-{i}" for i in range(51)]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_force_process_passes_force_true_to_pipeline(self):
        """force_process_message_ids ska anropa _process_one_message med
        force=True så pipeline kringgår SavedFile/Drive-dedupen."""
        fake_gmail = MagicMock()
        captured_kwargs: dict = {}

        def fake_process(mid, *a, **kw):
            captured_kwargs.update(kw)
            a[5].processed += 1  # result

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer",
            return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/messages/force-process",
                json={"message_ids": ["abc123"]},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(captured_kwargs.get("force"),
                        "force=True ska skickas till _process_one_message")

    def test_force_process_surfaces_skip_reason(self):
        """När pipeline lägger en filtered-entry ska force-process
        rapportera skip_reason + skip_detail per detail."""
        fake_gmail = MagicMock()

        def fake_process(mid, gmail, drive, namer, analyzer, bezala, result,
                         **kwargs):
            # Simulera html_pdf_failed-skip — pipeline-konventionen är att
            # även lägga raden i result.filtered.
            result.skipped += 1
            result.filtered.append({
                "message_id": mid,
                "sender": "noreply@moovy.fi",
                "subject": "Moovy: kvitto #1",
                "received_at": None,
                "reason": "html_pdf_failed",
                "confidence": None,
                "detail": "HTML→PDF kraschade",
            })

        with patch.object(
            self.pipeline_module, "GmailClient", return_value=fake_gmail,
        ), patch.object(
            self.pipeline_module, "DriveClient", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "FileNamer", return_value=MagicMock(),
        ), patch.object(
            self.pipeline_module, "ReceiptAnalyzer",
            return_value=MagicMock(enabled=False),
        ), patch.object(
            self.pipeline_module, "_process_one_message",
            side_effect=fake_process,
        ):
            resp = self.client.post(
                "/api/messages/force-process",
                json={"message_ids": ["mid-1", "mid-2"]},
            )

        body = resp.json()
        self.assertEqual(body["skipped"], 2)
        by_id = {d["message_id"]: d for d in body["details"]}
        self.assertEqual(by_id["mid-1"]["skip_reason"], "html_pdf_failed")
        self.assertEqual(
            by_id["mid-1"]["skip_detail"], "HTML→PDF kraschade",
        )
        # Varje detail har bara sin egen entry — inte hela listan från andra
        self.assertEqual(len(by_id["mid-1"]["filtered_entries"]), 1)
        self.assertEqual(len(by_id["mid-2"]["filtered_entries"]), 1)

    def test_force_process_records_filename_collision(self):
        """När force=False och SavedFile-raden krockar lägger pipeline
        en filtered-entry med reason='filename_already_saved'. Den här
        regressionen säkrar att silent-skip:en aldrig kommer tillbaka."""
        from datetime import datetime

        from app.services.pipeline import (
            FILTERED_REASON_FILENAME_ALREADY_SAVED,
            ScanResult,
            _process_one_message,
        )
        from app.services.gmail_client import Attachment, GmailMessage
        from app.models import SavedFile

        # Seed en blockerande SavedFile-rad
        with self.SessionLocal() as db:
            db.add(SavedFile(
                file_name="kollision.pdf",
                file_date="20260507",
                drive_file_id="drv-old",
            ))
            db.commit()

        msg = GmailMessage(
            message_id="mid-x",
            thread_id="th-x",
            sender="noreply@moovy.fi",
            subject="Moovy: kvitto",
            received_at=datetime(2026, 5, 7, 12, 0, 0),
            snippet="",
            attachments=[
                Attachment(filename="orig.pdf", mime_type="application/pdf",
                           data=b"%PDF-1.4\n%fake"),
            ],
            body_text="",
            body_html="",
        )

        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_drive.filename_exists.return_value = False
        fake_namer = MagicMock()
        fake_namer.name_for.return_value = "kollision.pdf"

        # AI off så pipeline går direkt till filnamn-checken
        analyzer = MagicMock(enabled=False)

        result = ScanResult()

        with patch(
            "app.services.pipeline.looks_like_pdf", return_value=True,
        ):
            _process_one_message(
                "mid-x", fake_gmail, fake_drive, fake_namer, analyzer,
                None, result,
                use_ai=False, force=False,
            )

        self.assertEqual(result.skipped, 1)
        self.assertEqual(len(result.filtered), 1)
        self.assertEqual(
            result.filtered[0]["reason"],
            FILTERED_REASON_FILENAME_ALREADY_SAVED,
        )
        self.assertIn("kollision.pdf", result.filtered[0]["detail"])

    def test_force_true_clears_savedfile_collision(self):
        """Med force=True ska pipeline rensa SavedFile-raden och låta
        uploaden gå igenom (ingen skip-entry, processed +1)."""
        from datetime import datetime
        from collections import namedtuple

        from app.services.pipeline import (
            ScanResult,
            _process_one_message,
        )
        from app.services.gmail_client import Attachment, GmailMessage
        from app.models import SavedFile

        with self.SessionLocal() as db:
            db.add(SavedFile(
                file_name="forced.pdf",
                file_date="20260430",
                drive_file_id="drv-blocker",
            ))
            db.commit()

        msg = GmailMessage(
            message_id="mid-forced",
            thread_id="th-forced",
            sender="noreply@moovy.fi",
            subject="Moovy",
            received_at=datetime(2026, 4, 30, 12, 0, 0),
            snippet="",
            attachments=[
                Attachment(filename="x.pdf", mime_type="application/pdf",
                           data=b"%PDF-1.4\n%fake"),
            ],
            body_text="", body_html="",
        )

        Upload = namedtuple("Upload", ["file_id", "web_view_link"])
        fake_gmail = MagicMock()
        fake_gmail.fetch_message.return_value = msg
        fake_drive = MagicMock()
        fake_drive.filename_exists.return_value = True  # även detta hade
        # blockerat utan force
        fake_drive.upload_pdf.return_value = Upload("drv-new", "http://x")
        fake_namer = MagicMock()
        fake_namer.name_for.return_value = "forced.pdf"
        analyzer = MagicMock(enabled=False)

        result = ScanResult()

        with patch(
            "app.services.pipeline.looks_like_pdf", return_value=True,
        ):
            _process_one_message(
                "mid-forced", fake_gmail, fake_drive, fake_namer, analyzer,
                None, result,
                use_ai=False, force=True,
            )

        self.assertEqual(result.processed, 1, "uploaden ska gå igenom")
        self.assertEqual(result.skipped, 0)
        # Den gamla SavedFile-raden ska vara borta, ersatt av en ny
        with self.SessionLocal() as db:
            rows = db.query(SavedFile).filter_by(
                file_name="forced.pdf",
            ).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].drive_file_id, "drv-new")
        # Och Drive.upload_pdf KALLADES trots filename_exists=True
        fake_drive.upload_pdf.assert_called_once()

    def test_force_process_gmail_init_error_returns_payload(self):
        with patch.object(
            self.pipeline_module, "GmailClient",
            side_effect=RuntimeError("no token"),
        ):
            resp = self.client.post(
                "/api/messages/force-process",
                json={"message_ids": ["x"]},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["found"], 0)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
