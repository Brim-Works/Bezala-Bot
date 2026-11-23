"""C49 Del 2 - underlag per resa för Netvisors Dagpenningskalkylator.

Ren läsning: resan ändras aldrig och inget skickas. Boten räknar INTE
traktamentsbelopp - Netvisor räknar. Underlaget innehåller exakt det som
fylls i för hand: rubrik, beskrivning och en eller flera kalkylatorrader
(land, start/slut i Helsingfors-tid, beskrivning, tilläggsinställningar).

"Inklusive kost" gäller en hel kalkylatorrad i Netvisor. Har bara vissa dagar
2 fria måltider delas resan upp i flera rader: en per sammanhängande period
med samma land och samma kost-status. Gränsen mellan två rader är midnatt
(Helsingfors-tid).
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import CalendarTrip, CalendarTripDay
from app.services.calendar_trip_edit import TripEditError, _aware
from app.services.text_clean import plain_dashes
from app.services.trip_english import (
    combine_purposes,
    country_name,
    generate_purpose,
    meals_note,
    netvisor_description_warnings,
    netvisor_line_description,
    non_english_warnings,
    split_cities,
)
from app.services.trip_meals import meals_included

HELSINKI = ZoneInfo("Europe/Helsinki")
_FANCY_DASH_RE = re.compile("[‐‑‒–—―−]")

# Land på svenska så som Netvisor visar dem i Dagpenningskalkylatorn.
COUNTRY_NAMES_SV: dict[str, str] = {
    "FI": "Finland", "SE": "Sverige", "NO": "Norge", "DK": "Danmark",
    "IS": "Island", "EE": "Estland", "LV": "Lettland", "LT": "Litauen",
    "GB": "Storbritannien", "IE": "Irland", "NL": "Nederländerna",
    "BE": "Belgien", "DE": "Tyskland", "FR": "Frankrike", "CH": "Schweiz",
    "AT": "Österrike", "PL": "Polen", "ES": "Spanien", "PT": "Portugal",
    "IT": "Italien", "CZ": "Tjeckien", "HU": "Ungern", "TR": "Turkiet",
    "US": "USA", "LU": "Luxemburg", "GR": "Grekland", "HR": "Kroatien",
    "SI": "Slovenien", "SK": "Slovakien", "RO": "Rumänien", "BG": "Bulgarien",
    "CY": "Cypern", "MT": "Malta", "CA": "Kanada", "JP": "Japan", "CN": "Kina",
    "IN": "Indien", "AE": "Förenade Arabemiraten", "RU": "Ryssland",
    "UA": "Ukraina", "SG": "Singapore", "AU": "Australien",
}


def country_sv(code: str | None) -> str | None:
    if not code:
        return None
    return COUNTRY_NAMES_SV.get(code.upper(), code.upper())


def fmt_date(d: date) -> str:
    """d.m.yyyy (Netvisor), t.ex. 13.1.2026."""
    return f"{d.day}.{d.month}.{d.year}"


def _hel(value: str | None) -> datetime | None:
    dt = _aware(value)
    return dt.astimezone(HELSINKI) if dt else None


def _midnight(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=HELSINKI)


def _cities(names) -> list[str]:
    return split_cities(names)


def _join(cities: list[str]) -> str:
    if not cities:
        return ""
    if len(cities) == 1:
        return cities[0]
    return ", ".join(cities[:-1]) + " and " + cities[-1]


def travel_description_info(trip: CalendarTrip, days: list[CalendarTripDay]) -> dict:
    """C55 - Netvisor-beskrivningen + varifrån syftet kom.

    Syftet tas ur resans `purpose`, annars ur dagarnas syften, annars ur
    kalenderns möten. Hittas inget: 'Travel in <city>' + purpose_missing
    (boten gissar inte - Mikko sätter syftet i Resor-vyn)."""
    purpose, source = (trip.purpose or "").strip() or None, "trip"
    cities_raw = [d.destination for d in days] or [trip.destination]
    if not purpose:
        purpose = combine_purposes([d.purpose for d in days], split_cities(cities_raw))
        source = "days"
    if not purpose:
        meetings = [m for d in days for m in (d.meetings or [])]
        cities = split_cities(cities_raw)
        purpose = generate_purpose(meetings, None, extra_cities=cities) if meetings else None
        if purpose and purpose.strip().lower() == "meetings":
            purpose = None  # bara generiska möten - gissa inte
        source = "calendar"
    text = netvisor_line_description(cities_raw, purpose)
    flags = [] if purpose else ["purpose_missing"]
    return {
        "text": text,
        "purpose_source": source if purpose else None,
        "flags": flags,
        "warnings": netvisor_description_warnings(text),
    }


def netvisor_travel_description(trip: CalendarTrip, days: list[CalendarTripDay] | None = None,
                                db: Session | None = None) -> str:
    """C55 - 'Travel for <purpose> in <city>' för en resa."""
    if days is None:
        days = _days(db, trip) if db is not None else list(getattr(trip, "days", []) or [])
    return travel_description_info(trip, days)["text"]


def compensation_type(country: str | None, hours: float) -> str | None:
    """Klassificering (inte belopp) för tripexpense.nv:
    utrikes -> 'foreign'; Finland -> 'domesticfull' (> 10 h) eller
    'domestichalf' (> 6 h); kortare inrikes -> None (ingen dagpenning).
    Netvisor räknar själv belopp och antal enligt sina regler."""
    if not country:
        return None
    if country.upper() != "FI":
        return "foreign"
    if hours > 10:
        return "domesticfull"
    if hours > 6:
        return "domestichalf"
    return None


def started_periods(hours: float) -> int:
    """Antal påbörjade 24-timmarsperioder (information, inget belopp).
    Resa 93: 05:07 -> 23:05 nästa dag = 42 h = 2 dygn."""
    if hours <= 0:
        return 0
    full, rest = divmod(hours, 24)
    return int(full) + (1 if rest > 0 else 0)


def _days(db: Session, trip: CalendarTrip) -> list[CalendarTripDay]:
    return (
        db.query(CalendarTripDay)
        .filter(CalendarTripDay.trip_id == trip.id)
        .order_by(CalendarTripDay.day_date)
        .all()
    )


def _group_days(trip: CalendarTrip, days: list[CalendarTripDay]) -> list[dict]:
    """Sammanhängande perioder med samma land + kost-status."""
    trip_cc = trip.country if trip.country and "," not in trip.country else None
    groups: list[dict] = []
    for d in days:
        cc = (d.country or trip_cc or "").upper() or None
        incl = meals_included(d.free_meals)
        if groups and groups[-1]["country"] == cc and groups[-1]["meals_included"] == incl \
                and groups[-1]["days"][-1].day_date + timedelta(days=1) == d.day_date:
            groups[-1]["days"].append(d)
        else:
            groups.append({"country": cc, "meals_included": incl, "days": [d]})
    if not groups:
        groups.append({"country": trip_cc, "meals_included": False, "days": []})
    return groups


def build_entry(db: Session, trip_id: int) -> dict:
    from app.services.netvisor_status import status_of

    trip = db.get(CalendarTrip, trip_id)
    if trip is None:
        raise TripEditError(f"Trip {trip_id} not found.")
    days = _days(db, trip)
    warnings: list[str] = []
    notes: list[str] = []

    # --- status
    if trip.status != "approved":
        warnings.append(
            f"Trip status is '{trip.status}' - only approved trips get a Netvisor entry."
        )
    nv = status_of(db, trip.id)
    if nv["status"] in ("created_manual", "created_api"):
        warnings.append(
            f"Already created in Netvisor ({nv['status']}, report "
            f"{nv['netvisor_report_id'] or '?'}) - do not create it again."
        )

    # --- tider (Helsingfors)
    start = _hel(trip.travel_start_at)
    end = _hel(trip.travel_end_at)
    if start is None or end is None:
        missing = [n for n, v in (("start", start), ("end", end)) if v is None]
        warnings.append(f"Missing travel time ({' and '.join(missing)}).")
        start = start or _hel(trip.start_at)
        end = end or _hel(trip.end_at)
        if start or end:
            notes.append("Flight times are used where the travel time is missing.")

    # --- texter
    for field, value in (("purpose", trip.purpose), ("destination", trip.destination)):
        if value and _FANCY_DASH_RE.search(value):
            warnings.append(
                f"{field} contains a long dash ('–'); Netvisor text uses a plain '-'."
            )
    desc_info = travel_description_info(trip, days)
    description = desc_info["text"]
    if "purpose_missing" in desc_info["flags"]:
        warnings.append(
            "Purpose is missing (Netvisor description must be in English) - "
            "set it in the Trips view."
        )
    warnings += desc_info["warnings"]
    warnings += non_english_warnings("purpose", trip.purpose)
    warnings += non_english_warnings("destination", trip.destination)

    cities = _cities([d.destination for d in days] or [trip.destination])
    countries = []
    for d in days:
        cc = d.country or (trip.country if trip.country and "," not in trip.country else None)
        if cc and cc not in countries:
            countries.append(cc.upper())
    if not countries and trip.country:
        countries = [c.strip().upper() for c in trip.country.split(",") if c.strip()]
    if not countries:
        warnings.append("Country is missing.")
    elif any(not (d.country or trip.country) for d in days):
        warnings.append("Country is missing for some days.")

    d_from, d_to = trip.start_date, trip.end_date
    dates = fmt_date(d_from) if d_from == d_to else f"{fmt_date(d_from)}-{fmt_date(d_to)}"
    where = ", ".join(p for p in (_join(cities), country_name(",".join(countries))) if p)
    title = plain_dashes(f"Business trip {where} {dates}".replace("  ", " "))

    # --- kalkylatorrader
    groups = _group_days(trip, days)
    has_flight = bool(trip.flights)
    rows = []
    for i, g in enumerate(groups):
        g_start = start if i == 0 else _midnight(g["days"][0].day_date)
        g_end = end if i == len(groups) - 1 else _midnight(g["days"][-1].day_date + timedelta(days=1))
        hours = (g_end - g_start).total_seconds() / 3600 if g_start and g_end else 0.0
        cc = g["country"]
        g_days = g["days"]
        # C55 - samma text på varje rad som reseräkningens beskrivning.
        # Måltider står i "Inklusive kost" (och internt i meals_note), aldrig i texten.
        desc = description
        g_meals = meals_note(
            (d.day_date.isoformat(), d.free_meals, d.free_meal_sources, d.country or cc)
            for d in g_days
        )
        incl_days = [fmt_date(d.day_date) for d in g_days if meals_included(d.free_meals)]
        night = _night_without_hotel(g_days, trip.end_date)
        rows.append({
            "row": i + 1,
            "by_air_or_sea": has_flight,
            "object": country_sv(cc),
            "country": cc,
            "start_date": fmt_date(g_start.date()) if g_start else None,
            "start_time": g_start.strftime("%H:%M") if g_start else None,
            "end_date": fmt_date(g_end.date()) if g_end else None,
            "end_time": g_end.strftime("%H:%M") if g_end else None,
            "description": desc,
            # Bara i botens vy - exporteras inte till Netvisor.
            "meals_note": g_meals,
            "settings": {
                "over_15_km": True,
                "over_5_km": True,
                "meals_included": g["meals_included"],
                "meals_included_days": incl_days,
                "night_travel": False,
                "night_travel_check": night,
            },
            "domestic": cc == "FI",
            "hours": round(hours, 2),
            "started_24h_periods": started_periods(hours),
            "compensation_type": compensation_type(cc, hours),
        })

    if len(rows) > 1:
        changes = []
        if len({r["country"] for r in rows}) > 1:
            changes.append("the country changes")
        if len({r["settings"]["meals_included"] for r in rows}) > 1:
            changes.append(
                "'Inklusive kost' applies to a whole calculator row in Netvisor, and "
                "only some days have 2 free meals ("
                + ", ".join(d for r in rows for d in r["settings"]["meals_included_days"])
                + ")"
            )
        notes.append(
            f"Split into {len(rows)} calculator rows because "
            + " and ".join(changes)
            + ". Rows are split at midnight (Helsinki time)."
        )
    if any(r["domestic"] for r in rows):
        notes.append(
            "Domestic trip (Finland): Netvisor uses the domestic per diem rules. "
            "No amount is calculated here."
        )
    for r in rows:
        if r["settings"]["night_travel_check"]:
            notes.append(f"Row {r['row']}: {r['settings']['night_travel_check']}")
    # C58 - hotell som fakturerats direkt till företaget blir aldrig en
    # utgiftsrad i reseräkningen (boten bygger bara traktamentsrader).
    invoiced = invoiced_hotel_nights(db, trip.id)
    if invoiced:
        notes.append(
            "Hotel invoiced directly to the company (" + ", ".join(invoiced)
            + ") - no expense line in the travel expense."
        )

    return {
        "trip_id": trip.id,
        "trip_status": trip.status,
        "netvisor_status": nv,
        "title": title,
        "description": description,
        "purpose_source": desc_info["purpose_source"],
        "flags": desc_info["flags"],
        "time_zone": "Europe/Helsinki",
        "travel_start": start.strftime("%Y-%m-%d %H:%M") if start else None,
        "travel_end": end.strftime("%Y-%m-%d %H:%M") if end else None,
        "rows": rows,
        "warnings": warnings,
        "notes": notes,
        "ready": not warnings,
        "amounts": "Calculated by Netvisor, not by Bezala Bot.",
        "hotel_invoiced_to_company": invoiced,
    }


def invoiced_hotel_nights(db: Session, trip_id: int) -> list[str]:
    from app.models import TripExpectation

    rows = (db.query(TripExpectation)
            .filter(TripExpectation.trip_id == trip_id, TripExpectation.kind == "hotel",
                    TripExpectation.resolution == "invoiced_to_company")
            .order_by(TripExpectation.slot).all())
    return [r.slot for r in rows]


def _night_without_hotel(days: list[CalendarTripDay], trip_end: date) -> str | None:
    """Nätter i perioden utan hotell -> flagga (kan vara 'Resa på natten')."""
    nights = [d for d in days if d.day_date < trip_end and not d.hotel]
    if not nights:
        return None
    labels = ", ".join(
        f"{fmt_date(d.day_date)}-{fmt_date(d.day_date + timedelta(days=1))}" for d in nights
    )
    return (
        f"No hotel found for the night {labels} - check whether the night was spent "
        "travelling ('Resa på natten')."
    )
