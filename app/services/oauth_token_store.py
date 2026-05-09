"""Persistenta OAuth-tokens för Gmail och Drive.

Tokens lagras i `oauth_tokens`-tabellen och har företräde över
miljövariablerna GMAIL_REFRESH_TOKEN / DRIVE_REFRESH_TOKEN. Detta gör
att tokens överlever Railway-redeploys utan att vi behöver köra om
generate_token.py och uppdatera env-variabler för hand.

Vid `invalid_grant` (refresh-token utgången/återkallad) sätts
`gmail_auth_required` / `drive_auth_required` på AppSettings, och UI
visar en banner med "Återanslut Gmail"-knapp.
"""

from __future__ import annotations

import logging
from typing import Literal

from app.config import get_settings
from app.db import session_scope
from app.models import AppSettings, OAuthToken
from app.services.settings_service import load_settings

logger = logging.getLogger(__name__)

Service = Literal["gmail", "drive"]
SERVICES: tuple[Service, ...] = ("gmail", "drive")

_FLAG_FIELD = {
    "gmail": "gmail_auth_required",
    "drive": "drive_auth_required",
}


def _env_refresh_token(service: Service) -> str:
    settings = get_settings()
    if service == "gmail":
        return settings.gmail_refresh_token or ""
    return settings.drive_refresh_token or ""


def get_refresh_token(service: Service) -> str:
    """Returnera refresh-token för `service`.

    DB har företräde — om det finns en rad i oauth_tokens används den.
    Annars faller vi tillbaka på env-variabeln (legacy/seed-värdet).
    Tom sträng om varken DB eller env har en token.
    """
    if service not in SERVICES:
        raise ValueError(f"okänd service: {service!r}")
    try:
        with session_scope() as db:
            row = db.query(OAuthToken).filter(OAuthToken.service == service).first()
            if row and isinstance(row.token_data, dict):
                token = (row.token_data.get("refresh_token") or "").strip()
                if token:
                    return token
    except Exception:  # noqa: BLE001
        logger.exception("Kunde inte läsa oauth_tokens-rad för %s", service)
    return _env_refresh_token(service)


def save_refresh_token(
    service: Service,
    refresh_token: str,
    *,
    extra: dict | None = None,
) -> None:
    """Spara/uppdatera refresh-token för `service` i DB."""
    if service not in SERVICES:
        raise ValueError(f"okänd service: {service!r}")
    if not refresh_token or not refresh_token.strip():
        raise ValueError("refresh_token är tomt")

    payload: dict = {"refresh_token": refresh_token.strip()}
    if extra:
        payload.update({k: v for k, v in extra.items() if k != "refresh_token"})

    with session_scope() as db:
        row = db.query(OAuthToken).filter(OAuthToken.service == service).first()
        if row is None:
            row = OAuthToken(service=service, token_data=payload)
            db.add(row)
        else:
            row.token_data = payload
        # Token är ny → rensa eventuell auth_required-flagga.
        settings_row = load_settings(db)
        setattr(settings_row, _FLAG_FIELD[service], False)
    logger.info("Sparade ny OAuth refresh_token för %s", service)


def set_auth_required(service: Service, value: bool = True) -> None:
    """Markera att tokenen är trasig (eller fungerande igen).

    Best-effort: misslyckas tyst om DB är otillgänglig — vi vill aldrig
    krascha en pågående request bara för att sätta en UI-flagga.
    """
    if service not in SERVICES:
        return
    field = _FLAG_FIELD[service]
    try:
        with session_scope() as db:
            row = load_settings(db)
            current = getattr(row, field, False)
            if bool(current) != bool(value):
                setattr(row, field, bool(value))
                logger.info("AppSettings.%s = %s", field, value)
    except Exception:  # noqa: BLE001
        logger.exception("Kunde inte uppdatera %s", field)


def get_auth_required(service: Service) -> bool:
    if service not in SERVICES:
        return False
    field = _FLAG_FIELD[service]
    try:
        with session_scope() as db:
            row = load_settings(db)
            return bool(getattr(row, field, False))
    except Exception:  # noqa: BLE001
        logger.exception("Kunde inte läsa %s", field)
        return False


class OAuthAuthError(RuntimeError):
    """Refresh-token är ogiltig (invalid_grant) — användaren måste återansluta."""

    def __init__(self, service: Service, message: str = "") -> None:
        self.service = service
        super().__init__(message or f"{service}-OAuth kräver återanslutning")


def is_invalid_grant(exc: BaseException) -> bool:
    """Heuristik för att detektera invalid_grant från google.auth."""
    msg = str(exc) or ""
    return "invalid_grant" in msg.lower()
