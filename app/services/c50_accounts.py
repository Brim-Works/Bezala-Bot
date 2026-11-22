"""C50 Del 1 — kompletta utkast: konto per leverantör + beskrivningens datum.

Kontoförslag (gissar aldrig):
1. Mikkos fasta regel: AI-verktyg (Cursor, Lovable, Anthropic/Claude,
   Bolt/StackBlitz, OpenAI/ChatGPT, Midjourney) → "AI työkalut".
2. Annars: konto, momskod och kostnadsställe från Mikkos SENASTE egna utlägg
   för SAMMA leverantör i Bezala (C52: bara samma leverantör).
3. Annars: Mikkos kontoregler (resor, parkering, taxi, hotell, bil).
4. Annars: inget konto → flagga "Account missing" i svep-kön.

Metadata skrivs INTE till Bezala (Del 0: PUT är av) — förslaget visas i
svep-kön så Mikko sätter det själv.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime
from typing import Any, Iterable

logger = logging.getLogger(__name__)

AI_ACCOUNT_NAME = "AI työkalut"
# Verifierat konto-id (bezala_field_mapper.CATEGORY_TO_ACCOUNT_ID["ai-verktyg"]).
AI_ACCOUNT_ID = 166648
ACCOUNT_MISSING = "Account missing"

AI_TOOL_WORDS = (
    "cursor", "anysphere", "lovable", "anthropic", "claude", "bolt.new",
    "stackblitz", "openai", "chatgpt", "midjourney",
)
# "bolt" ensamt = även Bolt-taxi; räknas bara med StackBlitz-kontext.
_BOLT_AI_RE = re.compile(r"\bbolt(\.new| new| pro)\b", re.IGNORECASE)


def is_ai_tool(*texts: str | None) -> bool:
    hay = " ".join((t or "").lower() for t in texts)
    if not hay.strip():
        return False
    return any(w in hay for w in AI_TOOL_WORDS) or bool(_BOLT_AI_RE.search(hay))


# ---------------------------------------------------------------- cache

_CACHE: dict[str, Any] = {"accounts": [], "transactions": [], "at": 0.0}


def set_reference(accounts: list[dict] | None, transactions: list[dict] | None) -> None:
    _CACHE["accounts"] = list(accounts or [])
    _CACHE["transactions"] = list(transactions or [])
    _CACHE["at"] = time.time()


def reference() -> tuple[list[dict], list[dict]]:
    return _CACHE["accounts"], _CACHE["transactions"]


def refresh_reference(client=None) -> dict:
    """Läs Bezalas kontolista + Mikkos egna utlägg (endast läsning)."""
    close = False
    try:
        if client is None:
            from app.services.bezala_client import BezalaClient

            client = BezalaClient()
            close = True
        accounts, txs = [], []
        try:
            accounts = client.list_accounts() or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("C50: kontolistan kunde inte läsas: %s", exc)
        try:
            txs = client.list_transactions() or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("C50: utläggen kunde inte läsas: %s", exc)
        set_reference(accounts or _CACHE["accounts"], txs or _CACHE["transactions"])
        return {"accounts": len(_CACHE["accounts"]), "transactions": len(_CACHE["transactions"])}
    finally:
        if close:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- konton


def visible_accounts(accounts: Iterable[dict]) -> list[dict]:
    """Samma konton som Bezala visar i utläggsformuläret: aktiva, med namn."""
    out = []
    for a in accounts or []:
        if not isinstance(a, dict) or not a.get("name"):
            continue
        if a.get("active") is False or a.get("archived") or a.get("hidden") \
                or a.get("deleted_at") or a.get("disabled"):
            continue
        out.append(a)
    return out


def account_by_name(accounts: Iterable[dict], name: str) -> dict | None:
    want = (name or "").strip().casefold()
    for a in accounts or []:
        if str(a.get("name") or "").strip().casefold() == want:
            return a
    return None


def account_by_id(accounts: Iterable[dict], acc_id) -> dict | None:
    for a in accounts or []:
        if str(a.get("id")) == str(acc_id):
            return a
    return None


def _tx_date(tx: dict) -> str:
    return str(tx.get("date") or tx.get("created_at") or "")


def _tx_lines(tx: dict) -> list[dict]:
    for k in ("vat_lines", "vat_lines_attributes", "lines"):
        v = tx.get(k)
        if isinstance(v, list) and v:
            return [x for x in v if isinstance(x, dict)]
    return []


# C52 — Mikkos kontoregler per leverantör/typ (gäller när det inte finns ett
# utlägg från SAMMA leverantör). Ordningen spelar roll: AI-verktyg hanteras
# före (Bolt.new), Bolt-taxi fångas av taxiregeln.
ACCOUNT_TICKETS = "Matkaliput"
ACCOUNT_PARKING = "Paikoituskulut"
ACCOUNT_TAXI = "Taksikulut"
ACCOUNT_HOTEL = "Hotelli- ym. majoitus"
ACCOUNT_CAR = "Muut ajoneuvokulut"
# Får aldrig föreslås (Mikko 2026-11-22).
NEVER_SUGGEST = frozenset({"tiliöimättömät ostolaskut"})

VENDOR_RULES: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"car ?wash|biltvätt|autopesu|tjänstebil|company car|työsuhdeauto", re.I),
     ACCOUNT_CAR),
    (re.compile(r"moovy|parking|parkering|pysäköinti|easypark|aimo ?park|europark|q-park|apcoa",
                re.I), ACCOUNT_PARKING),
    (re.compile(r"\btaxi|\btaksi|\buber\b|\bbolt\b|cabonline|yango|lähitaksi|taxi ?kurir",
                re.I), ACCOUNT_TAXI),
    (re.compile(r"flytoget|sk[aå]netrafiken|arlanda ?express|finnair|\bsas\b|norwegian|"
                r"ryanair|lufthansa|airbaltic|\bklm\b|\bvy\b|\bsj\b|\bvr\b|flixbus|"
                r"onnibus|v[aä]sttrafik|ruter|\bhsl\b|airline|\bflight|\btrain\b|"
                r"\bbus\b|\bt[aå]g\b", re.I), ACCOUNT_TICKETS),
    (re.compile(r"hotel|hotelli|scandic|clarion|radisson|hilton|marriott|sokos|\belite\b|"
                r"comfort hotel|quality hotel|best western|nordic choice|strawberry|"
                r"booking\.com|\bhostel", re.I), ACCOUNT_HOTEL),
)

_GENERIC_VENDOR_WORDS = frozenset({
    "the", "www", "com", "oy", "oyj", "ab", "as", "asa", "ltd", "inc", "llc", "gmbh",
    "mikko", "keinonen", "receipt", "invoice", "kuitti", "kvitto", "payment", "app",
})


def _norm(text: str | None) -> str:
    import unicodedata

    t = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in t if not unicodedata.combining(ch))


def vendor_key(vendor: str | None) -> str | None:
    """Leverantörens nyckelord: första distinkta ordet i leverantörsnamnet
    ("Skånetrafiken" → "skanetrafiken", "Scandic Oslo City" → "scandic")."""
    for w in re.findall(r"[a-z0-9]+", _norm(vendor)):
        if len(w) >= 3 and w not in _GENERIC_VENDOR_WORDS and not w.isdigit():
            return w
    return None


def _tx_vendor_text(tx: dict) -> str:
    """Utläggets leverantör: leverantörsfälten + de tre första orden i
    beskrivningen ("Moovy parking at ..."). Ord längre in räknas inte
    ("Parking for the Finnair flight" är inte ett Finnair-utlägg)."""
    head = " ".join(re.findall(r"[a-z0-9.]+", _norm(str(tx.get("description") or "")))[:3])
    fields = " ".join(str(tx.get(k) or "") for k in (
        "vendor_name", "merchant", "bill_line_description"))
    v = tx.get("vendor")
    if isinstance(v, dict):
        fields += " " + str(v.get("name") or "")
    elif isinstance(v, str):
        fields += " " + v
    return head + " " + _norm(fields)


def _never(name: str | None) -> bool:
    return (name or "").strip().casefold() in NEVER_SUGGEST


def latest_own_expense(transactions: Iterable[dict], vendor: str | None,
                       accounts: Iterable[dict] | None = None) -> dict | None:
    """C52 — senaste utlägget i Bezala från SAMMA leverantör (leverantörens
    nyckelord som eget ord i utläggets text) med ett riktigt konto. Andra
    leverantörer räknas aldrig (prod 2026-11-22: en Moovy-parkering blev
    förslag för Flytoget, Finnair och Skånetrafiken)."""
    key = vendor_key(vendor)
    if not key:
        return None
    pat = re.compile(r"\b" + re.escape(key) + r"\b")
    best = None
    for tx in transactions or []:
        if not isinstance(tx, dict) or not pat.search(_tx_vendor_text(tx)):
            continue
        lines = _tx_lines(tx)
        acc = (lines[0].get("expense_account_id") if lines else None) or tx.get("account_id")
        if not acc:
            continue
        a = account_by_id(accounts or [], acc)
        if a is not None and _never(a.get("name")):
            continue
        if best is None or _tx_date(tx) > _tx_date(best):
            best = tx
    return best


def rule_account(category: str | None, vendor_texts: Iterable[str | None],
                 other_texts: Iterable[str | None] = ()) -> tuple[str | None, str | None]:
    """(kontonamn, källa) enligt Mikkos regler: leverantör/kortrad först,
    sedan kvittots kategori, sist övriga texter (ämne, beskrivning)."""
    from app.services.c51_vat import CATEGORY_ACCOUNTS

    hay = " ".join(t or "" for t in vendor_texts)
    for rx, name in VENDOR_RULES:
        if rx.search(hay):
            return name, f"rule: {name} (vendor)"
    if category and CATEGORY_ACCOUNTS.get(category):
        name = CATEGORY_ACCOUNTS[category]
        return name, f"rule: category {category}"
    hay2 = " ".join(t or "" for t in other_texts)
    for rx, name in VENDOR_RULES:
        if rx.search(hay2):
            return name, f"rule: {name} (receipt text)"
    return None, None


def suggest_account(*vendor_texts: str | None, accounts: list[dict] | None = None,
                    transactions: list[dict] | None = None,
                    category: str | None = None,
                    card_description: str | None = None) -> dict:
    """C52 — första texten är leverantören. Ordning:
    1. AI-verktyg → "AI työkalut".
    2. Senaste utlägg från SAMMA leverantör (aldrig "Tiliöimättömät ostolaskut").
    3. Mikkos regler (leverantör/kortrad, kategori, övrig text).
    4. "Account missing" - gissar aldrig."""
    if accounts is None or transactions is None:
        acc_c, tx_c = reference()
        accounts = acc_c if accounts is None else accounts
        transactions = tx_c if transactions is None else transactions
    texts = [t for t in vendor_texts]
    if is_ai_tool(*texts, card_description):
        a = account_by_name(accounts, AI_ACCOUNT_NAME)
        return {"account_id": (a or {}).get("id", AI_ACCOUNT_ID), "name": AI_ACCOUNT_NAME,
                "source": "rule: AI tools", "missing": False}
    vendor = next((t for t in texts if t and t.strip()), None) or card_description
    tx = latest_own_expense(transactions, vendor, accounts)
    if tx is None and card_description and vendor_key(card_description) != vendor_key(vendor):
        tx = latest_own_expense(transactions, card_description, accounts)
    if tx is not None:
        line = (_tx_lines(tx) or [{}])[0]
        acc_id = line.get("expense_account_id") or tx.get("account_id")
        a = account_by_id(accounts, acc_id)
        cc = line.get("cost_center_ids") or tx.get("cost_center_ids") or (
            [tx["cost_center_id"]] if tx.get("cost_center_id") else None)
        return {"account_id": acc_id, "name": (a or {}).get("name"),
                "vat_code_id": line.get("vat_code_id"), "cost_center_ids": cc,
                "source": f"your latest expense from the same vendor ({vendor_key(vendor)}) "
                          f"{_tx_date(tx)[:10]} (id {tx.get('id')})",
                "missing": False}
    first = texts[:1] + [card_description]
    name, source = rule_account(category, first, texts[1:])
    if name and not _never(name):
        a = account_by_name(accounts, name)
        return {"account_id": (a or {}).get("id"), "name": name, "source": source,
                "missing": False}
    return {"account_id": None, "name": None, "source": None, "missing": True,
            "flag": ACCOUNT_MISSING}


# ---------------------------------------------------------------- datum

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_DMY_RE = re.compile(r"\b\d{1,2}\s+(" + "|".join(_MONTHS) + r")\s+\d{4}\b")
_MY_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(19|20)\d{2}\b")


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def english_date(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def fix_description_date(description: str | None, corrected) -> str | None:
    """Datumet i beskrivningen kommer alltid från kortradens/kvittots rättade
    datum ("26 November 2018" → "3 November 2026"). Datum inom en vecka från
    det rättade lämnas (t.ex. resans slutdatum)."""
    d = _as_date(corrected)
    if not description or d is None:
        return description

    def dmy(m: re.Match) -> str:
        try:
            found = datetime.strptime(m.group(0), "%d %B %Y").date()
        except ValueError:
            return english_date(d)
        return m.group(0) if abs((found - d).days) <= 7 else english_date(d)

    def my(m: re.Match) -> str:
        month = _MONTHS.index(m.group(1)) + 1
        year = int(m.group(0)[-4:])
        if (year, month) == (d.year, d.month):
            return m.group(0)
        return f"{_MONTHS[d.month - 1]} {d.year}"

    out = _DMY_RE.sub(dmy, description)
    if not _DMY_RE.search(description):
        out = _MY_RE.sub(my, out)
    return out
