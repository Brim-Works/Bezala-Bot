"""Bezala API-klient.

Autentisering: POST /auth/token med {email, password} → returnerar
access_token. Token cache:as in-memory med utgångstid och re-hämtas
automatiskt vid 401 eller när den löper ut.

Upload-flöde:
  1. upload_attachment(pdf_bytes, filename) → attachment_id
  2. create_transaction({attachment_ids, vendor, amount, ...}) → transaction_id

Metadata-endpoints (Gate 0-groundwork):
  - list_accounts() → Bezala kontolista (för account_id-mappning)
  - list_cost_centers() → kostnadsställen (för cost_center_id)
  - list_vat_rates() → momssatser (för vat_rate_id)

Retries: 2 försök vid 5xx med exponentiell backoff (1s, 2s).
Timeout: 30 sek per request.

LOGGNING: På varje request loggar vi metod, path och payload-nycklar (inte
värden — de kan innehålla PII). På varje non-2xx loggar vi statuskod +
hela response.text (trunkerat till BODY_LOG_LIMIT). Vid 422 höjer vi
loglevel till ERROR så felet inte drunknar i INFO-strömmen.
"""

from __future__ import annotations

import json
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
# Trunkeringsgräns för loggade/felrapporterade response-bodies. Höjd från
# 500 → 4000 så vi ser hela Bezala-felmeddelandet (viktigt vid 422).
BODY_LOG_LIMIT = 4000


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


def _safe_body_snippet(resp: httpx.Response) -> str:
    """Returnerar response.text trunkerad till BODY_LOG_LIMIT tecken.
    Skyddar mot None/binär respons."""
    try:
        text = resp.text or ""
    except Exception:  # noqa: BLE001
        return "<binärt svar — text() kastade>"
    if len(text) > BODY_LOG_LIMIT:
        return text[:BODY_LOG_LIMIT] + f"…<{len(text) - BODY_LOG_LIMIT} tecken kapade>"
    return text


def _log_response(resp: httpx.Response, method: str, path: str, payload_keys: list[str] | None = None) -> None:
    """Strukturerad loggning av Bezala-svar. 4xx/5xx → ERROR, 2xx → DEBUG.
    Payload-nycklar loggas (inte värdena — de kan bära PII)."""
    status = resp.status_code
    keys_str = ",".join(payload_keys) if payload_keys else "—"
    headers_interesting = {
        k: v for k, v in resp.headers.items()
        if k.lower() in ("content-type", "x-request-id", "x-correlation-id", "retry-after")
    }
    if 200 <= status < 300:
        logger.debug(
            "Bezala %s %s → %d | payload_keys=%s | headers=%s",
            method, path, status, keys_str, headers_interesting,
        )
        return
    body = _safe_body_snippet(resp)
    level = logging.ERROR if status in (400, 401, 403, 422, 500, 502, 503) else logging.WARNING
    logger.log(
        level,
        "Bezala %s %s → %d | payload_keys=%s | headers=%s | body=%s",
        method, path, status, keys_str, headers_interesting, body,
    )


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

        # Payload-nycklar för loggning (inte värden — kan bära PII/belopp).
        payload_keys: list[str] = []
        if isinstance(json, dict):
            payload_keys = list(json.keys())
        elif isinstance(data, dict):
            payload_keys = list(data.keys())
        elif files:
            payload_keys = [f"file:{k}" for k in (files.keys() if isinstance(files, dict) else [])]

        for attempt in range(MAX_RETRIES_5XX + 1):
            try:
                headers = self._auth_headers()
                logger.debug(
                    "Bezala → %s %s (försök %d, payload_keys=%s)",
                    method, path, attempt + 1, payload_keys,
                )
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

            _log_response(resp, method, path, payload_keys)

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
                    body=_safe_body_snippet(resp),
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
                body=_safe_body_snippet(resp),
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
        extra_fields: dict[str, Any] | None = None,
    ) -> BezalaTransaction:
        """Skapar en transaktion i Bezala.

        `extra_fields` är Gate 0-groundwork: låter anroparen lager in
        account_id, cost_center_id, vat_rate_id, purchase_date m.fl. när
        vi vet exakt vilka nycklar Bezala förväntar (fastställs när live-
        response.text från 422 analyserats)."""
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
        if extra_fields:
            for k, v in extra_fields.items():
                if v is not None:
                    payload[k] = v

        resp = self._request("POST", "/transactions", json=payload)
        if resp.status_code >= 400:
            raise BezalaError(
                f"Bezala create_transaction: {resp.status_code}",
                status_code=resp.status_code,
                body=_safe_body_snippet(resp),
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

    # --------- Gate 0 groundwork: metadata-endpoints ---------
    #
    # Dessa läser referensdata från Bezala (konton, kostnadsställen,
    # momssatser) som behövs för att bygga rätt transaction-payload.
    # De används INTE av create_transaction än — först nästa iteration
    # när vi vet exakta fältnamn från Bezala 422-respons.

    def _fetch_list(self, path: str, *, item_keys: tuple[str, ...] = ("items", "data", "results")) -> list[dict]:
        """Generisk GET → list[dict]. Hanterar både toppnivå-lista och
        wrapper-objekt {"items": [...]} / {"data": [...]} / {"results": [...]}.
        Höjer BezalaError på non-2xx."""
        resp = self._request("GET", path)
        if resp.status_code >= 400:
            raise BezalaError(
                f"Bezala GET {path}: {resp.status_code}",
                status_code=resp.status_code,
                body=_safe_body_snippet(resp),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise BezalaError(
                f"Bezala GET {path}: icke-JSON svar ({exc})"
            ) from exc

        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in item_keys:
                if isinstance(data.get(key), list):
                    return [item for item in data[key] if isinstance(item, dict)]
        logger.warning("Bezala GET %s: oväntad struktur (%s)", path, type(data).__name__)
        return []

    def list_accounts(self) -> list[dict]:
        """Hämtar Bezala-konton. Varje post har typiskt {id, name, code?}."""
        return self._fetch_list("/accounts")

    def list_cost_centers(self) -> list[dict]:
        """Hämtar kostnadsställen. Varje post har typiskt {id, name, default?}."""
        return self._fetch_list("/cost_centers")

    def list_vat_rates(self) -> list[dict]:
        """Hämtar momssatser. Bezala-endpointen kan heta /vat_rates eller
        /vat_codes — vi försöker den gängse varianten först och returnerar
        tom lista om inget finns (logger varnar)."""
        try:
            return self._fetch_list("/vat_rates")
        except BezalaError as exc:
            if exc.status_code == 404:
                logger.info(
                    "Bezala /vat_rates saknas (%s) — försöker /vat_codes som fallback",
                    exc.status_code,
                )
                try:
                    return self._fetch_list("/vat_codes")
                except BezalaError:
                    logger.warning("Bezala har varken /vat_rates eller /vat_codes — returnerar tom lista.")
                    return []
            raise

    # --------- receipt upload (Gate 0 fix) ---------
    #
    # Bezalas /attachments-endpoint är en RECEIPT-POST (Rails-baserad).
    # 422-responsen "description/date/vat_lines kan inte vara tom" trots
    # att vi skickar fälten bekräftar Rails strong-params-beteende:
    # params.require(:attachment).permit(:description, ...). Top-level-
    # fält ignoreras → vi måste nesta under attachment[...].

    def upload_receipt(
        self,
        *,
        filename: str,
        pdf_bytes: bytes,
        description: str,
        date: str,
        amount: float | None,
        currency: str | None,
        vat_lines: list[dict] | None = None,
        account_id: int | str | None = None,
        cost_center_id: int | str | None = None,
        vendor: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> BezalaAttachment:
        """Ladda upp ett kvitto till Bezala i en enda multipart-request.

        Form-fält namnges enligt Rails strong-params-konvention:
          attachment[file], attachment[description], attachment[date], ...
          attachment[vat_lines] = JSON-sträng "[{amount, vat_code_id}]"

        Returnerar BezalaAttachment med id:t som Bezala genererade."""
        if not filename:
            raise BezalaError("upload_receipt: filename saknas")
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            raise BezalaError("upload_receipt: pdf_bytes är inte en giltig PDF")
        if not description:
            raise BezalaError("upload_receipt: description saknas")
        if not date:
            raise BezalaError("upload_receipt: date saknas (ÅÅÅÅ-MM-DD)")

        # Synlig INFO-logg av värdena som faktiskt skickas — debug 422:or
        # utan att behöva gissa.
        logger.info(
            "Bezala upload_receipt payload: description=%r date=%r amount=%r "
            "currency=%r account_id=%r cost_center_id=%r vat_lines=%s",
            description, date, amount, currency, account_id, cost_center_id,
            vat_lines,
        )

        form: dict[str, Any] = {
            "attachment[description]": description,
            "attachment[date]": date,
        }
        if amount is not None:
            form["attachment[amount]"] = str(amount)
        if currency:
            form["attachment[currency]"] = currency
        if vendor:
            form["attachment[vendor]"] = vendor
        if account_id is not None:
            form["attachment[account_id]"] = str(account_id)
        if cost_center_id is not None:
            form["attachment[cost_center_id]"] = str(cost_center_id)
        if vat_lines:
            # JSON-sträng under nested-nyckeln — Bezala parser den.
            form["attachment[vat_lines]"] = json.dumps(vat_lines, ensure_ascii=False)
        if extra_fields:
            for k, v in extra_fields.items():
                if v is None:
                    continue
                # Respekterar redan pre-fixade nycklar; annars nesta under attachment
                key = k if k.startswith("attachment[") else f"attachment[{k}]"
                form[key] = v if isinstance(v, str) else json.dumps(v)

        resp = self._request(
            "POST",
            "/attachments",
            files={"attachment[file]": (filename, pdf_bytes, "application/pdf")},
            data=form,
        )
        if resp.status_code >= 400:
            raise BezalaError(
                f"Bezala upload_receipt: {resp.status_code}",
                status_code=resp.status_code,
                body=_safe_body_snippet(resp),
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise BezalaError(
                f"Bezala upload_receipt: icke-JSON svar ({exc})"
            ) from exc

        receipt_id = (
            data.get("id")
            or data.get("attachment_id")
            or (data.get("attachment") or {}).get("id")
            or (data.get("receipt") or {}).get("id")
        )
        if not receipt_id:
            raise BezalaError(
                f"Bezala upload_receipt: saknar id i svar ({data})"
            )
        logger.info(
            "Bezala: laddade upp kvitto %s som id=%s (account_id=%s, vat_lines=%d)",
            filename, receipt_id, account_id, len(vat_lines or []),
        )
        return BezalaAttachment(attachment_id=str(receipt_id))

    def close(self) -> None:
        self._client.close()
