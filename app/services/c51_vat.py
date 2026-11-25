"""C51 Del 2-3 — moms, konto, kostnadsställe och beskrivning per kortrad.

Regler verifierade mot kvitton av C7 + Mikko (2026-11-22). Momsen läses
alltid från kvittot först (`vat_info` från kvittoanalysen); saknas den
används landet från kortraden/leverantören.

Bezalas MOMS-alternativ: 0%, Purchases Abroad (EU), Purchases Abroad
(Non-EU), 10%, 13,5%, 25,5%.

1. Köp på plats i annat land (hotell, taxi, tåg, restaurang, parkering …) → 0%.
2. Internationellt flyg → 0%.
3. Tjänst från bolag utanför EU utan moms på fakturan → Purchases Abroad (Non-EU).
4. Tjänst från EU-bolag med reverse charge → Purchases Abroad (EU).
5. Utländsk säljare som debiterar finsk moms → 0% + flagga. Anthropic:
   t.o.m. faktura 14 nov 2026 finsk moms via OSS → 0%; från 14 dec 2026
   reverse charge → Purchases Abroad (Non-EU).
6. Finskt köp med moms på kvittot → satsen på kvittot (flera satser → flera rader).
7. Kortslip utan momsspecifikation: finskt → kontots standardsats + flagga;
   utländskt → 0%. Hotell med bara kortslip → flagga "Hotel folio missing".
8. Återbetalning (negativ kortrad): samma moms som köpet, negativt belopp,
   beskrivning börjar med "Refund - ".
9. Kontot styr standardmomsen i Bezala → konto först, sedan moms.

Ingenting här skriver till Bezala. Förslaget visas på svep-kortet med en
kopieringsknapp per fält.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable

# ---------------------------------------------------------------- moms-alternativ

VAT_0 = "0%"
VAT_EU = "Purchases Abroad (EU)"
VAT_NON_EU = "Purchases Abroad (Non-EU)"
VAT_10 = "10%"
VAT_13_5 = "13,5%"
VAT_25_5 = "25,5%"
VAT_ACCOUNT_DEFAULT = "Account default"
BEZALA_VAT_OPTIONS = (VAT_0, VAT_EU, VAT_NON_EU, VAT_10, VAT_13_5, VAT_25_5)

_RATE_TO_OPTION = {0.0: VAT_0, 10.0: VAT_10, 13.5: VAT_13_5, 25.5: VAT_25_5}

FLAG_FI_VAT_FOREIGN = "Supplier charged Finnish VAT - check buyer VAT ID"
FLAG_NO_BREAKDOWN = "Receipt has no VAT breakdown"
FLAG_FOLIO_MISSING = "Hotel folio missing"
FLAG_COUNTRY_UNKNOWN = "VAT country unknown - check the receipt"
FLAG_RATE_UNKNOWN = "VAT rate on receipt not in Bezala - check"
FLAG_REVERSE_CHARGE = "Check reverse charge on the invoice"
FLAG_FLIGHT_ROUTE = "Flight route unknown - check it is international"
FLAG_VAT_NOT_READ = "VAT not read from the receipt yet - check the receipt"

REFUND_PREFIX = "Refund - "

# C60 — finska parkeringsbolag med 25,5 % moms (Mikko bekräftade).
_FI_PARKING = re.compile(r"moovy|finavia", re.IGNORECASE)

# Anthropic: VAT-nr satt av Mikko → reverse charge från fakturan 14 dec 2026.
ANTHROPIC_REVERSE_CHARGE_FROM = date(2026, 12, 14)

# ---------------------------------------------------------------- konton (Del 3)

COST_CENTER_NAME = "VIS128 Visma HRM Sverige AB"

ACCOUNT_CAR = "Muut ajoneuvokulut"
ACCOUNT_TICKETS = "Matkaliput"
ACCOUNT_HOTEL = "Hotelli- ym. majoitus"
ACCOUNT_TAXI = "Taksikulut"
ACCOUNT_PARKING = "Paikoituskulut"
ACCOUNT_AI = "AI työkalut"

# Reserv per kategori (Mikkos beslut). Kontoregeln från C50 gäller först.
CATEGORY_ACCOUNTS = {
    "Flyg": ACCOUNT_TICKETS,
    "Tåg": ACCOUNT_TICKETS,
    "Kollektivtrafik": ACCOUNT_TICKETS,
    "Hotell": ACCOUNT_HOTEL,
    "Taxi": ACCOUNT_TAXI,
    "Parkering": ACCOUNT_PARKING,
    "AI-verktyg": ACCOUNT_AI,
}
_CAR_WORDS = re.compile(r"car ?wash|biltvätt|autopesu|tjänstebil|company car|työsuhdeauto",
                        re.IGNORECASE)


def category_account(category: str | None, *texts: str | None) -> str | None:
    hay = " ".join(t or "" for t in texts)
    if _CAR_WORDS.search(hay):
        return ACCOUNT_CAR
    return CATEGORY_ACCOUNTS.get(category or "")


# ---------------------------------------------------------------- land

EU_COUNTRIES = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})

# Säljarens land för tjänster där kortraden inte räcker (verifierat av C7).
KNOWN_SELLERS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"anthropic|claude", re.I), "US"),
    (re.compile(r"stackblitz|bolt\.new|bolt \(by stackblitz\)", re.I), "US"),
    (re.compile(r"cursor|anysphere", re.I), "US"),
    (re.compile(r"lovable", re.I), "US"),
    (re.compile(r"openai|chatgpt", re.I), "US"),
    (re.compile(r"midjourney", re.I), "US"),
)

# "…, SAN FRANCISCO, US 23.3 EUR" / "…, HELSINGBORG, SE 83.66 EUR"
_CARD_COUNTRY_RE = re.compile(r",\s*([A-Z]{2})\s+-?\d[\d.,]*\s*[A-Z]{3}\s*$")

# Kategorier som köps på plats (rule 1).
ON_SITE_CATEGORIES = frozenset({
    "Hotell", "Taxi", "Tåg", "Kollektivtrafik", "Mat", "Parkering", "Bilhyra",
    "Representation",
})
SERVICE_CATEGORIES = frozenset({
    "AI-verktyg", "Mjukvara", "Telefon", "Datakommunikation", "Utbildning", "Böcker",
})

FINNISH_PLACES = frozenset({
    "helsinki", "helsingfors", "vantaa", "espoo", "oulu", "rovaniemi", "tampere",
    "turku", "åbo", "vaasa", "kuopio", "joensuu", "ivalo", "kittilä", "kittila",
    "kemi", "kajaani", "kokkola", "jyväskylä", "jyvaskyla", "lappeenranta",
    "savonlinna", "pori", "kuusamo", "hel", "oul", "rvn", "tmp", "tku", "vaa",
    "kuo", "joe", "ivl", "ktt", "kem", "kaj", "kok", "jyv", "lpp", "svl", "por", "kao",
})
_ROUTE_RE = re.compile(r"\b([A-ZÅÄÖ][\wåäö.-]+)\s+(?:to|-|→)\s+([A-ZÅÄÖ][\wåäö.-]+)")


def card_country(card_description: str | None) -> str | None:
    m = _CARD_COUNTRY_RE.search((card_description or "").strip())
    return m.group(1) if m else None


def seller_country(*texts: str | None) -> str | None:
    hay = " ".join(t or "" for t in texts)
    for pat, cc in KNOWN_SELLERS:
        if pat.search(hay):
            return cc
    return None


def _norm_cc(value: Any) -> str | None:
    if not value:
        return None
    v = str(value).strip().upper()
    return v if re.fullmatch(r"[A-Z]{2}", v) else None


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _rate_option(rate: Any) -> str | None:
    try:
        r = round(float(str(rate).replace(",", ".").rstrip("%")), 1)
    except (TypeError, ValueError):
        return None
    return _RATE_TO_OPTION.get(r)


def _flight_international(segments: Any, *texts: str | None) -> bool | None:
    """True/False om rutten kan avgöras, annars None."""
    places: list[str] = []
    for seg in segments or []:
        if isinstance(seg, dict):
            # C60 — flight_segments heter "origin"/"dest" (receipt_details).
            # "dest" saknades: HEL→ARN blev bara ["HEL"] = inrikes.
            for k in ("from", "to", "origin", "dest", "destination", "destination_iata",
                      "departure_airport", "arrival_airport"):
                if seg.get(k):
                    places.append(str(seg[k]))
    if not places:
        for t in texts:
            for a, b in _ROUTE_RE.findall(t or ""):
                places += [a, b]
    if not places:
        return None
    return any(p.strip().lower() not in FINNISH_PLACES for p in places)


# ---------------------------------------------------------------- kvittots moms


def _receipt_vat(vat_info: dict | None) -> dict:
    vi = vat_info if isinstance(vat_info, dict) else {}
    lines = []
    for ln in vi.get("lines") or []:
        if isinstance(ln, dict) and ln.get("rate") is not None:
            lines.append(ln)
    return {
        "lines": lines,
        "reverse_charge": bool(vi.get("reverse_charge")),
        "finnish_vat_charged": bool(vi.get("finnish_vat_charged")),
        "seller_country": _norm_cc(vi.get("seller_country")),
        "purchase_country": _norm_cc(vi.get("purchase_country")),
        "document": (vi.get("document") or "").lower() or None,
    }


def _line_amount(ln: dict) -> float | None:
    for k in ("gross", "total", "amount_incl_vat"):
        if ln.get(k) is not None:
            try:
                return round(float(ln[k]), 2)
            except (TypeError, ValueError):
                return None
    try:
        net = float(ln.get("net")) if ln.get("net") is not None else None
        vat = float(ln.get("vat")) if ln.get("vat") is not None else None
    except (TypeError, ValueError):
        return None
    if net is not None and vat is not None:
        return round(net + vat, 2)
    return None


# ---------------------------------------------------------------- motor


def suggest_vat(*, category: str | None = None, texts: Iterable[str | None] = (),
                card_description: str | None = None, card_amount: float | None = None,
                receipt_date: Any = None, vat_info: dict | None = None,
                flight_segments: Any = None) -> dict:
    """Momsförslag. Returnerar {vat, lines, flags, rule}. `vat` är ett av
    Bezalas alternativ, VAT_ACCOUNT_DEFAULT eller None (okänt)."""
    texts = [t for t in texts if t]
    hay = " ".join(texts + [card_description or ""])
    rv = _receipt_vat(vat_info)
    refund = card_amount is not None and float(card_amount) < 0
    flags: list[str] = []

    seller = rv["seller_country"] or seller_country(hay)
    country = rv["purchase_country"] or card_country(card_description)
    if country is None and seller and category not in ON_SITE_CATEGORIES:
        country = seller
    card_slip = rv["document"] == "card_slip"

    def done(vat, rule, lines=None):
        out_lines = lines or ([{"vat": vat, "amount": card_amount}] if vat else [])
        if refund:
            out_lines = [{**ln, "amount": -abs(ln["amount"]) if ln.get("amount") is not None
                          else None} for ln in out_lines]
        return {"vat": vat, "lines": out_lines, "flags": flags, "rule": rule,
                "refund": refund}

    foreign_seller = seller is not None and seller != "FI"

    # Regel 5 — utländsk säljare som tar finsk moms.
    if foreign_seller and rv["finnish_vat_charged"]:
        flags.append(FLAG_FI_VAT_FOREIGN)
        return done(VAT_0, "5: foreign supplier charged Finnish VAT")
    if re.search(r"anthropic|claude", hay, re.I) and not rv["reverse_charge"] \
            and not rv["lines"]:
        d = _as_date(receipt_date)
        if d is None or d < ANTHROPIC_REVERSE_CHARGE_FROM:
            flags.append(FLAG_FI_VAT_FOREIGN)
            return done(VAT_0, "5: Anthropic charged Finnish VAT (OSS) until invoice 14 Nov 2026")
        return done(VAT_NON_EU, "5: Anthropic reverse charge from invoice 14 Dec 2026")

    # Regel 3/4 — reverse charge på fakturan.
    if rv["reverse_charge"]:
        if seller in EU_COUNTRIES and seller != "FI":
            return done(VAT_EU, "4: EU supplier, reverse charge")
        return done(VAT_NON_EU, "3: non-EU supplier, reverse charge")

    # Regel 2 — flyg.
    if category == "Flyg":
        intl = _flight_international(flight_segments, *texts)
        if intl is None and country and country != "FI":
            intl = True
        if intl is not False:
            if intl is None:
                flags.append(FLAG_FLIGHT_ROUTE)
            return done(VAT_0, "2: international flight (only flight taxes)")
        # Inrikesflyg: läs kvittot (regel 6/7 nedan).

    # Regel 1 — köp på plats i annat land.
    if country and country != "FI" and (category in ON_SITE_CATEGORIES or category is None
                                        and not foreign_seller):
        if category == "Hotell" and card_slip:
            flags.append(FLAG_FOLIO_MISSING)
        return done(VAT_0, f"1: bought on site in {country} - foreign VAT not deductible")

    # Regel 3/4 — tjänst från utlandet utan moms på fakturan.
    if foreign_seller and category not in ON_SITE_CATEGORIES:
        has_vat = any((_rate_option(ln.get("rate")) or "") not in ("", VAT_0)
                      for ln in rv["lines"])
        if not has_vat:
            if seller in EU_COUNTRIES:
                flags.append(FLAG_REVERSE_CHARGE)
                return done(VAT_EU, "4: EU supplier without VAT on the invoice")
            return done(VAT_NON_EU, "3: non-EU supplier without VAT on the invoice")
        # Utländsk säljare med moms på fakturan men okänt land på momsen.
        flags.append(FLAG_FI_VAT_FOREIGN)
        return done(VAT_0, "5: foreign supplier charged VAT")

    # Regel 6 — finskt köp, satsen/satserna på kvittot.
    if country in (None, "FI") and rv["lines"]:
        lines = []
        for ln in rv["lines"]:
            opt = _rate_option(ln.get("rate"))
            if opt is None:
                flags.append(FLAG_RATE_UNKNOWN)
                return done(None, f"6: VAT rate {ln.get('rate')} on receipt is not in Bezala")
            lines.append({"vat": opt, "amount": _line_amount(ln)})
        if len(lines) == 1:
            lines[0]["amount"] = card_amount if card_amount is not None else lines[0]["amount"]
        vat = lines[0]["vat"] if len({ln["vat"] for ln in lines}) == 1 else "Several rates"
        return done(vat, "6: Finnish purchase - rate(s) from the receipt", lines)

    # C60 — Moovy/Finavia-parkering i Finland: 25,5 % (Mikko bekräftade
    # 2026-11-25). Används när kvittot saknar momsspecifikation, i stället
    # för "Account default".
    if country == "FI" and category == "Parkering" and _FI_PARKING.search(hay):
        return done(VAT_25_5, "7b: Moovy/Finavia parking in Finland - 25.5% (Mikko confirmed)")

    # Regel 7 — kortslip/kvitto utan momsspecifikation.
    if country == "FI":
        flags.append(FLAG_NO_BREAKDOWN if vat_info is not None else FLAG_VAT_NOT_READ)
        if category == "Hotell":
            flags.append(FLAG_FOLIO_MISSING)
        return done(VAT_ACCOUNT_DEFAULT, "7: Finnish purchase without VAT breakdown - "
                                         "account default rate")

    flags.append(FLAG_COUNTRY_UNKNOWN)
    return done(None, "unknown country")


# ---------------------------------------------------------------- återbetalning (regel 8)

_REFUND_WORDS = re.compile(r"refund|återbetal|hyvitys|palautus|credit ?note|kreditnota|"
                           r"cancel+ation|cancelled|peruutus", re.IGNORECASE)


def is_refund_document(*texts: str | None) -> bool:
    return bool(_REFUND_WORDS.search(" ".join(t or "" for t in texts)))


def refund_coupling_error(card_amount, receipt_amount, *texts: str | None) -> str | None:
    """Regel 8: ett köpkvitto kopplas aldrig som positivt belopp mot en
    negativ kortrad. Returnerar felmeddelande eller None."""
    try:
        ca = float(card_amount) if card_amount is not None else None
        ra = float(receipt_amount) if receipt_amount is not None else None
    except (TypeError, ValueError):
        return None
    if ca is None or ca >= 0:
        return None
    if ra is not None and ra < 0:
        return None
    if is_refund_document(*texts):
        return None
    return ("Card line is a refund (negative amount) - a purchase receipt is never coupled "
            "to it as a positive amount. Couple the refund/cancellation document instead.")


def refund_description(description: str | None) -> str | None:
    if not description:
        return description
    d = description.strip()
    if d.lower().startswith("refund"):
        d = re.sub(r"^refund\s*[-:–]?\s*", "", d, flags=re.IGNORECASE)
    return REFUND_PREFIX + d


# ---------------------------------------------------------------- svep-kortet


def bezala_fill(*, account: dict | None, category: str | None, texts: Iterable[str | None],
                card_description: str | None, card_amount: float | None,
                receipt_date: Any, vat_info: dict | None, flight_segments: Any,
                description: str | None) -> dict:
    """Allt Mikko klistrar in i Bezala: konto → moms → kostnadsställe →
    beskrivning, med en kopieringsbar text per fält."""
    texts = list(texts)
    vat = suggest_vat(category=category, texts=texts, card_description=card_description,
                      card_amount=card_amount, receipt_date=receipt_date, vat_info=vat_info,
                      flight_segments=flight_segments)
    desc = refund_description(description) if vat["refund"] else description
    acc = dict(account or {})
    flags = list(vat["flags"])
    if acc.get("missing"):
        flags.insert(0, acc.get("flag") or "Account missing")
    vat_text = vat["vat"]
    if vat_text == "Several rates":
        vat_text = "; ".join(f"{ln['vat']} {ln['amount']:.2f}" if ln.get("amount") is not None
                             else ln["vat"] for ln in vat["lines"])
    return {
        "account": acc.get("name"),
        "account_id": acc.get("account_id"),
        "account_source": acc.get("source"),
        "vat": vat["vat"],
        "vat_lines": vat["lines"],
        "vat_rule": vat["rule"],
        "cost_center": COST_CENTER_NAME,
        "description": desc,
        "refund": vat["refund"],
        "flags": flags,
        "copy": {
            "account": acc.get("name"),
            "vat": vat_text,
            "cost_center": COST_CENTER_NAME,
            "description": desc,
        },
    }
