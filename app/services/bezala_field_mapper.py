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
from typing import Iterable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kategori → Bezala konto-ID (live-verifierade från produktionens metadata)
# ---------------------------------------------------------------------------

# Fallback-konto när kategorin är okänd eller inget matchar.
DEFAULT_ACCOUNT_ID = 67110  # Muut matkakulut

CATEGORY_TO_ACCOUNT_ID: dict[str, int] = {
    # Flyg & långresor → Matkaliput (7800)
    "flyg": 67100,
    "resa": 67100,
    "transport": 67100,
    "tåg": 67100,
    "tag": 67100,
    "buss": 67100,
    "kollektivtrafik": 67100,

    # Taxi → Taksikulut (7810)
    "taxi": 67101,

    # Parkering → Paikoituskulut (7850) — Moovy, p-hus m.fl.
    "parkering": 67113,
    "parking": 67113,

    # Hotell / boende → Hotelli-ym. majoitus (7820)
    "hotell": 67102,
    "hotel": 67102,
    "boende": 67102,

    # AI-tjänster → "AI työkalut" (dedikerad kontorad för Anthropic/OpenAI/etc.)
    "ai": 166648,

    # Allmän programvara / SaaS → Atk-ohjelmistot, päivitykset ja yp (7660)
    "programvara": 82612,
    "saas": 82612,
    "software": 82612,
    "it": 82612,

    # Representation / gäster → Edustuskulut (7990)
    "representation": 67097,

    # Mat på resa → Ruokailut matkalla (7830)
    "mat": 148404,
    "matkalla": 148404,

    # Kontorsmaterial → Toimistotarvikkeet (8620)
    "kontor": 67107,
    "kontorsmaterial": 67107,

    # Övrigt/Annat → Muut matkakulut (7860)
    "annat": 67110,
    "övrigt": 67110,
    "ovrigt": 67110,
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
    "resa": "Matkaliput",
    "transport": "Matkaliput",
    "taxi": "Taksikulut",
    "parkering": "Paikoituskulut",
    "hotell": "Hotelli-ym. majoitus",
    "boende": "Hotelli-ym. majoitus",
    "programvara": "Atk-ohjelmistot, päivitykset ja yp",
    "ai": "AI työkalut",
    "software": "Atk-ohjelmistot, päivitykset ja yp",
    "representation": "Edustuskulut",
    "mat": "Ruokailut matkalla",
    "kontor": "Toimistotarvikkeet",
    "annat": "Muut matkakulut",
}


def category_to_account_id(category: str | None) -> int:
    """Bezala Bot-kategori → Bezala konto-ID (case-insensitive).
    Okänd / None / tom → DEFAULT_ACCOUNT_ID (Muut matkakulut)."""
    if not category:
        return DEFAULT_ACCOUNT_ID
    return CATEGORY_TO_ACCOUNT_ID.get(category.strip().lower(), DEFAULT_ACCOUNT_ID)


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
# Bezala vat_code_id → tax_percentage-decimalsträng (live-verifierad från docs)
# ---------------------------------------------------------------------------

VAT_PERCENTAGE_BY_CODE: dict[int, str] = {
    1355: "0.255",  # Finland standard 25,5%
    864:  "0.14",   # Finland reducerad 14%
    1:    "0.0",    # 0% (representation / skattefritt)
}


def tax_percentage_for_vat_code(vat_code_id: int | str | None) -> str:
    """Slår upp tax_percentage-sträng från vat_code_id. Default '0.255'
    (FI standard) om okänt — Bezala kommer korrigera via default_vat_id
    på kontot om värdet är fel."""
    if vat_code_id is None:
        return "0.255"
    try:
        key = int(vat_code_id)
    except (TypeError, ValueError):
        return "0.255"
    return VAT_PERCENTAGE_BY_CODE.get(key, "0.255")


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
    account = select_account(accounts, category)
    cost_center = select_default_cost_center(
        cost_centers,
        preferred_name=preferred_cost_center,
        preferred_id=preferred_cost_center_id,
    )

    # Välj vat_rate för fallback om account.default_vat_id saknas
    vat_rate_fallback: dict | None = None
    if vat_rates and account and account.get("default_vat_id") is None:
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

    params: dict = {
        "description": build_description(
            file_name, vendor=vendor, subject=subject, receipt_date=receipt_date,
        ),
        "date": receipt_date,
        # Dessa tre används INTE i Bezala-payload (fältet finns inte längre
        # top-level) men sparas för logging + UI-visning.
        "amount": amount,
        "currency": currency,
        "vendor": vendor,
        "vat_lines_attributes": vat_lines_attributes,
    }
    if account:
        params["credit_account_id"] = account.get("id") or account.get("account_id")

    logger.info(
        "bezala-mapper(receipt): country=%s category=%s → "
        "credit_account=%s (id=%s default_vat_id=%s) cost_center=%s "
        "vat_lines_attributes=%d",
        country, category,
        (account or {}).get("name"),
        (account or {}).get("id"),
        (account or {}).get("default_vat_id"),
        (cost_center or {}).get("name"),
        len(vat_lines_attributes),
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
