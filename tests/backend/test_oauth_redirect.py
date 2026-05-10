"""Tester för OAuth redirect_uri-byggaren.

Railway terminerar TLS i sin edge och pratar HTTP internt med appen,
så request.base_url ger `http://...`. _build_oauth_redirect_uri måste
respektera X-Forwarded-Proto för att Google ska acceptera URI:n.
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
os.environ.setdefault("BEZALA_USERNAME", "x")
os.environ.setdefault("BEZALA_PASSWORD", "x")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"


def _make_request(*, headers=None, base_url="http://localhost:8000/"):
    req = MagicMock()
    req.headers = headers or {}
    req.base_url = base_url
    return req


class BuildOAuthRedirectUriTest(unittest.TestCase):
    def setUp(self):
        # Säkra clean miljö per test — env-flagga får inte läcka.
        for key in ("GMAIL_OAUTH_REDIRECT_URI", "DRIVE_OAUTH_REDIRECT_URI"):
            os.environ.pop(key, None)

    def test_explicit_env_var_takes_precedence(self):
        from app.main import _build_oauth_redirect_uri
        with patch.dict(
            os.environ,
            {"GMAIL_OAUTH_REDIRECT_URI": "https://prod.example/api/auth/gmail/callback"},
        ):
            uri = _build_oauth_redirect_uri(
                _make_request(
                    headers={"x-forwarded-proto": "http", "host": "wrong"},
                ),
                "gmail",
            )
        self.assertEqual(uri, "https://prod.example/api/auth/gmail/callback")

    def test_uses_x_forwarded_proto_https_on_railway(self):
        """Railway proxy: X-Forwarded-Proto=https + Host=domän → https URI."""
        from app.main import _build_oauth_redirect_uri
        uri = _build_oauth_redirect_uri(
            _make_request(
                headers={
                    "x-forwarded-proto": "https",
                    "x-forwarded-host": "bezala-bot-production.up.railway.app",
                    "host": "bezala-bot-production.up.railway.app",
                },
                base_url="http://bezala-bot-production.up.railway.app/",
            ),
            "gmail",
        )
        self.assertEqual(
            uri,
            "https://bezala-bot-production.up.railway.app/api/auth/gmail/callback",
        )

    def test_x_forwarded_proto_with_only_host_header(self):
        from app.main import _build_oauth_redirect_uri
        uri = _build_oauth_redirect_uri(
            _make_request(
                headers={
                    "x-forwarded-proto": "https",
                    "host": "example.com",
                },
            ),
            "drive",
        )
        self.assertEqual(uri, "https://example.com/api/auth/drive/callback")

    def test_x_forwarded_proto_takes_first_in_chain(self):
        """Vid flera proxies kommer X-Forwarded-Proto som komma-separerad
        lista — vi tar första värdet."""
        from app.main import _build_oauth_redirect_uri
        uri = _build_oauth_redirect_uri(
            _make_request(
                headers={
                    "x-forwarded-proto": "https, http",
                    "host": "example.com",
                },
            ),
            "gmail",
        )
        self.assertTrue(uri.startswith("https://"))

    def test_falls_back_to_base_url_when_no_proxy_headers(self):
        """Lokal körning utan proxy → använd request.base_url."""
        from app.main import _build_oauth_redirect_uri
        uri = _build_oauth_redirect_uri(
            _make_request(headers={}, base_url="http://localhost:8000/"),
            "gmail",
        )
        self.assertEqual(
            uri, "http://localhost:8000/api/auth/gmail/callback",
        )


if __name__ == "__main__":
    unittest.main()
