"""Bezala field-mapper — översätter Bezala Bot-interna värden till Bezalas
referensdata (konto-IDs, kostnadsställe-IDs, moms-IDs).

Pure functions, inga sidoeffekter. Client-koden hämtar metadata via
BezalaClient.list_accounts()/list_cost_centers()/list_vat_rates() och
feedar resultaten till funktionerna här.

Mappning baserad på LIVE Bezala-data från produktionens
GET /api/bezala/metadata (se DEFAULT_ACCOUNT_ID-kommentaren).

VAT-strategi: Bezala-konton bär default_vat_id. Vi läser det från
account-raden istället för att slå upp separat vat_rates-lista. Om
null → vat_lines utelämnas (Bezala väljer själv).
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kategori → Bezala konto-ID
# ---------------------------------------------------------------------------
#
# Bezala-konto-IDs är live-verifierade från produktionens metadata-endpoint
# (GET /api/bezala/metadata). Nya kategorier som lagts till efter den
# initiala verifieringen har None-värden tills Mikko bekräftar mot
# `scripts/list_bezala_metadata.py`. Lookup-funktionen `get_account_id_for_category`
# faller tillbaka till "annat"-kontot när None påträffas.

# Fallback-konto när kategorin är okänd eller inte är mappad ännu.
DEFAULT_ACCOUNT_ID = 67110  # Muut matkakulut


# Mapper för svenska → finska Bezala-kontonamn (referens i kommentarer).
# Källa: Mikkos facit + Bezala-prod-metadata.
CATEGORY_TO_ACCOUNT_ID: dict[str, int | None] = {
    # --- Resekategorier (alla mappar mot Matkaliput utom Taxi/Hotell/Mat) ---
    "flyg": 67100,                   # Matkaliput — verifierat
    "tåg": 67100,                    # Matkaliput — verifierat (delar konto)
    "tag": 67100,                    # alias utan diakritik
    "kollektivtrafik": 67100,        # Matkaliput — verifierat
    "buss": 67100,                   # alias → Matkaliput
    "transport": 67100,              # alias → Matkaliput
    "resa": 67100,                   # alias → Matkaliput
    "taxi": 67101,                   # Taksikulut — verifierat
    "bilhyra": 67110,                # Muut matkakulut — verifierat (default_vat_id=864). OBS: ingen dedikerad konto för bilhyra i Mikkos Bezala-instans.
    "parkering": 67113,              # Paikoituskulut — verifierat (Moovy, EasyPark)
    "parking": 67113,                # alias
    "pysakointi": 67113,             # finsk alias
    "hotell": 67102,                 # Hotelli- ym. majoitus — verifierat
    "hotel": 67102,                  # alias
    "boende": 67102,                 # alias
    "mat": 148404,                   # Ruokailut matkalla — verifierat
    "matkalla": 148404,              # alias

    # --- IT / Mjukvara ---
    "ai-verktyg": 166648,            # AI työkalut — verifierat (Anthropic/OpenAI etc.)
    "ai": 166648,                    # alias för bakåtkompatibilitet
    "mjukvara": 82612,               # Atk-ohjelmistot, päivitykset ja yp — verifierat
    "programvara": 82612,            # alias
    "saas": 82612,                   # alias
    "software": 82612,               # alias
    "it": 82612,                     # alias
    "telefon": 67109,                # Puhelinkulut — verifierat (default_vat_id=864)
    "puhelin": 67109,                # finsk alias
    "datakommunikation": 67106,      # Datasiirtokulut — verifierat (default_vat_id=864)
    "tiedonsiirto": 67106,           # finsk alias

    # --- Övrigt ---
    "böcker": 67085,                 # Ammattikirjallisuus, lehdet, kirjat — verifierat (default_vat_id=1355)
    "bocker": 67085,                 # alias utan diakritik
    "kirjat": 67085,                 # finsk alias
    "utbildning": 67086,             # Henkilökunnan koulutus — verifierat (default_vat_id=null)
    "koulutus": 67086,               # finsk alias
    "representation": 67097,         # Edustuskulut — verifierat
    "kontorsmaterial": 67107,        # Toimistotarvikkeet — verifierat
    "kontor": 67107,                 # alias

    # --- Fallback ---
    "annat": 67110,                  # Muut matkakulut — verifierat
    "övrigt": 67110,                 # alias
    "ovrigt": 67110,                 # alias utan diakritik
    "muut": 67110,                   # finsk alias
}

# Env-override för default-kostnadsställe (så olika användare kan ha olika).
# Produktions-default: VIS128 Visma HRM Sverige AB (Mikkos enhet).
DEFAULT_COST_CENTER_ID = int(
    os.environ.get("BEZALA_DEFAULT_COST_CENTER_ID", "927151")
)

# Behålls bakåtkompatibelt — mapper använder ID-direkt nu, men gamla kallare
# och tester som importerar namn-konstanten fortsätter fungera.
DEFAULT_ACCOUNT_NAME = "Muut matkakulut"

CATEGORY_TO_ACCOUNT: dict[str, str] = {
    "flyg": "Matkaliput",
    "tåg": "Matkaliput",
    "tag": "Matkaliput",
    "kollektivtrafik": "Matkaliput",
    "buss": "Matkaliput",
    "resa": "Matkaliput",
    "transport": "Matkaliput",
    "taxi": "Taksikulut",
    "bilhyra": "Muut matkakulut",
    "parkering": "Paikoituskulut",
    "parking": "Paikoituskulut",
    "hotell": "Hotelli-ym. majoitus",
    "hotel": "Hotelli-ym. majoitus",
    "boende": "Hotelli-ym. majoitus",
    "mat": "Ruokailut matkalla",
    "matkalla": "Ruokailut matkalla",
    "ai-verktyg": "AI työkalut",
    "ai": "AI työkalut",
    "mjukvara": "Atk-ohjelmistot, päivitykset ja yp",
    "programvara": "Atk-ohjelmistot, päivitykset ja yp",
    "software": "Atk-ohjelmistot, päivitykset ja yp",
    "saas": "Atk-ohjelmistot, päivitykset ja yp",
    "it": "Atk-ohjelmistot, päivitykset ja yp",
    "telefon": "Puhelinkulut",
    "datakommunikation": "Datasiirtokulut",
    "böcker": "Ammattikirjallisuus, lehdet, kirjat",
    "bocker": "Ammattikirjallisuus, lehdet, kirjat",
    "utbildning": "Henkilökunnan koulutus",
    "representation": "Edustuskulut",
    "kontorsmaterial": "Toimistotarvikkeet",
    "kontor": "Toimistotarvikkeet",
    "annat": "Muut matkakulut",
    "övrigt": "Muut matkakulut",
    "ovrigt": "Muut matkakulut",
}


def _normalize_category_key(category: str | None) -> str | None:
    """Normalisera kategori-strängen för uppslagning i CATEGORY_TO_ACCOUNT_ID.

    Returnerar lowercase + trimmad sträng. None om tom input.
    Bibehåller diakritik (ÅÄÖ) — tabellen har egna alias för icke-diakritik.
    """
    if not category:
        return None
    s = str(category).strip().lower()
    return s or None


def get_account_id_for_category(category: str | None) -> int:
    """FAS 11.x — public lookup för AI-kategori → Bezala konto-ID.

    Beteende:
      1. Normaliserar (lowercase, strip) — accepterar bl.a. 'Parkering'
         och 'parkering' likvärdigt.
      2. Slår upp i CATEGORY_TO_ACCOUNT_ID. Mappar har None för kategorier
         som inte är verifierade mot prod-Bezala ännu.
      3. Om värdet är None eller saknas: faller tillbaka till
         DEFAULT_ACCOUNT_ID (Muut matkakulut) och loggar en INFO-rad
         så vi kan tracka i prod-loggar vilka mappningar som saknas.

    Garanterar att returvärdet alltid är ett giltigt int — kallaren
    behöver inte handskas med None.
    """
    key = _normalize_category_key(category)
    if not key:
        return DEFAULT_ACCOUNT_ID
    if key not in CATEGORY_TO_ACCOUNT_ID:
        logger.info(
            "category_to_account_id: okänd kategori=%r, använder fallback %d",
            category, DEFAULT_ACCOUNT_ID,
        )
        return DEFAULT_ACCOUNT_ID
    mapped = CATEGORY_TO_ACCOUNT_ID[key]
    if mapped is None:
        logger.info(
            "category_to_account_id: kategori=%r saknar verifierat konto-ID — "
            "använder fallback %d (Muut matkakulut). Mikko: kör "
            "scripts/list_bezala_metadata.py för att fylla i.",
            category, DEFAULT_ACCOUNT_ID,
        )
        return DEFAULT_ACCOUNT_ID
    return mapped


def category_to_account_id(category: str | None) -> int:
    """Bakåtkompatibel wrapper. Ny kod ska kalla
    `get_account_id_for_category` direkt."""
    return get_account_id_for_category(category)


def category_to_account_name(category: str | None) -> str:
    """Behålls bakåtkompatibelt — returnerar svenskt/finskt kontonamn."""
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

    Lookup-ordning:
      1. Direkt ID-match via CATEGORY_TO_ACCOUNT_ID (primärt)
      2. Namn-substring-match mot CATEGORY_TO_ACCOUNT (fallback om Bezala
         skulle byta ID för ett konto, sällsynt men möjligt)

    Returnerar hela account-raden (innehåller default_vat_id som
    build_vat_lines behöver)."""
    target_id = category_to_account_id(category)
    accounts_list = list(accounts)

    # 1. ID-match (primärt — snabbt och robust)
    for row in accounts_list:
        for key in id_keys:
            if row.get(key) == target_id:
                return row

    # 2. Namn-match (fallback)
    target_name = category_to_account_name(category).lower()
    best: dict | None = None
    best_score = -1
    for row in accounts_list:
        name = _first_string(row, name_keys)
        if not name:
            continue
        score = _match_score(name.lower(), target_name)
        if score > best_score:
            best_score = score
            best = row
    if best is None or best_score <= 0:
        logger.warning(
            "select_account: ingen match för kategori=%r (target_id=%d, target_name=%r) bland %d konton",
            category, target_id, target_name, len(accounts_list),
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
    preferred_id: int | None = None,
    name_keys: tuple[str, ...] = ("name", "label", "title"),
    id_keys: tuple[str, ...] = ("id", "cost_center_id"),
) -> dict | None:
    """Välj kostnadsställe:
      1. Post där `default=true`
      2. Post vars ID matchar `preferred_id` (eller modulens DEFAULT_COST_CENTER_ID)
      3. Post vars namn matchar `preferred_name` (eller BEZALA_DEFAULT_COST_CENTER env)
      4. Första posten i listan
      5. None om listan är tom
    """
    rows = [r for r in cost_centers if isinstance(r, dict)]
    if not rows:
        return None

    for row in rows:
        if row.get("default") is True or row.get("is_default") is True:
            return row

    target_id = preferred_id if preferred_id is not None else DEFAULT_COST_CENTER_ID
    for row in rows:
        for key in id_keys:
            if row.get(key) == target_id:
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


def build_description(
    file_name: str | None,
    *,
    vendor: str | None = None,
    subject: str | None = None,
    receipt_date: str | None = None,
    fallback: str | None = None,
) -> str:
    """Bezala 'Beskrivning'-fält — ALDRIG tom sträng.

    Försöker i ordning:
      1. file_name utan .pdf (primär källa)
      2. subject (mailets ämnesrad)
      3. f"{vendor} {receipt_date}"
      4. fallback
      5. "Kvitto"  — sista utväg, Bezala kräver icke-tomt."""
    if file_name:
        name = file_name.strip()
        if name.lower().endswith(".pdf"):
            name = name[:-4]
        name = name.strip()
        if name:
            return name
    if subject:
        s = subject.strip()
        if s:
            return s
    if vendor and receipt_date:
        combo = f"{vendor.strip()} {receipt_date.strip()}".strip()
        if combo:
            return combo
    if vendor:
        v = vendor.strip()
        if v:
            return v
    if fallback:
        f = fallback.strip()
        if f:
            return f
    return "Kvitto"


def build_vat_lines(
    amount: float | None,
    source: dict | None = None,
    *,
    account: dict | None = None,
    vat_rate: dict | None = None,
) -> list[dict]:
    """Legacy-helper: returnerar enkel [{amount, vat_code_id}]-form.
    Behålls för bakåtkompatibla tester. Ny kod ska använda
    build_vat_lines_attributes."""
    if amount is None:
        return []
    if source is not None and account is None and vat_rate is None:
        if "default_vat_id" in source:
            account = source
        else:
            vat_rate = source
    vat_id: int | str | None = None
    if account is not None:
        vat_id = account.get("default_vat_id")
    if vat_id is None and vat_rate is not None:
        vat_id = (
            vat_rate.get("vat_code_id")
            or vat_rate.get("vat_rate_id")
            or vat_rate.get("id")
        )
    if vat_id is None:
        return []
    return [{"amount": amount, "vat_code_id": vat_id}]


# ---------------------------------------------------------------------------
# Bezala vat_code_id → tax_percentage-decimalsträng
# ---------------------------------------------------------------------------
#
# Verifierade FI-koder (live från prod): 1355, 864, 1.
# Resterande IDs är platshållare (None) tills Mikko kör
# `scripts/list_bezala_metadata.py` mot prod-Bezala och fyller i.
# `tax_percentage_for_vat_code` faller tillbaka till country-baserat
# default via `get_default_vat_for_country` när None påträffas.
#
# Korrekta dec-strängar att använda när IDs är kända:
#   FI: 25.5% → "0.255", 14% → "0.14", 10% → "0.10", 0% → "0.0"
#   SE: 25%   → "0.25",  12% → "0.12", 6%  → "0.06", 0% → "0.0"
#   NO: 25%   → "0.25",  15% → "0.15", 12% → "0.12", 0% → "0.0"
#   Purchases Abroad (EU/Non-EU): "0.0"

# `None` = okänt prod-ID; fylls i efter diagnostik. `str` = verifierat.
#
# Status efter Mikkos prod-dump (2026-05-11):
#   Bezalas GET /api/vat_rates returnerar tom array → vi har inga
#   explicita SE/NO-VAT-koder att lägga till. De aktuella koder som
#   används i prod är de tre nedan (sett via account.default_vat_id).
#   SE/NO-flöden förlitar sig på `get_default_vat_for_country`-fallback
#   tills Bezala börjar exponera vat_rates-listan eller Mikko kan
#   gräva fram IDs ur Bezala-UI:t.
VAT_PERCENTAGE_BY_CODE: dict[int, str | None] = {
    # --- Finland — VERIFIERADE via account.default_vat_id ---
    1355: "0.255",   # FI standard 25,5% (efter sep 2024)
    864:  "0.14",    # FI reducerad 14% (livsmedel, restaurang)
    1:    "0.0",     # FI 0% (representation / skattefritt)
    # --- Finland 10% reducerad: ID okänt — vat_rates-endpoint tom ---
    # --- Sverige / Norge: ID okänt — country-fallback aktiv via
    #     get_default_vat_for_country() i tax_percentage_for_vat_code ---
}


# ---------------------------------------------------------------------------
# Country → default VAT-procent
# ---------------------------------------------------------------------------
#
# Fallback när vi har country-detektion men inget vat_code_id från Bezala.
# Används också av `tax_percentage_for_vat_code` när uppslagen kod är None
# eller saknas.

COUNTRY_DEFAULT_VAT: dict[str, str] = {
    "fi": "0.255",       # FI standard 25,5%
    "se": "0.25",        # SE standard 25%
    "no": "0.25",        # NO standard 25%
    "dk": "0.25",        # DK standard 25%
    "ee": "0.22",        # EE standard 22%
    "lv": "0.21",        # LV standard 21%
    "lt": "0.21",        # LT standard 21%
    "de": "0.19",        # DE standard 19%
    "nl": "0.21",        # NL standard 21%
    "fr": "0.20",        # FR standard 20%
    "eu_other": "0.0",   # Purchases Abroad (EU)
    "non_eu": "0.0",     # Purchases Abroad (Non-EU)
}

# Mappning från `sender_to_country()`-värden till COUNTRY_DEFAULT_VAT-nycklar.
# sender_to_country returnerar 'fi' | 'eu' | 'non-eu' — vi översätter till
# 'fi' / 'eu_other' / 'non_eu'.
_SENDER_COUNTRY_TO_VAT_KEY: dict[str, str] = {
    "fi": "fi",
    "eu": "eu_other",
    "non-eu": "non_eu",
}


def get_default_vat_for_country(country_code: str | None) -> str:
    """Returnera default tax_percentage-sträng för ett land.

    Accepterar både:
      - ISO-koder 'fi', 'se', 'no', 'dk' etc.
      - `sender_to_country`-värden 'fi', 'eu', 'non-eu'.

    Okänt land → 'fi'-default (Mikkos primära land) snarare än '0.0',
    eftersom 0% skulle skicka 0-moms till Bezala vilket är farligare än
    en hög default.
    """
    if not country_code:
        return COUNTRY_DEFAULT_VAT["fi"]
    key = str(country_code).strip().lower()
    if key in _SENDER_COUNTRY_TO_VAT_KEY:
        key = _SENDER_COUNTRY_TO_VAT_KEY[key]
    return COUNTRY_DEFAULT_VAT.get(key, COUNTRY_DEFAULT_VAT["fi"])


# credit_account_id = BETALNINGSMETODEN (kreditkort), inte utgiftskategorin.
# Rotorsak till 422 'credit_account måste finnas': vi skickade kategori-kontot
# (67100 Matkaliput) som credit_account, men Bezala förväntade sig ett
# payment-account (kreditkort). Mikkos kreditkort = 82320.
# expense_account_id i vat_lines_attributes är däremot utgiftskategorin
# (Flyg→67100, Hotell→67102, etc.) och kommer från select_account().
DEFAULT_CREDIT_ACCOUNT_ID = int(
    os.environ.get("BEZALA_CREDIT_ACCOUNT_ID", "82320")
)


def tax_percentage_for_vat_code(
    vat_code_id: int | str | None,
    *,
    country: str | None = None,
) -> str:
    """Slår upp tax_percentage-sträng från vat_code_id.

    Fallback-ordning:
      1. Direkt match i VAT_PERCENTAGE_BY_CODE → använd det värdet.
      2. Match men värdet är None (placeholder för icke-verifierat ID) →
         country-baserat default.
      3. Ingen match alls (okänt ID) → country-baserat default.
      4. Inget country angivet → FI standard (25,5%) som sista fallback.

    `country` kan vara ISO-kod ('fi'/'se') eller `sender_to_country`-värde
    ('fi'/'eu'/'non-eu').
    """
    fallback = (
        get_default_vat_for_country(country) if country
        else COUNTRY_DEFAULT_VAT["fi"]
    )
    if vat_code_id is None:
        return fallback
    try:
        key = int(vat_code_id)
    except (TypeError, ValueError):
        return fallback
    mapped = VAT_PERCENTAGE_BY_CODE.get(key)
    if mapped is None:
        # Antingen okänt ID, eller känt-men-icke-verifierat (None i tabellen)
        if key not in VAT_PERCENTAGE_BY_CODE:
            logger.info(
                "tax_percentage_for_vat_code: okänt vat_code_id=%s, "
                "använder country-default %s (country=%r)",
                key, fallback, country,
            )
        return fallback
    return mapped


def build_vat_lines_attributes(
    *,
    amount: float | None,
    currency: str | None,
    account: dict | None,
    cost_center: dict | None,
    vat_rate: dict | None = None,
) -> list[dict]:
    """Bygg Bezalas vat_lines_attributes[] enligt senaste API-docs.

    Format per rad:
      {
        "taxable": "577.50",          # string med 2 decimaler
        "tax_percentage": "0.255",    # decimal-sträng
        "currency": "EUR",
        "expense_account_id": 67100,
        "cost_center_ids": [927151],
        "vat_code_id": 1355
      }

    account.default_vat_id prioriteras. Om account saknar default_vat_id
    OCH vat_rate har ett ID → använd det. Annars returneras []."""
    if amount is None or account is None:
        return []

    vat_id: int | str | None = account.get("default_vat_id")
    if vat_id is None and vat_rate is not None:
        vat_id = (
            vat_rate.get("vat_code_id")
            or vat_rate.get("vat_rate_id")
            or vat_rate.get("id")
        )
    if vat_id is None:
        return []

    account_id = account.get("id") or account.get("account_id")
    if account_id is None:
        return []

    entry: dict = {
        "taxable": f"{float(amount):.2f}",
        "tax_percentage": tax_percentage_for_vat_code(vat_id),
        "currency": currency or "EUR",
        "expense_account_id": account_id,
        "vat_code_id": vat_id,
    }
    if cost_center is not None:
        cc_id = cost_center.get("id") or cost_center.get("cost_center_id")
        if cc_id is not None:
            entry["cost_center_ids"] = [cc_id]
    return [entry]


def _mapping_attr(mapping: Any, key: str) -> Any:
    """Plocka ut attribut/key från ett mapping-objekt. Funkar både för
    SQLAlchemy-rader (BezalaVendorMapping) och dict-likt-objekt — så
    bezala_field_mapper kan förbli pure (utan ORM-import)."""
    if mapping is None:
        return None
    if isinstance(mapping, dict):
        return mapping.get(key)
    return getattr(mapping, key, None)


def find_vendor_mapping(
    vendor: str | None, mappings: Iterable[Any] | None,
) -> Any | None:
    """Substring-match (case-insensitive) vendor mot vendor_pattern. Första
    träffen vinner — anropare ska sortera om de vill ha deterministisk
    prioritering."""
    if not vendor or not mappings:
        return None
    needle = vendor.lower()
    for m in mappings:
        pattern = _mapping_attr(m, "vendor_pattern")
        if pattern and str(pattern).lower() in needle:
            return m
    return None


def _rate_to_decimal_string(rate: Any) -> str | None:
    """Översätt en VAT-procent (t.ex. Decimal('25.50')) till Bezalas
    tax_percentage-format (t.ex. '0.255'). Returnerar None om input
    inte är numerisk."""
    if rate is None:
        return None
    try:
        d = Decimal(str(rate)) / Decimal("100")
    except (InvalidOperation, ValueError):
        return None
    normalized = d.normalize()
    s = format(normalized, "f")
    if "." not in s:
        s += ".0"
    return s


def _vat_code_for_rate(rate: Any) -> int | None:
    """Reverse-lookup: vilket vat_code_id matchar den här procenten?
    Använder VAT_PERCENTAGE_BY_CODE för FI-verifierade koder. Returnerar
    None om ingen match (då faller vi tillbaka på account.default_vat_id)."""
    target_str = _rate_to_decimal_string(rate)
    if target_str is None:
        return None
    target = Decimal(target_str)
    for code, pct_str in VAT_PERCENTAGE_BY_CODE.items():
        if pct_str is None:
            continue
        try:
            if Decimal(pct_str) == target:
                return code
        except InvalidOperation:
            continue
    return None


def _find_account_by_id(accounts: Iterable[dict], target_id: int) -> dict | None:
    for row in accounts or ():
        if not isinstance(row, dict):
            continue
        if row.get("id") == target_id or row.get("account_id") == target_id:
            return row
    return None


def build_receipt_params(
    *,
    file_name: str | None,
    sender: str | None,
    vendor: str | None,
    category: str | None,
    amount: float | None,
    currency: str | None,
    receipt_date: str | None,
    subject: str | None = None,
    accounts: list[dict],
    cost_centers: list[dict],
    vat_rates: list[dict] | None = None,  # valfri — default_vat_id från account föredras
    preferred_cost_center: str | None = None,
    preferred_cost_center_id: int | None = None,
    description_override: str | None = None,
    vendor_mappings: Iterable[Any] | None = None,
) -> dict:
    """Bygger en komplett kwargs-dict för BezalaClient.upload_receipt().

    Nya Bezala-fältnamn (från API-docs):
      description, date                   — top-level
      credit_account_id                   — account id (tidigare 'account_id')
      vat_lines_attributes[]              — array med taxable (string),
                                             tax_percentage (decimal-string),
                                             currency, expense_account_id,
                                             cost_center_ids (array), vat_code_id

    Returnerar dict med: description, date, credit_account_id,
    vat_lines_attributes. Dessutom amount/currency/vendor för logging/UI
    (inte skickade till Bezala men användbara för UI-toast)."""
    country = sender_to_country(sender, vendor)
    mapping = find_vendor_mapping(vendor, vendor_mappings)

    if mapping is not None:
        forced_account_id = _mapping_attr(mapping, "bezala_account_id")
        account = _find_account_by_id(accounts, forced_account_id) or {
            "id": forced_account_id,
            "name": None,
            "default_vat_id": None,
        }
    else:
        account = select_account(accounts, category)

    cost_center = select_default_cost_center(
        cost_centers,
        preferred_name=preferred_cost_center,
        preferred_id=preferred_cost_center_id,
    )

    # Välj vat_rate för fallback om account.default_vat_id saknas.
    # Mapping forcerar sin egen rate efter att linjerna byggts, så vi
    # behöver inte slå upp Bezalas vat_rates-lista när mapping finns.
    vat_rate_fallback: dict | None = None
    if mapping is None and vat_rates and account and account.get("default_vat_id") is None:
        vat_rate_fallback = select_vat_rate(
            vat_rates, country=country, category=category,
        )

    vat_lines_attributes = build_vat_lines_attributes(
        amount=amount,
        currency=currency,
        account=account,
        cost_center=cost_center,
        vat_rate=vat_rate_fallback,
    )

    if mapping is not None:
        mapped_rate = _mapping_attr(mapping, "vat_rate")
        mapped_tax_pct = _rate_to_decimal_string(mapped_rate)
        mapped_vat_code_id = _vat_code_for_rate(mapped_rate)
        if not vat_lines_attributes and amount is not None:
            entry: dict = {
                "taxable": f"{float(amount):.2f}",
                "tax_percentage": mapped_tax_pct or "0.0",
                "currency": currency or "EUR",
                "expense_account_id": forced_account_id,
            }
            if mapped_vat_code_id is not None:
                entry["vat_code_id"] = mapped_vat_code_id
            if cost_center is not None:
                cc_id = cost_center.get("id") or cost_center.get("cost_center_id")
                if cc_id is not None:
                    entry["cost_center_ids"] = [cc_id]
            vat_lines_attributes = [entry]
        else:
            for entry in vat_lines_attributes:
                if mapped_tax_pct is not None:
                    entry["tax_percentage"] = mapped_tax_pct
                entry["expense_account_id"] = forced_account_id
                if mapped_vat_code_id is not None:
                    entry["vat_code_id"] = mapped_vat_code_id

    # Description-prioritet:
    #   1. mapping.description_override (bezala_vendor_mappings)
    #   2. description_override (ai_description_en eller row.summary)
    #   3. build_description(file_name, ...)
    mapping_desc_raw = _mapping_attr(mapping, "description_override")
    mapping_desc = (mapping_desc_raw or "").strip() if mapping_desc_raw else ""
    caller_override = (description_override or "").strip() if description_override else ""
    description = mapping_desc or caller_override or build_description(
        file_name, vendor=vendor, subject=subject, receipt_date=receipt_date,
    )
    params: dict = {
        "description": description,
        "date": receipt_date,
        # Dessa tre används INTE i Bezala-payload (fältet finns inte längre
        # top-level) men sparas för logging + UI-visning.
        "amount": amount,
        "currency": currency,
        "vendor": vendor,
        "vat_lines_attributes": vat_lines_attributes,
        # credit_account_id = betalningsmetod (kreditkort), inte kategori.
        # Hårdkodad default från env (kan överstyras per användare).
        "credit_account_id": DEFAULT_CREDIT_ACCOUNT_ID,
    }

    logger.info(
        "bezala-mapper(receipt): country=%s category=%s → "
        "expense_account=%s (id=%s default_vat_id=%s) cost_center=%s "
        "credit_account_id=%s vat_lines_attributes=%d",
        country, category,
        (account or {}).get("name"),
        (account or {}).get("id"),
        (account or {}).get("default_vat_id"),
        (cost_center or {}).get("name"),
        DEFAULT_CREDIT_ACCOUNT_ID,
        len(vat_lines_attributes),
    )
    if mapping is not None:
        logger.info(
            "Applied vendor mapping: %s → account %s, VAT %s%%",
            _mapping_attr(mapping, "vendor_pattern"),
            _mapping_attr(mapping, "bezala_account_id"),
            _mapping_attr(mapping, "vat_rate"),
        )
    return params


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
