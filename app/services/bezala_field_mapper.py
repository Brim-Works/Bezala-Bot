"""Bezala field-mapper — översätter Bezala Bot-interna värden till Bezalas
referensdata (konto-IDs, kostnadsställe-IDs, moms-IDs).

Pure functions, inga sidoeffekter. Client-koden hämtar metadata via
BezalaClient.list_accounts()/list_cost_centers()/list_vat_rates() och
feedar resultaten till funktionerna här.

Används INTE av pipeline än (Gate 0-groundwork). När live Bezala-respons
(422) är analyserad vet vi exakta fältnamn → pipelinen wire:as in
via create_transaction(extra_fields=...).

Mappning enligt spec:
  Flyg → Matkaliput
  Resa / Transport → Muut Matkakulut
  Programvara / AI → ATK-ohjelmistot, päivitykset ja yp
  Hotell / Boende → Hotelli-ym. majoitus
  default → Muut Matkakulut
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kategori → Bezala-konto (finska namn från spec)
# ---------------------------------------------------------------------------

DEFAULT_ACCOUNT_NAME = "Muut Matkakulut"

CATEGORY_TO_ACCOUNT: dict[str, str] = {
    # Resor & transport
    "flyg": "Matkaliput",
    "resa": "Muut Matkakulut",
    "transport": "Muut Matkakulut",
    "taxi": "Muut Matkakulut",
    "kollektivtrafik": "Muut Matkakulut",

    # Programvara / IT
    "programvara": "ATK-ohjelmistot, päivitykset ja yp",
    "ai": "ATK-ohjelmistot, päivitykset ja yp",
    "it": "ATK-ohjelmistot, päivitykset ja yp",
    "software": "ATK-ohjelmistot, päivitykset ja yp",

    # Boende
    "hotell": "Hotelli-ym. majoitus",
    "boende": "Hotelli-ym. majoitus",
    "hotel": "Hotelli-ym. majoitus",
}


def category_to_account_name(category: str | None) -> str:
    """Bezala Bot-kategori → Bezala-kontonamn (case-insensitive).
    Okänd / None / tom → default-konto ('Muut Matkakulut')."""
    if not category:
        return DEFAULT_ACCOUNT_NAME
    return CATEGORY_TO_ACCOUNT.get(category.strip().lower(), DEFAULT_ACCOUNT_NAME)


def select_account(
    accounts: Iterable[dict],
    category: str | None,
    *,
    name_keys: tuple[str, ...] = ("name", "title", "label"),
    id_keys: tuple[str, ...] = ("id", "account_id"),
) -> dict | None:
    """Hitta Bezala-konto-posten som matchar kategorin.

    Returnerar hela raden (så anroparen kan plocka ID + eventuellt code).
    Matchar substring-insensitivt för att tåla variation i Bezala-namnet
    (t.ex. 'ATK-ohjelmistot, päivitykset ja yp' kan kapa av i UI)."""
    target = category_to_account_name(category)
    target_lower = target.lower()
    best: dict | None = None
    best_score = -1
    for row in accounts:
        name = _first_string(row, name_keys)
        if not name:
            continue
        name_lower = name.lower()
        score = _match_score(name_lower, target_lower)
        if score > best_score:
            best_score = score
            best = row
    if best is None or best_score <= 0:
        logger.warning(
            "select_account: ingen match för kategori=%r (target=%r) bland %d konton",
            category, target, len(list(accounts)) if isinstance(accounts, list) else -1,
        )
        return None
    return best


def _first_string(row: dict, keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _match_score(candidate: str, target: str) -> int:
    """Heuristisk substring-match. Exakt match > börjar-med > innehåller."""
    if candidate == target:
        return 100
    if candidate.startswith(target) or target.startswith(candidate):
        return 80
    if target in candidate or candidate in target:
        return 60
    # Första ordet — hanterar 'ATK-ohjelmistot, päivitykset ja yp' vs 'ATK-ohjelmistot'
    first_target = target.split()[0]
    if first_target and (first_target in candidate):
        return 40
    return 0


# ---------------------------------------------------------------------------
# Land-detektion (för moms-regler)
# ---------------------------------------------------------------------------

# EU-medlemsstater per 2026 (TLDs). Norge, Storbritannien, Schweiz är
# explicit INTE EU → hamnar i "non-eu"-grinden.
_EU_TLDS: set[str] = {
    ".at", ".be", ".bg", ".hr", ".cy", ".cz", ".dk", ".ee", ".fi", ".fr",
    ".de", ".gr", ".hu", ".ie", ".it", ".lv", ".lt", ".lu", ".mt", ".nl",
    ".pl", ".pt", ".ro", ".sk", ".si", ".es", ".se",
}

# Kända leverantörer som .com men vi vet land — hardcoded overrides.
_VENDOR_COUNTRY_OVERRIDES: dict[str, str] = {
    "anthropic.com": "non-eu",  # US
    "mail.anthropic.com": "non-eu",
    "openai.com": "non-eu",
    "uber.com": "non-eu",
    "stripe.com": "non-eu",
    "github.com": "non-eu",
    "google.com": "non-eu",
    "microsoft.com": "non-eu",
    # Skandinaviska .com-avsändare vi sett
    "finnair.com": "fi",
    "scandichotels.com": "eu",  # Nordic chain, HQ DK/SE
}


def _extract_domain(sender: str | None) -> str | None:
    """Plockar ut 'finnair.com' från 'Finnair <receipts@finnair.com>' eller
    'receipts@finnair.com'. Returnerar None om inget matchar."""
    if not sender:
        return None
    s = sender.strip().lower()
    # Remove display-name part: "Finnair <...>" → "..."
    if "<" in s and ">" in s:
        s = s[s.index("<") + 1 : s.rindex(">")]
    if "@" in s:
        s = s.split("@", 1)[1]
    # Rensa subdomains av typ 'mail.anthropic.com' — behåll hela domänen,
    # eftersom vi slår upp på full domän först och sedan TLD.
    s = s.split(":")[0].strip().strip(".")
    return s or None


def sender_to_country(sender: str | None, vendor: str | None = None) -> str:
    """Returnerar 'fi' | 'eu' | 'non-eu' baserat på avsändar-domän.

    Prioritering:
      1. _VENDOR_COUNTRY_OVERRIDES (fullmatch på domän)
      2. TLD .fi → 'fi'
      3. TLD i EU-lista → 'eu'
      4. Allt annat → 'non-eu'
    """
    domain = _extract_domain(sender) or _extract_domain(vendor)
    if not domain:
        return "non-eu"

    if domain in _VENDOR_COUNTRY_OVERRIDES:
        return _VENDOR_COUNTRY_OVERRIDES[domain]

    # Testa även parent-domänen (mail.anthropic.com → anthropic.com)
    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        parent = ".".join(parts[i:])
        if parent in _VENDOR_COUNTRY_OVERRIDES:
            return _VENDOR_COUNTRY_OVERRIDES[parent]

    tld = "." + parts[-1] if parts else ""
    if tld == ".fi":
        return "fi"
    if tld in _EU_TLDS:
        return "eu"
    return "non-eu"


# ---------------------------------------------------------------------------
# Moms-val
# ---------------------------------------------------------------------------

# VAT-profilen per (country, category-group). Värdena är sökord som vi
# matchar mot Bezala vat_rate-posternas `name`/`description` när vi
# slår upp rätt rad via select_vat_rate().
TRANSPORT_CATEGORIES = {"flyg", "resa", "transport", "taxi", "kollektivtrafik"}

VAT_MATCH_HINTS: dict[tuple[str, str], tuple[str, ...]] = {
    ("fi", "transport"): ("13.5", "13,5"),
    ("fi", "standard"): ("25.5", "25,5"),
    ("eu", "standard"): ("purchases abroad (eu)", "eu"),
    ("non-eu", "standard"): ("purchases abroad (non-eu)", "non-eu", "non eu"),
}


def _category_group(category: str | None) -> str:
    if category and category.strip().lower() in TRANSPORT_CATEGORIES:
        return "transport"
    return "standard"


def select_vat_rate(
    vat_rates: Iterable[dict],
    *,
    country: str,
    category: str | None,
    name_keys: tuple[str, ...] = ("name", "description", "label", "title"),
) -> dict | None:
    """Välj rätt moms-rad ur Bezalas vat_rates. Matchar genom att söka
    efter VAT_MATCH_HINTS i post-namnet (case-insensitive)."""
    group = _category_group(category)
    hints = VAT_MATCH_HINTS.get((country, group))
    if not hints:
        # Fallback: "fi" + okänt → standard
        hints = VAT_MATCH_HINTS.get((country, "standard"), ())
    if not hints:
        return None

    for row in vat_rates:
        name = _first_string(row, name_keys)
        if not name:
            continue
        lower = name.lower()
        if any(h.lower() in lower for h in hints):
            return row
    return None


# ---------------------------------------------------------------------------
# Cost center — välj default
# ---------------------------------------------------------------------------

DEFAULT_COST_CENTER_NAME_ENV = "BEZALA_DEFAULT_COST_CENTER"


def select_default_cost_center(
    cost_centers: Iterable[dict],
    *,
    preferred_name: str | None = None,
    name_keys: tuple[str, ...] = ("name", "label", "title"),
) -> dict | None:
    """Välj kostnadsställe:
      1. Post där `default=true`
      2. Post vars namn matchar `preferred_name` (eller BEZALA_DEFAULT_COST_CENTER env)
      3. Första posten i listan
      4. None om listan är tom
    """
    rows = [r for r in cost_centers if isinstance(r, dict)]
    if not rows:
        return None

    for row in rows:
        if row.get("default") is True or row.get("is_default") is True:
            return row

    pref = preferred_name or os.environ.get(DEFAULT_COST_CENTER_NAME_ENV)
    if pref:
        pref_lower = pref.lower()
        for row in rows:
            name = _first_string(row, name_keys) or ""
            if pref_lower in name.lower():
                return row

    return rows[0]


# ---------------------------------------------------------------------------
# Beskrivning
# ---------------------------------------------------------------------------


def build_description(file_name: str | None, *, fallback: str | None = None) -> str:
    """Bezala 'Beskrivning'-fält: filnamn utan .pdf-ändelse."""
    if file_name:
        name = file_name.strip()
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        return name.strip()
    return (fallback or "").strip()


# ---------------------------------------------------------------------------
# High-level: bygg extras-dict för create_transaction
# ---------------------------------------------------------------------------


def build_transaction_extras(
    *,
    file_name: str | None,
    sender: str | None,
    vendor: str | None,
    category: str | None,
    receipt_date: str | None,
    accounts: list[dict],
    cost_centers: list[dict],
    vat_rates: list[dict],
    preferred_cost_center: str | None = None,
) -> dict:
    """Bygger en dict med de nya Bezala-fälten (account_id, cost_center_id,
    vat_rate_id, purchase_date, description).

    KEY-NAMNEN här är våra bästa gissningar baserat på spec — de justeras
    till Bezalas faktiska schema när 422-response är analyserad.

    Returnerar alltid en dict; värden som inte kunde mappas utelämnas
    (None) så create_transaction kan skicka vidare bara det vi har.
    """
    country = sender_to_country(sender, vendor)
    account = select_account(accounts, category)
    cost_center = select_default_cost_center(
        cost_centers, preferred_name=preferred_cost_center,
    )
    vat_rate = select_vat_rate(vat_rates, country=country, category=category)
    description = build_description(file_name)

    extras: dict = {}
    if account and (acc_id := account.get("id") or account.get("account_id")):
        extras["account_id"] = acc_id
    if cost_center and (cc_id := cost_center.get("id") or cost_center.get("cost_center_id")):
        extras["cost_center_id"] = cc_id
    if vat_rate and (vat_id := vat_rate.get("id") or vat_rate.get("vat_rate_id")):
        extras["vat_rate_id"] = vat_id
    if receipt_date:
        extras["purchase_date"] = receipt_date
    if description:
        extras["description"] = description

    logger.info(
        "bezala-mapper: country=%s category=%s → account=%s cost_center=%s vat_rate=%s",
        country, category,
        (account or {}).get("name"),
        (cost_center or {}).get("name"),
        (vat_rate or {}).get("name"),
    )
    return extras
