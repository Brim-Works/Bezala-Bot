"""Bezala API-klient för att skicka kvitton."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class BezalaSubmitResult:
    ok: bool
    status_code: int
    body: str


class BezalaClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._username = settings.bezala_username
        self._password = settings.bezala_password
        self._base_url = settings.bezala_api_url.rstrip("/")
        self._client = httpx.Client(timeout=30.0)
        self._token: str | None = None

    def _auth_headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def login(self) -> str:
        if not (self._username and self._password):
            raise RuntimeError("BEZALA_USERNAME/BEZALA_PASSWORD saknas.")
        resp = self._client.post(
            f"{self._base_url}/auth/login",
            json={"username": self._username, "password": self._password},
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise RuntimeError(f"Bezala-login gav inget token: {data}")
        self._token = token
        return token

    def submit_receipt(self, filename: str, data: bytes) -> BezalaSubmitResult:
        if not self._token:
            self.login()
        resp = self._client.post(
            f"{self._base_url}/receipts",
            headers=self._auth_headers(),
            files={"file": (filename, data, "application/pdf")},
        )
        ok = 200 <= resp.status_code < 300
        if not ok:
            logger.warning("Bezala-svar %s: %s", resp.status_code, resp.text[:500])
        return BezalaSubmitResult(ok=ok, status_code=resp.status_code, body=resp.text)

    def close(self) -> None:
        self._client.close()
