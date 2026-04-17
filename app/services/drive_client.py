"""Google Drive-klient via service account JSON från miljövariabel."""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from app.config import get_settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


@dataclass
class DriveUploadResult:
    file_id: str
    web_view_link: str
    name: str


class DriveClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.google_service_account_json:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON saknas. Klistra in HELA service-account JSON "
                "som en sträng i Railway."
            )
        try:
            info = json.loads(settings.google_service_account_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "GOOGLE_SERVICE_ACCOUNT_JSON är inte giltig JSON."
            ) from exc

        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
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
