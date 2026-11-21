"""C46 Del 4 — omläsning av jobb-Gmail 2026."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from tests.backend.test_c46_senders import SessionLocal  # noqa: E402,F401
from app.services import c46_rescan as rs  # noqa: E402

OWN = "me@work.example"


def _m(mid, sender, subject, labels=()):
    return {"message_id": mid, "sender": sender, "subject": subject,
            "labels": list(labels), "date": "Tue, 13 Jan 2026 20:10:10 +0000"}


def test_queries_start_2026_and_ignore_done_label():
    row = MagicMock(require_attachments=True, exclude_promotions=True,
                    exclude_social=True, exclude_calendar=True,
                    exclude_senders=[], exclude_subjects=[], include_senders=[])
    qs = rs.rescan_queries(row, ["noreply@moovy.fi"], "Bezala-Klar")
    assert len(qs) == 3
    for q in qs:
        assert "after:2026/01/01" in q
        assert "2026/03/21" not in q
        assert "Bezala-Klar" not in q
    assert "to:app.bezala.com" in qs[2]


def test_original_preferred_over_forward():
    msgs = [
        _m("o1", "Clarion <cl.seau@strawberry.se>", "Bill SE131B0074992 from Clarion"),
        _m("f1", f"Me <{OWN}>", "Fwd: Bill SE131B0074992 from Clarion", ["SENT"]),
    ]
    picked = rs.pick_messages(msgs, OWN)
    assert [p["message_id"] for p in picked] == ["o1"]
    assert picked[0]["forwarded"] is False


def test_forward_used_when_only_forward_exists():
    msgs = [_m("f1", f"Me <{OWN}>", "Fwd: Kvitto 123", ["SENT"]),
            _m("r1", f"Me <{OWN}>", "Re: Kvitto 123", ["SENT"])]
    picked = rs.pick_messages(msgs, OWN)
    assert [p["message_id"] for p in picked] == ["f1"]
    assert picked[0]["forwarded"] is True


def test_all_messages_in_thread_and_known_skipped(SessionLocal):  # noqa: F811
    from app.models import ProcessedMessage

    with SessionLocal() as db:
        db.add(ProcessedMessage(message_id="m1", status="saved"))
        db.commit()
    gmail = MagicMock()
    gmail.get_profile_email.return_value = OWN
    gmail.list_candidates_with_threads.return_value = [("m1", "t1")]
    gmail.list_thread_messages.return_value = [
        _m("m1", "noreply@moovy.fi", "Moovy: kvitto #32 973 737"),
        _m("m2", "noreply@moovy.fi", "Moovy: kvitto #34 793 894"),
        _m("m3", f"Me <{OWN}>", "Fwd: Moovy: kvitto #34 793 894", ["SENT"]),
    ]
    with patch.object(rs.time, "sleep"):
        cands = rs.collect_candidates(gmail, ["q1", "q2"])
    assert [c["message_id"] for c in cands] == ["m2"]
    rep = rs.report(cands)
    assert rep["candidates"] == 1
    assert rep["per_sender"] == {"noreply@moovy.fi": 1}
    assert rep["examples"]["Moovy 5 mar"]["found"] is True
    assert rep["examples"]["Moovy 13 jan"]["found"] is False


def test_real_run_never_uploads_to_bezala(SessionLocal):  # noqa: F811
    gmail = MagicMock()
    cands = [{"message_id": "x1", "thread_id": "t", "sender": "a@b.se",
              "subject": "Fwd: kvitto", "forwarded": True}]
    deps = {"clients": (MagicMock(), MagicMock(), MagicMock(), MagicMock()),
            "kwargs": {"auto_upload": True, "use_ai": True, "bezala_metadata": {}}}
    seen = {}

    def fake_process(mid, g, drive, namer, analyzer, bezala, result, **kw):
        seen.update(kw, bezala=bezala)

    with patch.object(rs, "collect_candidates", return_value=cands), \
         patch("app.services.c45_migrations._scan_deps", return_value=deps), \
         patch("app.services.pipeline._process_one_message", side_effect=fake_process):
        out = rs.run(dry_run=False, gmail=gmail)
    assert seen["auto_upload"] is False and seen["bezala"] is None
    assert out["dry_run"] is False


def test_dry_run_processes_nothing(SessionLocal):  # noqa: F811
    with patch.object(rs, "collect_candidates", return_value=[]), \
         patch("app.services.pipeline._process_one_message") as proc:
        out = rs.run(dry_run=True, gmail=MagicMock())
    proc.assert_not_called()
    assert out["dry_run"] is True and out["candidates"] == 0
