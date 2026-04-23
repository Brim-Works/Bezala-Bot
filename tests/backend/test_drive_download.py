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


class UploadPdfPermissionGrantTest(unittest.TestCase):
    """Bug 2: Drive-filer får public 'anyone with link reader'-permission
    så användare i webbläsaren (annat Google-konto) kan se previewan."""

    def _setup_create(self, client, file_id: str = "new-file-1"):
        create_call = MagicMock()
        create_call.execute.return_value = {
            "id": file_id,
            "name": "kvitto.pdf",
            "webViewLink": f"https://drive/{file_id}",
        }
        files_ep = MagicMock()
        files_ep.create.return_value = create_call

        permissions_ep = MagicMock()
        perm_call = MagicMock()
        perm_call.execute.return_value = {"id": "perm-1"}
        permissions_ep.create.return_value = perm_call

        client._service.files.return_value = files_ep
        client._service.permissions.return_value = permissions_ep
        return files_ep, permissions_ep

    def test_upload_grants_anyone_reader(self):
        client = _make_drive_client()
        _, perms = self._setup_create(client)

        result = client.upload_pdf("kvitto.pdf", b"%PDF-1.4\nx")

        self.assertEqual(result.file_id, "new-file-1")
        perms.create.assert_called_once()
        kwargs = perms.create.call_args.kwargs
        self.assertEqual(kwargs["fileId"], "new-file-1")
        self.assertEqual(kwargs["body"], {"role": "reader", "type": "anyone"})

    def test_upload_continues_even_if_permission_grant_fails(self):
        """Permission-grant best-effort — Workspace-policy kan blockera,
        filen finns ändå kvar."""
        from googleapiclient.errors import HttpError

        client = _make_drive_client()
        _, perms = self._setup_create(client)
        http_response = MagicMock()
        http_response.status = 403
        http_response.reason = "Forbidden"
        perms.create.return_value.execute.side_effect = HttpError(
            resp=http_response,
            content=b'{"error": "forbidden"}',
        )

        # Ska INTE kasta — filen är ändå upploadad
        result = client.upload_pdf("kvitto.pdf", b"%PDF-1.4\nx")
        self.assertEqual(result.file_id, "new-file-1")


if __name__ == "__main__":
    unittest.main()
