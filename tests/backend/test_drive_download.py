"""Tester för DriveClient.download_pdf — säkerställer att tom respons
eller None räknas som fel (inte tyst levereras vidare)."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")
os.environ.setdefault("DRIVE_CLIENT_ID", "drive-client")
os.environ.setdefault("DRIVE_CLIENT_SECRET", "drive-secret")
os.environ.setdefault("DRIVE_REFRESH_TOKEN", "drive-refresh")
os.environ.setdefault("DRIVE_FOLDER_ID", "folder-abc")


def _make_drive_client():
    """Konstruerar DriveClient utan att initialisera Google-klienten."""
    from app.services.drive_client import DriveClient

    client = DriveClient.__new__(DriveClient)
    client._service = MagicMock()
    client._folder_id = "folder-abc"
    return client


class DownloadPdfHardeningTest(unittest.TestCase):
    def _configure_media(self, client, return_value):
        """Mockar self._service.files().get_media(fileId=...).execute()."""
        media = MagicMock()
        media.execute.return_value = return_value
        files_ep = MagicMock()
        files_ep.get_media.return_value = media
        client._service.files.return_value = files_ep

    def test_happy_path_returns_bytes(self):
        client = _make_drive_client()
        self._configure_media(client, b"%PDF-1.4\ncontent")
        result = client.download_pdf("file-123")
        self.assertEqual(result, b"%PDF-1.4\ncontent")

    def test_empty_response_raises(self):
        """Tom bytes från Drive → RuntimeError istället för tyst leverans."""
        client = _make_drive_client()
        self._configure_media(client, b"")
        with self.assertRaises(RuntimeError) as ctx:
            client.download_pdf("file-123")
        self.assertIn("tom respons", str(ctx.exception).lower())

    def test_none_response_raises(self):
        client = _make_drive_client()
        self._configure_media(client, None)
        with self.assertRaises(RuntimeError):
            client.download_pdf("file-123")

    def test_non_bytes_response_raises(self):
        """Om Google returnerar en sträng eller annat oväntat → RuntimeError."""
        client = _make_drive_client()
        self._configure_media(client, "not bytes")
        with self.assertRaises(RuntimeError) as ctx:
            client.download_pdf("file-123")
        self.assertIn("oväntad typ", str(ctx.exception).lower())

    def test_missing_file_id_raises(self):
        client = _make_drive_client()
        with self.assertRaises(ValueError):
            client.download_pdf("")
        with self.assertRaises(ValueError):
            client.download_pdf(None)


if __name__ == "__main__":
    unittest.main()
