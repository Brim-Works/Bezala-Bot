"""FAS 11.5.1 — Per diem (traktamente) calculator enligt finsk lag.

Räknar matkavuorokaudet (resedygn) för en resa från `departure_home_at`
till `return_home_at`. Ett dygn = 24h-block räknat från avgång hemifrån.
Sista deldygnet hanteras enligt Verohallintos special-regler.

Kotimaan-regler (Finland):
  - >6h: osapäiväraha (halvdag)
  - >10h: kokopäiväraha (heldag)

Ulkomaan-regler (utomlands):
  - Hela 24h-dygn (kokonainen vuorokausi): full ulkomaanpäiväraha
  - Sista deldygn:
      >2h: halv ulkomaanpäiväraha
      >10h: full ulkomaanpäiväraha

Specialfall: Kort utlandsresa <10h (totalt) → kotimaan-regler.

Mat-avdrag: 2+ gratis måltider på en dag halverar dygnet — gäller dock
INTE halv-ulkomaanpäiväraha-deldygn (Verohallintos undantag).

Pure-funktioner — inga sidoeffekter, deterministisk output.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import PerDiemRate, Trip

logger = logging.getLogger(__name__)


# Konstanter (Verohallinto). Ändra här om regelverket uppdateras.
HALF_DAY_MIN_HOURS = 6
FULL_DAY_MIN_HOURS = 10
DELDYGN_HALF_DAY_THRESHOLD = 2     # >2h över sista hela dygn → halv
DELDYGN_FULL_DAY_THRESHOLD = 6     # >6h över sista hela dygn → heldag (kotimaa)
DELDYGN_FOREIGN_FULL_DAY = 10      # >10h över → ulkomaan kokopäiväraha

MEAL_DEDUCTION_PERCENT = Decimal("0.50")  # 2+ måltider = halverat


def _hours_between(start: datetime, end: datetime) -> float:
    """Returnerar antal timmar mellan två datetimes (kan vara naive eller TZ)."""
    return (end - start).total_seconds() / 3600.0


def _round_amount(value: Decimal | float) -> float:
    """Avrunda till 2 decimaler för final output. Decimal in, float ut."""
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return float(value.quantize(Decimal("0.01")))


def get_rate(db: Session, year: int, country_code: str) -> PerDiemRate | None:
    """Slå upp en per-diem-rate. Returnerar None om saknas."""
    return (
        db.query(PerDiemRate)
        .filter(PerDiemRate.year == year)
        .filter(PerDiemRate.country_code == country_code)
        .first()
    )


def calculate_per_diem(
    trip: Trip,
    db: Session,
    *,
    year: int | None = None,
    meal_toggles: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Beräkna per diem för en hel resa.

    Argument:
        trip: Trip med departure_home_at + return_home_at + destination_country
        db: SQLAlchemy session (för rate-lookup)
        year: vilket års rates som ska användas. Default = avgångsårets.
        meal_toggles: {day_number_str: bool} — användarens mat-val per dag.

    Returnerar:
        {
            "dygnet": [...],
            "total_amount": float,
            "currency": "EUR",
            "rules_year": int,
            "destination_country": str,
            "effective_country_used": str,
            "is_short_foreign_trip": bool,
            "calculated_at": ISO,
            "user_edited": bool,
            "warnings": [str, ...]
        }

    Vid fel returnerar dict med "error"-nyckel.
    """
    if not trip.departure_home_at or not trip.return_home_at:
        return {"error": "Saknar avgångs- eller hemkomsttid"}

    if trip.return_home_at <= trip.departure_home_at:
        return {"error": "Hemkomsttid måste vara efter avgångstid"}

    departure = trip.departure_home_at
    arrival = trip.return_home_at

    if year is None:
        year = departure.year

    destination_country = (trip.destination_country or "FI").upper()
    warnings: list[str] = []

    total_hours = _hours_between(departure, arrival)

    # Specialfall: kort utlandsresa <10h → kotimaan-regler
    is_short_foreign_trip = (
        destination_country != "FI"
        and total_hours < FULL_DAY_MIN_HOURS
    )
    effective_country = "FI" if is_short_foreign_trip else destination_country

    rates = get_rate(db, year, effective_country)
    if rates is None:
        # Fallback: försök hämta FI-rates
        rates = get_rate(db, year, "FI")
        if rates is None:
            return {
                "error": (
                    f"Saknar rates för {effective_country} år {year} "
                    f"(och Finland-fallback saknas också)"
                )
            }
        warnings.append(
            f"Saknade rates för {effective_country} år {year} — "
            f"använder Finland som fallback"
        )
        effective_country = "FI"

    full_day = Decimal(str(rates.full_day_amount))
    half_day = Decimal(str(rates.half_day_amount))

    # Bygg dygnet (24h-block från avgång)
    dygnet: list[dict[str, Any]] = []
    dygn_start = departure
    day_number = 1
    safety = 0  # hindra oändlig loop vid orimliga datum

    while dygn_start < arrival and safety < 1000:
        safety += 1
        dygn_end_potential = dygn_start + timedelta(hours=24)

        if dygn_end_potential <= arrival:
            # Komplett 24h-dygn
            dygn_data = _full_dygn(
                dygn_start, dygn_end_potential, day_number,
                effective_country, destination_country,
                full_day, rates.currency,
            )
            dygnet.append(dygn_data)
            dygn_start = dygn_end_potential
        else:
            # Sista deldygn (vajaa matkavuorokausi)
            dygn_data = _last_partial_dygn(
                dygn_start, arrival, day_number,
                effective_country, destination_country,
                full_day, half_day, rates.currency,
                is_short_foreign=is_short_foreign_trip,
            )
            if dygn_data is not None:
                dygnet.append(dygn_data)
            break
        day_number += 1

    # Applicera mat-toggles om angivet
    if meal_toggles:
        _apply_meal_toggles_inplace(dygnet, meal_toggles)
        user_edited = True
    else:
        user_edited = False

    total = sum(
        Decimal(str(d["final_amount"])) for d in dygnet
    ) if dygnet else Decimal("0")

    return {
        "dygnet": dygnet,
        "total_amount": _round_amount(total),
        "currency": rates.currency,
        "rules_year": year,
        "destination_country": destination_country,
        "effective_country_used": effective_country,
        "is_short_foreign_trip": is_short_foreign_trip,
        "calculated_at": datetime.utcnow().isoformat(),
        "user_edited": user_edited,
        "warnings": warnings,
    }


def _full_dygn(
    start: datetime,
    end: datetime,
    day_number: int,
    effective_country: str,
    destination_country: str,
    full_day_amount: Decimal,
    currency: str,
) -> dict[str, Any]:
    """Komplett 24h-dygn = full traktamente (ingen mat-avdrag default)."""
    is_abroad = effective_country != "FI"
    return {
        "day_number": day_number,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "hours": 24.0,
        "ends_in_country": destination_country if is_abroad else "FI",
        "type": "full_day_abroad" if is_abroad else "full_day_domestic",
        "rate_amount": _round_amount(full_day_amount),
        "rate_currency": currency,
        "meal_deduction": False,
        "final_amount": _round_amount(full_day_amount),
        "rule_applied": (
            "kokopäiväraha_ulkomaa" if is_abroad else "kokopäiväraha_kotimaa"
        ),
    }


def _last_partial_dygn(
    start: datetime,
    end: datetime,
    day_number: int,
    effective_country: str,
    destination_country: str,
    full_day_amount: Decimal,
    half_day_amount: Decimal,
    currency: str,
    *,
    is_short_foreign: bool,
) -> dict[str, Any] | None:
    """Sista deldygn enligt Verohallintos regler.

    Returnerar None om dygnet är för kort för traktamente.
    """
    hours = _hours_between(start, end)

    if effective_country == "FI" or is_short_foreign:
        # Kotimaan-regler
        if hours > FULL_DAY_MIN_HOURS:
            amount = full_day_amount
            day_type = "full_day_domestic"
            rule = "kokopäiväraha_kotimaa_deldygn"
        elif hours > HALF_DAY_MIN_HOURS:
            amount = half_day_amount
            day_type = "half_day_domestic"
            rule = "osapäiväraha_kotimaa_deldygn"
        else:
            return None
        ends_in = "FI" if effective_country == "FI" else destination_country
    else:
        # Ulkomaan-regler
        if hours > DELDYGN_FOREIGN_FULL_DAY:
            amount = full_day_amount
            day_type = "full_day_abroad"
            rule = "kokopäiväraha_ulkomaa_deldygn"
        elif hours > DELDYGN_HALF_DAY_THRESHOLD:
            amount = full_day_amount / Decimal("2")
            day_type = "half_day_abroad"
            rule = "puolikas_ulkomaanpäiväraha_deldygn"
        else:
            return None
        ends_in = destination_country

    return {
        "day_number": day_number,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "hours": round(hours, 1),
        "ends_in_country": ends_in,
        "type": day_type,
        "rate_amount": _round_amount(amount),
        "rate_currency": currency,
        "meal_deduction": False,
        "final_amount": _round_amount(amount),
        "rule_applied": rule,
    }


def _apply_meal_toggles_inplace(
    dygnet: list[dict[str, Any]],
    meal_toggles: dict[str, bool],
) -> None:
    """Applicera användarens mat-val på en redan-beräknad dygn-lista.

    meal_toggles-nycklar kan vara strängar eller ints — vi accepterar båda
    så frontend-JSON inte behöver konvertera.
    """
    # Normalisera nycklar till int
    normalized = {}
    for key, value in meal_toggles.items():
        try:
            normalized[int(key)] = bool(value)
        except (TypeError, ValueError):
            continue

    for dygn in dygnet:
        day_num = dygn["day_number"]
        meal_included = normalized.get(day_num, False)

        # Mat-avdrag gäller INTE för halv-ulkomaanpäiväraha-deldygn
        is_partial_foreign = (
            dygn["rule_applied"] == "puolikas_ulkomaanpäiväraha_deldygn"
        )

        if meal_included and not is_partial_foreign:
            dygn["meal_deduction"] = True
            dygn["final_amount"] = _round_amount(
                Decimal(str(dygn["rate_amount"])) * MEAL_DEDUCTION_PERCENT
            )
        else:
            dygn["meal_deduction"] = False
            dygn["final_amount"] = _round_amount(
                Decimal(str(dygn["rate_amount"]))
            )


def apply_meal_deductions(
    per_diem_data: dict[str, Any],
    meal_toggles: dict[str, bool],
) -> dict[str, Any]:
    """Returnerar en kopia av per_diem_data med mat-toggles applicerade
    och total_amount omräknat. Används för PATCH-endpoint."""
    if "dygnet" not in per_diem_data:
        return per_diem_data

    # Mutera in-place på en kopia av listan (men dygnen själva är okej
    # att mutera eftersom de är fria-stående dicts).
    new_data = dict(per_diem_data)
    new_dygnet = [dict(d) for d in per_diem_data["dygnet"]]
    new_data["dygnet"] = new_dygnet

    _apply_meal_toggles_inplace(new_dygnet, meal_toggles)

    total = sum(
        Decimal(str(d["final_amount"])) for d in new_dygnet
    ) if new_dygnet else Decimal("0")
    new_data["total_amount"] = _round_amount(total)
    new_data["user_edited"] = True
    new_data["calculated_at"] = datetime.utcnow().isoformat()
    return new_data


def list_supported_countries(db: Session, year: int) -> list[dict[str, Any]]:
    """Returnerar alla länder vi har rates för ett visst år."""
    rows = (
        db.query(PerDiemRate)
        .filter(PerDiemRate.year == year)
        .order_by(PerDiemRate.country_name.asc())
        .all()
    )
    return [
        {
            "country_code": r.country_code,
            "country_name": r.country_name,
            "full_day_amount": float(r.full_day_amount),
            "half_day_amount": float(r.half_day_amount),
            "currency": r.currency,
            "source": r.source,
        }
        for r in rows
    ]
