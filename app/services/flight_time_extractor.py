"""FAS 11.5.1 — extrahera flygtider + destinationsland från en resa.

Letar upp alla flyg-kategoriserade kvitton i en resa, frågar Claude om
strukturerade tider (avgång/ankomst, IATA-koder, land), sorterar
kronologiskt och bygger ett svar med:
  - departure_home_at  (avgång hemifrån = första outbound -1h)
  - return_home_at     (hemkomst = sista inbound +1h)
  - destination_country_suggestion
  - trip_route         ("Helsinki - Stockholm - Helsinki")

Vid Claude-fel returneras warnings i resultatet — ingen exception bubblar
upp så endpointen kan ge en användbar fallback (manuell input).

Pure-funktioner där det går; Claude-anrop är opt-in via dependency
injection så vi kan testa utan att slå mot riktig API.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ProcessedMessage, TripMessage

logger = logging.getLogger(__name__)


# Hur lång tid före outbound vi sätter "avgång hemifrån" (transfer + check-in)
HOME_TRANSFER_HOURS = 1


# IATA-kod → ISO 3166-1 alpha-2 land. Litet uppslag för säkerhet — Claude
# återger oftast korrekt land-kod direkt så detta är bara fallback.
IATA_COUNTRY_FALLBACK: dict[str, str] = {
    "HEL": "FI", "TKU": "FI", "TMP": "FI", "OUL": "FI", "RVN": "FI",
    "ARN": "SE", "GOT": "SE", "MMX": "SE", "BMA": "SE",
    "OSL": "NO", "BGO": "NO", "TRD": "NO", "SVG": "NO",
    "RIX": "LV",
    "CPH": "DK", "BLL": "DK",
    "TLL": "EE",
    "FRA": "DE", "MUC": "DE", "BER": "DE", "TXL": "DE",
    "AMS": "NL",
    "LHR": "GB", "LGW": "GB", "STN": "GB", "MAN": "GB",
    "CDG": "FR", "ORY": "FR",
    "JFK": "US", "EWR": "US", "LAX": "US", "ORD": "US",
}


def _strip_codefences(text: str) -> str:
    """Ta bort ev. ```json ... ``` runt Claude-svaret."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _parse_iso(raw: str | None) -> datetime | None:
    """Parsa ISO-8601 datetime. Returnerar None vid fel."""
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    # Python <3.11 hanterar inte 'Z'
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _country_from_iata(code: str | None) -> str | None:
    if not code:
        return None
    return IATA_COUNTRY_FALLBACK.get(code.strip().upper())


def _flight_messages_for_trip(
    trip_id: int, db: Session,
) -> list[ProcessedMessage]:
    """Returnerar aktiva (icke-borttagna) flyg-meddelanden i en resa."""
    rows = (
        db.query(ProcessedMessage)
        .join(TripMessage, TripMessage.message_id == ProcessedMessage.message_id)
        .filter(TripMessage.trip_id == trip_id)
        .filter(TripMessage.removed_at.is_(None))
        .filter(ProcessedMessage.deleted_at.is_(None))
        .all()
    )
    return [
        m for m in rows
        if m.category and "flyg" in m.category.lower()
    ]


def _build_extract_prompt(msg: ProcessedMessage) -> str:
    """Bygg prompt för Claude att extrahera flygdetaljer ur ett kvitto."""
    return (
        f"Extrahera flygdetaljer från detta kvitto.\n\n"
        f"Avsändare: {msg.sender or ''}\n"
        f"Ämne: {msg.subject or ''}\n"
        f"Vendor: {msg.vendor or ''}\n"
        f"Sammanfattning: {msg.summary or ''}\n"
        f"Datum: {msg.receipt_date or ''}\n\n"
        "Returnera giltig JSON enligt schemat — INGA codefences, INGA "
        "kommentarer. Om kvittot inte är ett flygkvitto, returnera null.\n\n"
        "Schema (eller bara: null):\n"
        "{\n"
        '  "departure_airport": "HEL",\n'
        '  "departure_city": "Helsinki",\n'
        '  "departure_country": "FI",\n'
        '  "departure_time": "2026-04-30T07:15:00+03:00",\n'
        '  "arrival_airport": "ARN",\n'
        '  "arrival_city": "Stockholm",\n'
        '  "arrival_country": "SE",\n'
        '  "arrival_time": "2026-04-30T07:55:00+02:00",\n'
        '  "is_outbound": true,\n'
        '  "booking_reference": "994ZHA"\n'
        "}\n\n"
        "Använd ISO 3166-1 alpha-2 för land. ISO 8601 med tidszon för tider."
    )


def _call_claude_for_flight(
    msg: ProcessedMessage,
    *,
    client=None,
) -> dict | None:
    """Anropa Claude för att extrahera flygdata från ett kvitto.

    Returnerar dict eller None (saknad API-key, parse-fel, eller Claude
    säger att det inte är ett flygkvitto)."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    if client is None:
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=settings.anthropic_api_key)
        except Exception:  # noqa: BLE001
            logger.exception("Kunde inte skapa Anthropic-client")
            return None

    prompt = _build_extract_prompt(msg)
    try:
        resp = client.messages.create(
            model=settings.claude_model,
            max_tokens=500,
            system=(
                "Du svarar ENDAST med giltig JSON eller bokstaven null. "
                "Inga kommentarer, ingen markdown."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Claude flight-extract API-fel")
        return None

    raw = "".join(
        block.text for block in resp.content
        if getattr(block, "type", "") == "text"
    )
    raw = _strip_codefences(raw)
    if not raw or raw.lower() == "null":
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        logger.warning("Kunde inte parsa Claude-flight-JSON: %r", raw[:200])
        return None
    if not isinstance(data, dict):
        return None
    return data


def _normalize_flight(raw: dict) -> dict | None:
    """Validera + normalisera ett extraktionssvar.

    Krav: departure_time + arrival_time måste kunna parsas. Annars None.
    Land-koder härleds från IATA-fallback om Claude inte gav dem.
    """
    dep_time = _parse_iso(raw.get("departure_time"))
    arr_time = _parse_iso(raw.get("arrival_time"))
    if dep_time is None or arr_time is None:
        return None

    dep_country = (raw.get("departure_country") or "").upper().strip() or None
    arr_country = (raw.get("arrival_country") or "").upper().strip() or None
    if dep_country is None:
        dep_country = _country_from_iata(raw.get("departure_airport"))
    if arr_country is None:
        arr_country = _country_from_iata(raw.get("arrival_airport"))

    return {
        "departure_airport": (raw.get("departure_airport") or "").upper() or None,
        "departure_city": raw.get("departure_city") or None,
        "departure_country": dep_country,
        "departure_time": dep_time,
        "arrival_airport": (raw.get("arrival_airport") or "").upper() or None,
        "arrival_city": raw.get("arrival_city") or None,
        "arrival_country": arr_country,
        "arrival_time": arr_time,
        "is_outbound": bool(raw.get("is_outbound", False)),
        "booking_reference": raw.get("booking_reference") or None,
    }


def _serialize_flight(f: dict) -> dict:
    """JSON-säkra varianten av ett flight (datetime → ISO)."""
    out = dict(f)
    if isinstance(out.get("departure_time"), datetime):
        out["departure_time"] = out["departure_time"].isoformat()
    if isinstance(out.get("arrival_time"), datetime):
        out["arrival_time"] = out["arrival_time"].isoformat()
    return out


def extract_flight_times_from_trip(
    trip,
    db: Session,
    *,
    extractor: Callable[[ProcessedMessage], dict | None] | None = None,
) -> dict[str, Any]:
    """Extrahera avgångs/ankomst-tider från flygkvitton i resan.

    Argument:
        trip: Trip-instans
        db: SQLAlchemy session
        extractor: optional fn(msg) → raw dict | None — för att injicera
                   testbar Claude-mock. Default = riktig Claude.

    Returnerar:
        {
            "departure_home_at": ISO,
            "return_home_at": ISO,
            "destination_country_suggestion": "SE",
            "trip_route": "Helsinki - Stockholm - Helsinki",
            "flights_extracted": [...],
            "warnings": [str, ...]
        }

    Vid problem returnerar dict med 'warnings' (alltid en lista) och
    delvist eller inget data — kallaren får hantera fallback.
    """
    warnings: list[str] = []
    flight_msgs = _flight_messages_for_trip(trip.id, db)

    if not flight_msgs:
        return {
            "warnings": ["Inga flygkvitton hittades i resan"],
            "flights_extracted": [],
        }

    if extractor is None:
        extractor = _call_claude_for_flight

    flights: list[dict] = []
    for msg in flight_msgs:
        try:
            raw = extractor(msg)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Flight-extraktion kraschade för message_id=%s", msg.message_id,
            )
            continue
        if not raw:
            continue
        norm = _normalize_flight(raw)
        if norm is None:
            warnings.append(
                f"Kunde inte tolka flygtider från {msg.subject or msg.message_id}"
            )
            continue
        norm["source_message_id"] = msg.message_id
        flights.append(norm)

    if not flights:
        return {
            "warnings": warnings + [
                "Kunde inte extrahera flygtider från några kvitton — "
                "lägg till manuellt"
            ],
            "flights_extracted": [],
        }

    # Sortera kronologiskt
    flights.sort(key=lambda f: f["departure_time"])

    # Outbound = första flyget från FI (annars första flyget)
    outbound = next(
        (f for f in flights if f.get("departure_country") == "FI"),
        flights[0],
    )

    # Inbound = sista flyget till FI (annars sista flyget)
    inbound = next(
        (f for f in reversed(flights) if f.get("arrival_country") == "FI"),
        flights[-1],
    )

    # Lägg till transfer-tid hemifrån/till hem
    departure_home = outbound["departure_time"] - timedelta(hours=HOME_TRANSFER_HOURS)
    return_home = inbound["arrival_time"] + timedelta(hours=HOME_TRANSFER_HOURS)

    # Bygg trip-route ("Helsinki - Stockholm - Helsinki")
    cities: list[str] = []
    for f in flights:
        dep_city = f.get("departure_city")
        arr_city = f.get("arrival_city")
        if dep_city and (not cities or cities[-1] != dep_city):
            cities.append(dep_city)
        if arr_city and (not cities or cities[-1] != arr_city):
            cities.append(arr_city)
    trip_route = " - ".join(cities) if cities else None

    # Föreslå destinationsland: outboundens ankomstland, eller första
    # icke-FI-landet i listan.
    destination_country = outbound.get("arrival_country")
    if destination_country == "FI" or not destination_country:
        for f in flights:
            for key in ("arrival_country", "departure_country"):
                cc = f.get(key)
                if cc and cc != "FI":
                    destination_country = cc
                    break
            if destination_country and destination_country != "FI":
                break

    return {
        "departure_home_at": departure_home.isoformat(),
        "return_home_at": return_home.isoformat(),
        "destination_country_suggestion": destination_country,
        "trip_route": trip_route,
        "flights_extracted": [_serialize_flight(f) for f in flights],
        "warnings": warnings,
    }
