"""Resor R1 — läs-klient för Google Calendar (primär kalender).

READ-ONLY: använder enbart scopen `calendar.readonly` och anropar bara
`events.list`. Inget skrivs till kalendern.

Autentisering: kalendern ligger på samma Google-konto som Gmail, så vi
återanvänder Gmail-kontots refresh-token (DB `oauth_tokens` har
företräde över env GMAIL_REFRESH_TOKEN, se oauth_token_store). Tokenen
måste ha fått `calendar.readonly` vid consent — annars svarar Google
`invalid_scope` och vi kastar `CalendarAuthError("consent_required")`.
Ny consent görs via "Återanslut Gmail" (/api/auth/gmail/start), som
sedan Resor R1 även begär kalender-scopen.

Viktigt: Gmail-klienten behåller sina egna scopes. Att lägga till
kalender-scopen där skulle få Gmail-refresh att faila (invalid_scope)
innan ny consent är gjord.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable

from app.config import get_settings
from app.services.oauth_token_store import get_refresh_token

logger = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.readonly"
SCOPES = [CALENDAR_SCOPE]

# Hämtfönster: 1 jan 2026 → idag + 90 dagar.
WINDOW_START = date(2026, 1, 1)
LOOKAHEAD_DAYS = 90
MAX_PAGES = 50  # 50 × 250 = 12 500 event — skydd mot oändlig paginering


class CalendarAuthError(RuntimeError):
    """Kalendern går inte att läsa pga auth/konfiguration.

    reason:
      'not_configured'   — OAuth-klient eller refresh-token saknas
      'consent_required' — tokenen saknar calendar.readonly (ny consent)
      'api_disabled'     — Calendar API ej aktiverat i Google Cloud-projektet
    """

    def __init__(self, reason: str, message: str = "") -> None:
        self.reason = reason
        super().__init__(message or reason)


def fetch_window(today: date | None = None) -> tuple[datetime, datetime]:
    """Returnera (time_min, time_max) i UTC för standardfönstret."""
    today = today or datetime.now(timezone.utc).date()
    start = datetime.combine(WINDOW_START, time.min, tzinfo=timezone.utc)
    end = datetime.combine(
        today + timedelta(days=LOOKAHEAD_DAYS + 1), time.min, tzinfo=timezone.utc,
    )
    return start, end


def _parse_event_time(raw: dict | None) -> tuple[datetime | date | None, bool]:
    """Google ger antingen {'dateTime': ISO} eller {'date': 'YYYY-MM-DD'}."""
    if not raw:
        return None, False
    if raw.get("dateTime"):
        value = raw["dateTime"].replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(value), False
        except ValueError:
            return None, False
    if raw.get("date"):
        try:
            return date.fromisoformat(raw["date"]), True
        except ValueError:
            return None, True
    return None, False


def normalize_event(raw: dict) -> dict | None:
    """Plattar ut ett Google-event till det format trip_detector läser.

    Returnerar None för inställda event eller event utan tid.
    """
    if raw.get("status") == "cancelled":
        return None
    start, all_day = _parse_event_time(raw.get("start"))
    end, _ = _parse_event_time(raw.get("end"))
    if start is None:
        return None
    if end is None:
        end = start
    organizer = (raw.get("organizer") or {}).get("email") or ""

    # C39 — deltagardata för mötesfiltret.
    self_response = None
    others = 0
    others_accepted = 0
    has_room = False
    for a in raw.get("attendees") or []:
        if a.get("self"):
            self_response = a.get("responseStatus")
            continue
        if a.get("resource") or str(a.get("email", "")).endswith(
            "resource.calendar.google.com"
        ):
            has_room = True
            continue
        status = a.get("responseStatus")
        if status == "declined":
            continue
        others += 1
        if status in ("accepted", "tentative") or a.get("organizer"):
            others_accepted += 1
    video = ((raw.get("conferenceData") or {}).get("videoEntryPoint") or {}).get("uri") or ""
    entry_points = (raw.get("conferenceData") or {}).get("entryPoints") or []
    uris = [video] + [str(e.get("uri") or "") for e in entry_points]
    is_stream = any("stream.meet.google.com" in u for u in uris)

    return {
        "id": raw.get("id") or "",
        "summary": raw.get("summary") or "",
        "description": raw.get("description") or "",
        "location": raw.get("location") or "",
        "start": start,
        "end": end,
        "all_day": all_day,
        "event_type": raw.get("eventType") or "default",
        "organizer": organizer,
        "transparency": raw.get("transparency") or "opaque",
        "visibility": raw.get("visibility") or "default",
        "self_response": self_response,
        "attendees_other": others,
        "attendees_other_accepted": others_accepted,
        "has_room": has_room,
        "is_stream": is_stream,
    }


class CalendarClient:
    """Tunn wrapper runt Calendar API v3 (endast läsning)."""

    def __init__(self, service: Any | None = None) -> None:
        # `service` kan injiceras i tester.
        self._service = service or self._build_service()

    @staticmethod
    def _build_service():
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        settings = get_settings()
        refresh_token = get_refresh_token("gmail")
        if not (settings.gmail_client_id and settings.gmail_client_secret and refresh_token):
            raise CalendarAuthError(
                "not_configured",
                "OAuth-klient eller refresh-token saknas för kalendern.",
            )
        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=SCOPES,
        )
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            msg = str(exc).lower()
            if "invalid_scope" in msg or "invalid_grant" in msg:
                raise CalendarAuthError(
                    "consent_required",
                    "Gmail-tokenen saknar calendar.readonly — gör ny consent "
                    "via Återanslut Gmail.",
                ) from exc
            raise
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def list_events(
        self,
        time_min: datetime,
        time_max: datetime,
        calendar_id: str = "primary",
    ) -> list[dict]:
        """Hämta alla (expanderade) event i fönstret, normaliserade."""
        from googleapiclient.errors import HttpError

        events: list[dict] = []
        page_token: str | None = None
        for _ in range(MAX_PAGES):
            try:
                resp = (
                    self._service.events()
                    .list(
                        calendarId=calendar_id,
                        timeMin=time_min.isoformat(),
                        timeMax=time_max.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=250,
                        pageToken=page_token,
                    )
                    .execute()
                )
            except HttpError as exc:
                status = getattr(getattr(exc, "resp", None), "status", None)
                text = str(exc)
                if status == 403 and (
                    "accessNotConfigured" in text or "has not been used" in text
                    or "SERVICE_DISABLED" in text
                ):
                    raise CalendarAuthError(
                        "api_disabled",
                        "Google Calendar API är inte aktiverat i Cloud-projektet.",
                    ) from exc
                if status in (401, 403):
                    raise CalendarAuthError("consent_required", text) from exc
                raise
            for raw in resp.get("items", []):
                ev = normalize_event(raw)
                if ev is not None:
                    events.append(ev)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        logger.info("Calendar: hämtade %d event (%s → %s)", len(events), time_min, time_max)
        return events


def fetch_events(
    today: date | None = None,
    client_factory: Callable[[], CalendarClient] = CalendarClient,
) -> list[dict]:
    """Hämta primärkalenderns event 1 jan 2026 → idag + 90 dagar."""
    time_min, time_max = fetch_window(today)
    return client_factory().list_events(time_min, time_max)
