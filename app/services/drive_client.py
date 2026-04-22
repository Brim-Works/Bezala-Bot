"""Google Drive-klient via OAuth2.

Drive autentiseras med ett SEPARAT konto från Gmail — Visma Workspace blockerar
extern delning av Drive-mappar till tredje-parts OAuth-klienter, så Drive-
uppladdningarna går mot ett privat Gmail-konto där OAuth-klienten är skapad
och där Drive-mappen ligger.

OAuth-klienten är dock samma (GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET) —
bara användaren som godkänner är en annan. Drive-kontots refresh-token
lagras som DRIVE_REFRESH_TOKEN.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config import get_settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/drive.readonly",
]


@dataclass
class DriveUploadResult:
    file_id: str
    web_view_link: str
    name: str


class DriveClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not (
            settings.gmail_client_id
            and settings.gmail_client_secret
            and settings.drive_refresh_token
        ):
            raise RuntimeError(
                "Drive OAuth saknar konfiguration. Sätt GMAIL_CLIENT_ID, "
                "GMAIL_CLIENT_SECRET och DRIVE_REFRESH_TOKEN (refresh-tokenen "
                "måste vara skapad från Drive-kontot och ha godkänt scopen "
                "drive.file + drive.readonly)."
            )

        creds = Credentials(
            token=None,
            refresh_token=settings.drive_refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        creds.refresh(Request())
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._folder_id = settings.google_drive_folder_id

    def upload_pdf(self, filename: str, data: bytes) -> DriveUploadResult:
        media = MediaIoBaseUpload(
            io.BytesIO(data), mimetype="application/pdf", resumable=False
        )
        metadata = {"name": filename, "parents": [self._folder_id]}
        created = (
            self._service.files()
            .create(
                body=metadata,
                media_body=media,
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        logger.info("Laddade upp %s till Drive (id=%s)", filename, created["id"])
        return DriveUploadResult(
            file_id=created["id"],
            web_view_link=created.get("webViewLink", ""),
            name=created.get("name", filename),
        )

    def download_pdf(self, file_id: str) -> bytes:
        """Hämta PDF-bytes från Drive.

        Hårdnar resultatet: tom respons (b'') eller None räknas som fel,
        inte som tom fil. Googles client returnerar normalt bytes — men om
        get_media misslyckas tyst vill vi upptäcka det här istället för att
        skicka noll bytes till Bezala och få oklara nedströmsfel."""
        if not file_id:
            raise ValueError("download_pdf: file_id saknas")
        data = self._service.files().get_media(fileId=file_id).execute()
        if not data:
            raise RuntimeError(
                f"download_pdf: tom respons från Drive för file_id={file_id!r}"
            )
        if not isinstance(data, (bytes, bytearray)):
            raise RuntimeError(
                f"download_pdf: oväntad typ {type(data).__name__} från Drive "
                f"(file_id={file_id!r})"
            )
        return bytes(data)

    def delete_file(self, file_id: str) -> None:
        """Radera en fil permanent från Drive. Används bara vid hard-delete
        när user explicit satt purge_drive=true."""
        self._service.files().delete(
            fileId=file_id, supportsAllDrives=True
        ).execute()

    def filename_exists(self, filename: str) -> bool:
        safe = filename.replace("'", "\\'")
        query = (
            f"name = '{safe}' and '{self._folder_id}' in parents and trashed = false"
        )
        resp = (
            self._service.files()
            .list(q=query, fields="files(id, name)", pageSize=1, supportsAllDrives=True)
            .execute()
        )
        return bool(resp.get("files"))
