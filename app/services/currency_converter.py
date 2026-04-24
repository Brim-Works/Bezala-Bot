"""Växelkurser från frankfurter.dev (ECB-data) med DB-cache.

Används av kortmatchningen för att jämföra kvitto-belopp (SEK) mot
Bezala-kortdebitering (EUR). Historiska kurser ändras inte → vi cachar
för alltid i currency_rates-tabellen. frankfurter.dev är öppen och
gratis, ingen API-nyckel behövs.

Docs: https://frankfurter.dev/
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CurrencyRate

logger = logging.getLogger(__name__)

FRANKFURTER_URL = "https://api.frankfurter.dev"
FETCH_TIMEOUT_SECONDS = 5.0


class RateProvider(Protocol):
    """(date, from, to) → rate eller None. Implementeras av
    get_rate_via_db nedan eller via en dict-stub i tester."""

    def __call__(self, date_str: str, from_currency: str, to_currency: str) -> float | None: ...


def _normalize_currency(c: str | None) -> str:
    return (c or "").strip().upper()


def _fetch_rate_from_api(
    date_str: str, from_currency: str, to_currency: str,
) -> float | None:
    """Hämtar kurs från frankfurter.dev. Returnerar None vid nätverksfel,
    okänd valuta, eller datum i framtiden. follow_redirects=True så att
    eventuella URL-flyttar inte tystar oss med 301."""
    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT_SECONDS, follow_redirects=True,
        ) as client:
            resp = client.get(
                f"{FRANKFURTER_URL}/{date_str}",
                params={"from": from_currency, "to": to_currency},
            )
    except Exception as exc:  # noqa: BLE001 — nätverk, DNS, timeout
        logger.info(
            "Currency: fetch %s %s→%s misslyckades: %s",
            date_str, from_currency, to_currency, exc,
        )
        return None

    if resp.status_code != 200:
        logger.info(
            "Currency: frankfurter %s %s→%s svarade %d",
            date_str, from_currency, to_currency, resp.status_code,
        )
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    rate = (data.get("rates") or {}).get(to_currency)
    if rate is None:
        return None
    try:
        return float(rate)
    except (TypeError, ValueError):
        return None


def get_rate(
    date_str: str,
    from_currency: str,
    to_currency: str,
    *,
    db: Session,
) -> float | None:
    """Returnera växelkurs mellan from/to på given historisk datum (ISO
    YYYY-MM-DD). DB-cache först — fallback till frankfurter-API, cacha
    sedan resultatet. Returnerar None vid okänd valuta, nätverksfel
    eller saknad data."""
    from_c = _normalize_currency(from_currency)
    to_c = _normalize_currency(to_currency)
    if not from_c or not to_c or not date_str:
        return None
    if from_c == to_c:
        return 1.0

    cached = (
        db.query(CurrencyRate)
        .filter_by(date=date_str, from_currency=from_c, to_currency=to_c)
        .first()
    )
    if cached is not None:
        return float(cached.rate)

    rate = _fetch_rate_from_api(date_str, from_c, to_c)
    if rate is None:
        return None

    try:
        db.add(CurrencyRate(
            date=date_str, from_currency=from_c,
            to_currency=to_c, rate=float(rate),
        ))
        db.commit()
    except IntegrityError:
        # Race: en parallel request cachat samma kurs — ignorera
        db.rollback()
    except Exception:  # noqa: BLE001
        logger.exception("Currency: kunde inte cacha %s %s→%s",
                         date_str, from_c, to_c)
        db.rollback()

    return rate


def make_db_rate_provider(db: Session) -> RateProvider:
    """Skapa en rate_provider-funktion stängd runt en db-session.
    Används av match-suggestions-endpointen som vill ge matcher:n
    pure-function-callability utan att matchern behöver känna till db."""
    def _provider(date_str: str, from_currency: str, to_currency: str) -> float | None:
        return get_rate(date_str, from_currency, to_currency, db=db)
    return _provider
