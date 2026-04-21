"""Bezala API-klient.

Autentisering: POST /auth/token med {email, password} → returnerar
access_token. Token cache:as in-memory med utgångstid och re-hämtas
automatiskt vid 401 eller när den löper ut.

Upload-flöde:
  1. upload_attachment(pdf_bytes, filename) → attachment_id
  2. create_transaction({attachment_ids, vendor, amount, ...}) → transaction_id

Retries: 2 försök vid 5xx med exponentiell backoff (1s, 2s).
Timeout: 30 sek per request.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 30.0
MAX_RETRIES_5XX = 2
TOKEN_SAFETY_MARGIN_SECONDS = 60


class BezalaError(RuntimeError):
    """Generic Bezala API error. Bär statuskod och serversvar där tillgängligt."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class BezalaAttachment:
    attachment_id: str


@dataclass
class BezalaTransaction:
    transaction_id: str
    url: str | None = None


class BezalaClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._email = settings.bezala_username
        self._password = settings.bezala_password
        self._base_url = settings.bezala_api_url.rstrip("/")
        self._client = httpx.Client(timeout=TIMEOUT_SECONDS)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

        if not (self._email and self._password):
            raise BezalaError(
                "BEZALA_USERNAME/BEZALA_PASSWORD saknas — kan inte autentisera mot Bezala."
            )

    # --------- auth ---------

    def _authenticate(self) -> str:
        url = f"{self._base_url}/auth/token"
        logger.info("Autentiserar mot Bezala (%s)", url)
        try:
            resp = self._client.post(
                url,
                json={"email": self._email, "password": self._password},
            )
        except httpx.HTTPError as exc:
            raise BezalaError(f"Bezala-auth: nätverksfel {exc}") from exc

        if resp.status_code >= 400:
            raise BezalaError(
                f"Bezala-auth: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text[:500],
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise BezalaError(f"Bezala-auth: icke-JSON svar ({exc})") from exc

        token = data.get("access_token") or data.get("token")
        if not token:
            raise BezalaError(f"Bezala-auth: saknar access_token i svar ({data})")

        expires_in = int(data.get("expires_in") or 3600)
        self._token = token
        self._token_expires_at = time.monotonic() + max(
            60, expires_in - TOKEN_SAFETY_MARGIN_SECONDS
        )
        logger.info("Bezala-token mottagen (giltig ~%ds)", expires_in)
        return token

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        return self._authenticate()

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}"}

    # --------- requests med retry + 401-refresh ---------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        files: Any | None = None,
        data: Any | None = None,
    ) -> httpx.Response:
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(MAX_RETRIES_5XX + 1):
            try:
                headers = self._auth_headers()
                resp = self._client.request(
                    method, url, json=json, files=files, data=data, headers=headers
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "Bezala %s %s: nätverksfel (försök %d): %s",
                    method, path, attempt + 1, exc,
                )
                if attempt >= MAX_RETRIES_5XX:
                    raise BezalaError(f"Bezala {method} {path}: {exc}") from exc
                time.sleep(2 ** attempt)
                continue

            if resp.status_code == 401:
                # Token kan vara utgången — tvinga ny auth EN gång.
                if attempt == 0:
                    logger.info("Bezala 401 — hämtar ny token och försöker igen.")
                    self._token = None
                    self._token_expires_at = 0.0
                    continue
                raise BezalaError(
                    f"Bezala {method} {path}: 401 efter re-auth",
                    status_code=401,
                    body=resp.text[:500],
                )

            if 500 <= resp.status_code < 600 and attempt < MAX_RETRIES_5XX:
                logger.warning(
                    "Bezala %s %s: %d (försök %d, backoff)",
                    method, path, resp.status_code, attempt + 1,
                )
                time.sleep(2 ** attempt)
                continue

            return resp

        # borde inte nås
        raise BezalaError(
            f"Bezala {method} {path}: slut på försök ({last_exc})"
        )

    # --------- public API ---------

    def upload_attachment(self, filename: str, pdf_bytes: bytes) -> BezalaAttachment:
        resp = self._request(
            "POST",
            "/attachments",
            files={"file": (filename, pdf_bytes, "application/pdf")},
        )
        if resp.status_code >= 400:
            raise BezalaError(
                f"Bezala upload_attachment: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text[:500],
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise BezalaError(
                f"Bezala upload_attachment: icke-JSON svar ({exc})"
            ) from exc

        attachment_id = (
            data.get("id")
            or data.get("attachment_id")
            or (data.get("attachment") or {}).get("id")
        )
        if not attachment_id:
            raise BezalaError(
                f"Bezala upload_attachment: saknar id i svar ({data})"
            )
        logger.info("Bezala: laddade upp %s som attachment_id=%s", filename, attachment_id)
        return BezalaAttachment(attachment_id=str(attachment_id))

    def create_transaction(
        self,
        *,
        attachment_ids: list[str],
        vendor: str | None,
        amount: float | None,
        currency: str | None,
        date: str | None,
        category: str | None,
        description: str | None,
    ) -> BezalaTransaction:
        payload: dict[str, Any] = {
            "attachment_ids": attachment_ids,
        }
        if vendor:
            payload["vendor"] = vendor
        if amount is not None:
            payload["amount"] = amount
        if currency:
            payload["currency"] = currency
        if date:
            payload["date"] = date
        if category:
            payload["category"] = category
        if description:
            payload["description"] = description

        resp = self._request("POST", "/transactions", json=payload)
        if resp.status_code >= 400:
            raise BezalaError(
                f"Bezala create_transaction: {resp.status_code}",
                status_code=resp.status_code,
                body=resp.text[:500],
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise BezalaError(
                f"Bezala create_transaction: icke-JSON svar ({exc})"
            ) from exc

        transaction_id = (
            data.get("id")
            or data.get("transaction_id")
            or (data.get("transaction") or {}).get("id")
        )
        if not transaction_id:
            raise BezalaError(
                f"Bezala create_transaction: saknar id i svar ({data})"
            )
        url = data.get("url") or data.get("web_url")
        logger.info(
            "Bezala: skapade transaction_id=%s med %d bilagor",
            transaction_id, len(attachment_ids),
        )
        return BezalaTransaction(transaction_id=str(transaction_id), url=url)

    def close(self) -> None:
        self._client.close()
