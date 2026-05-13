"""Tester för C4: filtrera bort >1 år gamla mail i Gmail-query.

Verifierar att GmailClient.list_candidate_message_ids injicerar
`newer_than:1y` när callern inte gett ett explicit datumfönster, men
låter explicita `after:`/`before:`-queries (reprocess, match_health,
diagnostik) gå igenom orörda.
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

from app.services import gmail_client as gmail_client_module
from app.services.gmail_client import (
    DEFAULT_QUERY,
    GMAIL_MAX_AGE,
    GmailClient,
    _apply_max_age,
    _has_explicit_date_clause,
)


def _make_client_with_list_response(executed_queries: list[str]) -> GmailClient:
    """Bygg en GmailClient som inte kontaktar Gmail. Fångar vilken `q`
    som skickas till messages().list()."""
    client = GmailClient.__new__(GmailClient)

    service = MagicMock()

    def list_side_effect(*, userId, q, maxResults, pageToken):  # noqa: N803
        executed_queries.append(q)
        request = MagicMock()
        request.execute.return_value = {"messages": [], "nextPageToken": None}
        return request

    service.users.return_value.messages.return_value.list.side_effect = list_side_effect
    client._service = service
    client._done_label_id = "Label_X"
    return client


class HelperFunctionTest(unittest.TestCase):
    def test_default_query_includes_newer_than_1y(self):
        self.assertIn(f"newer_than:{GMAIL_MAX_AGE}", DEFAULT_QUERY)
        self.assertIn("newer_than:1y", DEFAULT_QUERY)

    def test_apply_max_age_injects_when_missing(self):
        result = _apply_max_age("has:attachment -in:spam")
        self.assertIn("newer_than:1y", result)

    def test_apply_max_age_skips_when_after_present(self):
        q = "has:attachment after:2024/01/01"
        self.assertEqual(_apply_max_age(q), q)
        self.assertNotIn("newer_than", _apply_max_age(q))

    def test_apply_max_age_skips_when_before_present(self):
        q = "from:foo before:2025/06/01"
        self.assertEqual(_apply_max_age(q), q)

    def test_apply_max_age_skips_when_already_has_newer_than(self):
        q = "has:attachment newer_than:7d"
        self.assertEqual(_apply_max_age(q), q)

    def test_apply_max_age_skips_when_older_than_present(self):
        q = "has:attachment older_than:2y"
        self.assertEqual(_apply_max_age(q), q)

    def test_has_explicit_date_clause_detects_variants(self):
        self.assertTrue(_has_explicit_date_clause("foo AFTER:2024/01/01"))
        self.assertTrue(_has_explicit_date_clause("BEFORE:2024/01/01"))
        self.assertTrue(_has_explicit_date_clause("NEWER_THAN:1d"))
        self.assertTrue(_has_explicit_date_clause("OLDER_THAN:1d"))
        self.assertFalse(_has_explicit_date_clause("has:attachment -in:spam"))


class ListCandidateMessageIdsTest(unittest.TestCase):
    def test_default_query_sent_to_gmail_includes_newer_than_1y(self):
        executed: list[str] = []
        client = _make_client_with_list_response(executed)

        client.list_candidate_message_ids()

        self.assertEqual(len(executed), 1)
        self.assertIn("newer_than:1y", executed[0])

    def test_explicit_after_clause_skips_newer_than(self):
        """match_health, diagnostik osv. sätter egna datum — orör dem."""
        executed: list[str] = []
        client = _make_client_with_list_response(executed)

        explicit = "from:finnair@notify.finnair.com after:2021/01/01 before:2022/01/01"
        client.list_candidate_message_ids(query=explicit)

        self.assertEqual(executed, [explicit])
        self.assertNotIn("newer_than", executed[0])

    def test_reprocess_endpoint_still_works(self):
        """Reprocess bygger query med `after:{datum}` — den ska skickas
        oförändrad till Gmail (vi får inte krympa fönstret)."""
        from app.services.pipeline import _build_reprocess_query

        reprocess_q = _build_reprocess_query(days=400, vendor_filter=None)
        self.assertIn("after:", reprocess_q)

        executed: list[str] = []
        client = _make_client_with_list_response(executed)
        client.list_candidate_message_ids(query=reprocess_q)

        self.assertEqual(executed, [reprocess_q])
        self.assertNotIn("newer_than", executed[0])

    def test_message_id_fetch_unaffected(self):
        """fetch_message_metadata och fetch_message använder
        messages().get(id=...) — inte messages().list(q=...). Filtret
        får aldrig påverka dessa direktslag."""
        client = GmailClient.__new__(GmailClient)
        service = MagicMock()
        get_request = MagicMock()
        get_request.execute.return_value = {
            "id": "abc123",
            "threadId": "t1",
            "snippet": "",
            "labelIds": [],
            "payload": {"headers": []},
        }
        service.users.return_value.messages.return_value.get.return_value = get_request
        client._service = service
        client._done_label_id = "Label_X"

        client.fetch_message_metadata("abc123")

        kwargs = service.users.return_value.messages.return_value.get.call_args.kwargs
        self.assertEqual(kwargs.get("id"), "abc123")
        self.assertNotIn("q", kwargs)


if __name__ == "__main__":
    unittest.main()
