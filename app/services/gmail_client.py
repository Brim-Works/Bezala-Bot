"""Gmail-klient som bygger Credentials från refresh_token i miljövariabler.

Ingen lokal JSON-fil, ingen interaktiv OAuth-flow i produktion — allt sker via
de env-variabler som satts i Railway.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Iterator

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import get_settings
from app.services.oauth_token_store import (
    OAuthAuthError,
    get_refresh_token,
    is_invalid_grant,
    set_auth_required,
)

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
]

DONE_LABEL = "Bezala-Klar"

GMAIL_MAX_AGE = "1y"  # Don't fetch emails older than this

# Gmail-query: minst en bilaga, inte i Promotions/Social/Updates/Spam/Trash,
# inte redan bearbetad. Gmail auto-expanderar varianter av etikettnamnet.
DEFAULT_QUERY = (
    "has:attachment "
    "-category:promotions -category:social -category:updates "
    "-in:spam -in:trash "
    f'-label:"{DONE_LABEL}" '
    f"newer_than:{GMAIL_MAX_AGE}"
)


def _has_explicit_date_clause(query: str) -> bool:
    """True om callern har satt ett eget datumfönster (after:/before:/
    newer_than:/older_than:). Då injicerar vi inte GMAIL_MAX_AGE."""
    q = query.lower()
    return any(tok in q for tok in ("after:", "before:", "newer_than:", "older_than:"))


def _apply_max_age(query: str) -> str:
    """Lägg till `newer_than:GMAIL_MAX_AGE` om query saknar explicit datum.
    Skyddar pipelinen från att hämta urgamla mail (t.ex. 2021-kvitton)."""
    if _has_explicit_date_clause(query):
        return query
    return f"{query} newer_than:{GMAIL_MAX_AGE}".strip()


@dataclass
class Attachment:
    filename: str
    mime_type: str
    data: bytes
    part_id: str | None = None


@dataclass
class GmailMessage:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    received_at: datetime | None
    snippet: str
    attachments: list[Attachment] = field(default_factory=list)
    body_text: str = ""
    body_html: str = ""


class GmailClient:
    def __init__(self) -> None:
        settings = get_settings()
        refresh_token = get_refresh_token("gmail")
        if not (
            settings.gmail_client_id
            and settings.gmail_client_secret
            and refresh_token
        ):
            # Saknar token helt → samma effekt som invalid_grant ur UI:s
            # synvinkel: användaren måste klicka Återanslut.
            set_auth_required("gmail", True)
            raise OAuthAuthError(
                "gmail",
                "Gmail OAuth saknar konfiguration. Klicka Återanslut Gmail "
                "i Inställningar.",
            )

        self._creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        try:
            self._creds.refresh(Request())
        except RefreshError as exc:
            if is_invalid_grant(exc):
                set_auth_required("gmail", True)
                logger.warning("Gmail invalid_grant — kräver återanslutning: %s", exc)
                raise OAuthAuthError("gmail", str(exc)) from exc
            raise
        self._service = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        self._done_label_id: str | None = None

    # ---------- Labels ----------

    def _ensure_done_label(self) -> str:
        if self._done_label_id:
            return self._done_label_id
        labels = self._service.users().labels().list(userId="me").execute().get("labels", [])
        for lbl in labels:
            if lbl["name"] == DONE_LABEL:
                self._done_label_id = lbl["id"]
                return self._done_label_id
        created = (
            self._service.users()
            .labels()
            .create(
                userId="me",
                body={
                    "name": DONE_LABEL,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        self._done_label_id = created["id"]
        logger.info("Skapade Gmail-etikett %s (id=%s)", DONE_LABEL, self._done_label_id)
        return self._done_label_id

    def mark_done(self, message_id: str) -> None:
        label_id = self._ensure_done_label()
        self._service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"addLabelIds": [label_id]},
        ).execute()

    def remove_done(self, message_id: str) -> None:
        """Ta bort Bezala-Klar-etiketten. Används vid soft-delete/restore."""
        label_id = self._ensure_done_label()
        self._service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": [label_id]},
        ).execute()

    # ---------- Listing ----------

    def list_candidate_message_ids(
        self, query: str = DEFAULT_QUERY, max_results: int = 50
    ) -> list[str]:
        query = _apply_max_age(query)
        ids: list[str] = []
        page_token: str | None = None
        while True:
            req = self._service.users().messages().list(
                userId="me",
                q=query,
                maxResults=min(100, max_results - len(ids)),
                pageToken=page_token,
            )
            resp = req.execute()
            for m in resp.get("messages", []):
                ids.append(m["id"])
                if len(ids) >= max_results:
                    return ids
            page_token = resp.get("nextPageToken")
            if not page_token:
                return ids

    def fetch_message_metadata(self, message_id: str) -> dict:
        """Billig metadata-hämtning: bara headers + labels + snippet.
        Används av debug-endpoints där vi inte vill ladda ner bilagor."""
        raw = (
            self._service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            .execute()
        )
        headers = {
            h["name"].lower(): h["value"]
            for h in raw.get("payload", {}).get("headers", [])
        }
        return {
            "message_id": raw.get("id", message_id),
            "thread_id": raw.get("threadId", ""),
            "sender": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "date": headers.get("date", ""),
            "labels": list(raw.get("labelIds") or []),
            "snippet": raw.get("snippet", ""),
        }

    # ---------- Fetching ----------

    def fetch_messages(self, message_ids: Iterable[str]) -> Iterator[GmailMessage]:
        for mid in message_ids:
            try:
                yield self.fetch_message(mid)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Kunde inte hämta meddelande %s: %s", mid, exc)

    def fetch_message(self, message_id: str) -> GmailMessage:
        raw = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )

        headers = {h["name"].lower(): h["value"] for h in raw.get("payload", {}).get("headers", [])}
        received_at = _parse_date(headers.get("date"))

        attachments: list[Attachment] = []
        payload = raw.get("payload", {})
        _collect_attachments(
            payload,
            self._service,
            message_id,
            attachments,
        )
        body_text, body_html = _collect_body(payload)

        return GmailMessage(
            message_id=raw["id"],
            thread_id=raw.get("threadId", ""),
            sender=headers.get("from", ""),
            subject=headers.get("subject", ""),
            received_at=received_at,
            snippet=raw.get("snippet", ""),
            attachments=attachments,
            body_text=body_text,
            body_html=body_html,
        )


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _collect_attachments(part: dict, service, message_id: str, sink: list[Attachment]) -> None:
    filename = part.get("filename") or ""
    body = part.get("body") or {}
    if filename and (body.get("attachmentId") or body.get("data")):
        data = _load_attachment_data(service, message_id, body)
        if data:
            sink.append(
                Attachment(
                    filename=filename,
                    mime_type=part.get("mimeType", "application/octet-stream"),
                    data=data,
                    part_id=part.get("partId"),
                )
            )
    for sub in part.get("parts", []) or []:
        _collect_attachments(sub, service, message_id, sink)


def _collect_body(part: dict) -> tuple[str, str]:
    """Rekursivt extrahera text/plain + text/html från MIME-trädet.
    Returnerar (text, html). Saknade delar blir tomma strängar."""
    text = ""
    html = ""
    mime = (part.get("mimeType") or "").lower()
    body = part.get("body") or {}
    data = body.get("data")
    if data and not part.get("filename"):
        try:
            decoded = base64.urlsafe_b64decode(data.encode("utf-8")).decode(
                "utf-8", errors="replace"
            )
        except Exception:  # noqa: BLE001
            decoded = ""
        if decoded:
            if mime == "text/plain":
                text = decoded
            elif mime == "text/html":
                html = decoded
    for sub in part.get("parts", []) or []:
        sub_text, sub_html = _collect_body(sub)
        if sub_text and not text:
            text = sub_text
        elif sub_text:
            text = text + "\n" + sub_text
        if sub_html and not html:
            html = sub_html
        elif sub_html:
            html = html + "\n" + sub_html
    return text, html


def _load_attachment_data(service, message_id: str, body: dict) -> bytes | None:
    if body.get("data"):
        return base64.urlsafe_b64decode(body["data"].encode("utf-8"))
    attachment_id = body.get("attachmentId")
    if not attachment_id:
        return None
    raw = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data = raw.get("data")
    if not data:
        return None
    return base64.urlsafe_b64decode(data.encode("utf-8"))
