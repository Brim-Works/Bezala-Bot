"""C49 Del 4 (fas R4-förberedelse) - Netvisor API-klient, AVSTÄNGD.

* Ingen knapp i UI och inget MCP-verktyg anropar `send_trip`. Den testas
  bara mot mockad HTTP.
* `NETVISOR_ENABLED` är false som standard. Då vägrar klienten starta.
* Saknas en inställning vägrar klienten starta och säger vilket NAMN som
  saknas - aldrig något värde.
* `send_trip` skickar alltid linestatus=open, sparar Netvisors id i
  `netvisor_reports` (created_api) och vägrar om resan redan har en aktiv
  rad (created_manual/created_api).

Autentisering enligt Netvisors "API authentication" (support.netvisor.fi,
artikel 766842): HMAC-SHA256 över
  URL&Sender&CustomerId&Timestamp&Language&OrganisationId&TransactionId&
  TimestampUnix&CustomerKey&PartnerKey
med nyckeln "CustomerKey&PartnerKey", hex i gemener, strängen kodad som
ISO-8859-15.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Testmiljön först. Byt till produktion (integration.netvisor.fi) först när
# Riikka godkänt testrapporterna.
DEFAULT_BASE_URL = "https://isvapi.netvisor.fi"
DEFAULT_SENDER = "Bezala Bot"
DEFAULT_LANGUAGE = "EN"

REQUIRED_ENV = (
    "NETVISOR_BASE_URL",
    "NETVISOR_PARTNER_ID",
    "NETVISOR_PARTNER_KEY",
    "NETVISOR_CUSTOMER_ID",
    "NETVISOR_CUSTOMER_KEY",
    "NETVISOR_ORGANISATION_ID",
    "NETVISOR_EMPLOYEE_IDENTIFIER",
)


class NetvisorConfigError(RuntimeError):
    """Klienten får inte starta (avstängd eller saknad inställning)."""


class NetvisorError(RuntimeError):
    """Netvisor svarade med fel eller resan får inte skickas."""


def is_enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("NETVISOR_ENABLED", "false")).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class NetvisorConfig:
    base_url: str
    partner_id: str
    partner_key: str
    customer_id: str
    customer_key: str
    organisation_id: str
    employee_identifier: str
    sender: str = DEFAULT_SENDER
    language: str = DEFAULT_LANGUAGE

    def __repr__(self) -> str:  # aldrig nycklar i loggar
        return f"NetvisorConfig(base_url={self.base_url!r}, sender={self.sender!r})"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "NetvisorConfig":
        env = os.environ if env is None else env
        if not is_enabled(env):
            raise NetvisorConfigError("Netvisor API is disabled (NETVISOR_ENABLED is false).")
        missing = [n for n in REQUIRED_ENV if not str(env.get(n, "")).strip()]
        if missing:
            raise NetvisorConfigError(
                "Netvisor API is not configured. Missing: " + ", ".join(missing) + "."
            )
        return cls(
            base_url=env["NETVISOR_BASE_URL"].strip().rstrip("/"),
            partner_id=env["NETVISOR_PARTNER_ID"].strip(),
            partner_key=env["NETVISOR_PARTNER_KEY"].strip(),
            customer_id=env["NETVISOR_CUSTOMER_ID"].strip(),
            customer_key=env["NETVISOR_CUSTOMER_KEY"].strip(),
            organisation_id=env["NETVISOR_ORGANISATION_ID"].strip(),
            employee_identifier=env["NETVISOR_EMPLOYEE_IDENTIFIER"].strip(),
            sender=str(env.get("NETVISOR_SENDER") or DEFAULT_SENDER).strip(),
            language=str(env.get("NETVISOR_LANGUAGE") or DEFAULT_LANGUAGE).strip().upper(),
        )


def _latin9(text: str) -> bytes:
    return text.encode("iso-8859-15", errors="replace")


def calculate_mac(
    url: str, sender: str, customer_id: str, timestamp: str, language: str,
    organisation_id: str, transaction_id: str, timestamp_unix: str,
    customer_key: str, partner_key: str,
) -> str:
    message = "&".join([
        url, sender, customer_id, timestamp, language, organisation_id,
        transaction_id, timestamp_unix, customer_key, partner_key,
    ])
    key = f"{customer_key}&{partner_key}"
    return hmac.new(_latin9(key), _latin9(message), hashlib.sha256).hexdigest()


class NetvisorClient:
    def __init__(
        self,
        config: NetvisorConfig,
        http: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
        new_transaction_id: Callable[[], str] | None = None,
    ):
        self.config = config
        self._http = http
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._txid = new_transaction_id or (lambda: str(uuid.uuid4()))

    @classmethod
    def from_env(cls, env: dict | None = None, **kwargs) -> "NetvisorClient":
        return cls(NetvisorConfig.from_env(env), **kwargs)

    # ---------- signering

    def headers(self, url: str) -> dict[str, str]:
        c = self.config
        now = self._now().astimezone(timezone.utc)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        unix = str(int(now.timestamp()))
        txid = self._txid()
        mac = calculate_mac(url, c.sender, c.customer_id, timestamp, c.language,
                            c.organisation_id, txid, unix, c.customer_key, c.partner_key)
        return {
            "X-Netvisor-Authentication-Sender": c.sender,
            "X-Netvisor-Authentication-CustomerId": c.customer_id,
            "X-Netvisor-Authentication-PartnerId": c.partner_id,
            "X-Netvisor-Authentication-Timestamp": timestamp,
            "X-Netvisor-Authentication-TimestampUnix": unix,
            "X-Netvisor-Authentication-TransactionId": txid,
            "X-Netvisor-Interface-Language": c.language,
            "X-Netvisor-Organisation-ID": c.organisation_id,
            "X-Netvisor-Authentication-MAC": mac,
            "X-Netvisor-Authentication-MACHashCalculationAlgorithm": "HMACSHA256",
            "X-Netvisor-Authentication-UseHTTPResponseStatusCodes": "1",
            "Content-Type": "text/xml; charset=utf-8",
        }

    def post(self, resource: str, xml: str) -> str:
        url = f"{self.config.base_url}/{resource}"
        http = self._http or httpx.Client(timeout=30)
        try:
            resp = http.post(url, content=xml.encode("utf-8"), headers=self.headers(url))
        except httpx.HTTPError as exc:
            raise NetvisorError(f"Netvisor request failed: {exc.__class__.__name__}") from exc
        finally:
            if self._http is None:
                http.close()
        if resp.status_code >= 400:
            raise NetvisorError(
                f"Netvisor answered HTTP {resp.status_code}: {_status_text(resp.text)}"
            )
        return resp.text

    # ---------- reseräkning

    def send_trip(self, db: Session, trip_id: int) -> dict:
        """Skapa reseräkningen i Netvisor (linestatus=open). Idempotent:
        vägrar om resan redan har en aktiv rad i netvisor_reports."""
        from app.models import CalendarTrip
        from app.services import netvisor_status
        from app.services.netvisor_payload import build_payload, to_xml, validate_xml

        trip = db.get(CalendarTrip, trip_id)
        if trip is None:
            raise NetvisorError(f"Trip {trip_id} not found.")
        if netvisor_status.is_created(db, trip_id):
            st = netvisor_status.status_of(db, trip_id)
            raise NetvisorError(
                f"Trip {trip_id} is already in Netvisor ({st['status']}, report "
                f"{st['netvisor_report_id']}). Not sent again."
            )
        if trip.status != "approved":
            raise NetvisorError(f"Trip {trip_id} is not approved ({trip.status}).")
        built = build_payload(db, trip_id, employee=self.config.employee_identifier)
        if built["warnings"]:
            raise NetvisorError("Trip has warnings: " + " ".join(built["warnings"]))
        lines = built["payload"]["tripexpense"]["dailycompensationlines"]
        if not lines:
            raise NetvisorError("No per diem lines to send.")
        if any(ln["linestatus"] != "open" for ln in lines):
            raise NetvisorError("linestatus must be open.")
        xml = to_xml(built["payload"])
        errors = validate_xml(xml)
        if errors:
            raise NetvisorError("Payload is invalid: " + " ".join(errors))
        body = self.post("tripexpense.nv", xml)
        report_id = parse_inserted_id(body)
        out = netvisor_status.set_status(
            db, trip_id, "created_api", report_id,
            note="Created via Netvisor API (linestatus open)", source="api",
        )
        logger.info("C49: resa %s skapad i Netvisor (%s)", trip_id, report_id)
        return out


def _status_text(body: str) -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return (body or "")[:200]
    status = root.find("ResponseStatus")
    if status is None:
        return (body or "")[:200]
    texts = [t.strip() for t in status.itertext() if t and t.strip()]
    return " ".join(texts)[:300]


def parse_inserted_id(body: str) -> str:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise NetvisorError("Netvisor answer is not XML.") from exc
    status = (root.findtext("ResponseStatus/Status") or "").strip()
    if status.upper() != "OK":
        raise NetvisorError(f"Netvisor answered: {_status_text(body)}")
    inserted = (root.findtext("Replies/InsertedDataIdentifier") or "").strip()
    if not inserted:
        raise NetvisorError("Netvisor answered OK but without InsertedDataIdentifier.")
    return inserted
