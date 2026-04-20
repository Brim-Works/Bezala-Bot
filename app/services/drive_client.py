"""Google Drive-klient via OAuth2 (samma credentials som Gmail).

Använder GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN och
scopet drive.file (begränsad access — bara filer som appen själv skapar).
Service accounts använder vi inte längre eftersom de saknar storage quota
i personligt Drive.
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
            and settings.gmail_refresh_token
        ):
            raise RuntimeError(
                "Drive OAuth saknar konfiguration. Sätt GMAIL_CLIENT_ID, "
                "GMAIL_CLIENT_SECRET och GMAIL_REFRESH_TOKEN (refresh-tokenen "
                "måste ha godkänt scopet drive.file)."
            )

        creds = Credentials(
            token=None,
            refresh_token=settings.gmail_refresh_token,
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
