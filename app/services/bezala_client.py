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
import os
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

# Multipart-nyckeln där Bezala förväntar sig filen. Default top-level "file"
# eftersom Bezalas controller tycks göra params[:file].tempfile (nested
# 'attachment[file]' gav "undefined method `tempfile' for nil:NilClass" i
# produktion). Overridable via env om vi felar igen och måste experimentera.
FILE_FIELD_NAME = os.environ.get("BEZALA_FILE_FIELD_NAME", "file")

# Escape-hatches om Bezala 500:ar på fält som inte ingår i strong_params.
# Sätt env BEZALA_INCLUDE_VENDOR=false eller BEZALA_INCLUDE_VAT_LINES=false
# i Railway för att utesluta fältet från /transactions-payloaden utan
# code-deploy. Default är båda inkluderade.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


INCLUDE_VENDOR = _env_bool("BEZALA_INCLUDE_VENDOR", True)
INCLUDE_VAT_LINES = _env_bool("BEZALA_INCLUDE_VAT_LINES", True)

# Hur metadata-fält namnsätts i multipart-requesten:
#   "flat"   → description, date, amount, ...       (default — Alternativ A)
#   "nested" → attachment[description], attachment[date], ...
# Live-test visar att flat format passar Bezalas controller — Rails strong
# params kan tolka båda, men blandningen "file top-level + metadata nested"
# gav 422 "fields empty" trots att värdena skickades. Switch om det behövs
# via env utan redeploy.
FIELD_NAMING = os.environ.get("BEZALA_FIELD_NAMING", "flat").lower()


def _field_key(name: str) -> str:
    """Returnera rätt fältnamn baserat på FIELD_NAMING."""
    if FIELD_NAMING == "nested":
        return f"attachment[{name}]"
    return name


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
        description: str,
        date: str,
        credit_account_id: int | str | None = None,
        vat_lines_attributes: list[dict] | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> BezalaTransaction:
        """Skapa en transaktion i Bezala via POST /api/transactions med
        nested JSON-body enligt API-docs:

          {"transaction": {
            "description": "...",
            "date": "YYYY-MM-DD",
            "credit_account_id": 67100,
            "vat_lines_attributes": [{...}]
          }}

        amount/currency/vendor/cost_center_id finns INTE längre top-level
        — allt ekonomiskt innehåll ligger inuti vat_lines_attributes[]."""
        if not description:
            raise BezalaError("create_transaction: description saknas")
        if not date:
            raise BezalaError("create_transaction: date saknas (ÅÅÅÅ-MM-DD)")

        payload: dict[str, Any] = {
            "description": description,
            "date": date,
        }
        if credit_account_id is not None:
            payload["credit_account_id"] = credit_account_id
        if vat_lines_attributes:
            payload["vat_lines_attributes"] = vat_lines_attributes
        if extra_fields:
            for k, v in extra_fields.items():
                if v is not None:
                    payload[k] = v

        # Rails-konvention: params.require(:transaction).permit(...) kräver
        # att body:n wrappas i {"transaction": {...}}. Flat JSON gav 500
        # (NoMethodError i controllern) i live-test.
        wrapped = {"transaction": payload}

        logger.info(
            "create_transaction: POST /transactions body=%s",
            json.dumps(wrapped, ensure_ascii=False, default=str),
        )
        resp = self._request("POST", "/transactions", json=wrapped)
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
            "Bezala: skapade transaction_id=%s description=%r",
            transaction_id, description,
        )
        return BezalaTransaction(transaction_id=str(transaction_id), url=url)

    def attach_file(
        self,
        transaction_id: str,
        filename: str,
        pdf_bytes: bytes,
    ) -> BezalaAttachment:
        """Bifoga en PDF-fil till en befintlig transaktion via
        POST /api/attachments (Steg 2 av upload_receipt-flödet)."""
        if not transaction_id:
            raise BezalaError("attach_file: transaction_id saknas")
        if not filename:
            raise BezalaError("attach_file: filename saknas")
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            raise BezalaError("attach_file: pdf_bytes är inte en giltig PDF")

        logger.info(
            "attach_file: POST /attachments transaction_id=%s filename=%r bytes=%d",
            transaction_id, filename, len(pdf_bytes),
        )
        resp = self._request(
            "POST",
            "/attachments",
            files={"file": (filename, pdf_bytes, "application/pdf")},
            data={"transaction_id": str(transaction_id)},
        )
        if resp.status_code >= 400:
            raise BezalaError(
                f"Bezala attach_file: {resp.status_code}",
                status_code=resp.status_code,
                body=_safe_body_snippet(resp),
            )
        try:
            data = resp.json()
        except ValueError:
            # Vissa Rails-endpoints returnerar 204/tom body vid success
            data = {}

        attachment_id = (
            data.get("id")
            or data.get("attachment_id")
            or (data.get("attachment") or {}).get("id")
            or transaction_id  # fallback: använd transaction_id som referens
        )
        logger.info(
            "Bezala: bifogade %s till transaction_id=%s (attachment_id=%s)",
            filename, transaction_id, attachment_id,
        )
        return BezalaAttachment(attachment_id=str(attachment_id))

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

    # --------- receipt upload — TWO-STEP ---------
    #
    # Bezala API-docs (live-verifierad): POST /api/transactions för metadata,
    # sedan POST /api/attachments med filen + transaction_id. /receipts
    # existerar inte. Metadata + fil i samma request (tidigare försök)
    # fungerar inte.

    def upload_receipt(
        self,
        *,
        filename: str,
        pdf_bytes: bytes,
        description: str,
        date: str,
        credit_account_id: int | str | None = None,
        vat_lines_attributes: list[dict] | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> BezalaAttachment:
        """Ladda upp ett kvitto till Bezala via TWO-STEP-flödet:

          Steg 1: POST /transactions (JSON, {"transaction": {...}}) → tx_id
          Steg 2: POST /attachments (multipart file + transaction_id)

        Om steg 1 lyckas men steg 2 misslyckas: transaction_id loggas
        som ORPHAN och BezalaError propageras. Kvittot finns då i Bezala
        utan bifogad PDF och måste rensas eller bifogas manuellt."""
        if not filename:
            raise BezalaError("upload_receipt: filename saknas")
        if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
            raise BezalaError("upload_receipt: pdf_bytes är inte en giltig PDF")
        if not description:
            raise BezalaError("upload_receipt: description saknas")
        if not date:
            raise BezalaError("upload_receipt: date saknas (ÅÅÅÅ-MM-DD)")

        logger.info(
            "upload_receipt: two-step filename=%r bytes=%d description=%r "
            "date=%r credit_account_id=%s vat_lines_attributes_count=%d",
            filename, len(pdf_bytes), description, date,
            credit_account_id, len(vat_lines_attributes or []),
        )

        # Steg 1: skapa transaktionen
        transaction = self.create_transaction(
            description=description,
            date=date,
            credit_account_id=credit_account_id,
            vat_lines_attributes=vat_lines_attributes,
            extra_fields=extra_fields,
        )
        transaction_id = transaction.transaction_id

        # Steg 2: bifoga filen
        try:
            attachment = self.attach_file(transaction_id, filename, pdf_bytes)
        except BezalaError as exc:
            # ORPHAN: transaktionen finns i Bezala utan PDF. Logga tydligt
            # så ansvarig kan städa manuellt eller bifoga filen via API:et.
            logger.error(
                "Bezala ORPHAN transaction: tx_id=%s (skapad men fil-bifogning "
                "misslyckades: %s | body=%s)",
                transaction_id, exc, exc.body,
            )
            raise BezalaError(
                f"Transaktion {transaction_id} skapad men fil-bifogning "
                f"misslyckades: {exc}",
                status_code=exc.status_code,
                body=exc.body,
            ) from exc

        logger.info(
            "Bezala: two-step klart — transaction_id=%s attachment_id=%s",
            transaction_id, attachment.attachment_id,
        )
        # Returnera transaction_id som "attachment_id" — det är ID:t som
        # lagras i ProcessedMessage.bezala_transaction_id och används för
        # deep-link till Bezala-UI.
        return BezalaAttachment(attachment_id=transaction_id)

    def close(self) -> None:
        self._client.close()
