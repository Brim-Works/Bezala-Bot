"""C50 Del 1 — kompletta utkast: konto per leverantör + beskrivningens datum.

Kontoförslag (gissar aldrig):
1. Mikkos fasta regel: AI-verktyg (Cursor, Lovable, Anthropic/Claude,
   Bolt/StackBlitz, OpenAI/ChatGPT, Midjourney) → "AI työkalut".
2. Annars: konto, momskod och kostnadsställe från Mikkos SENASTE egna utlägg
   för samma leverantör i Bezala (läses, cachas av kortradssynken).
3. Annars: inget konto → flagga "Account missing" i svep-kön.

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


def _vendor_words(*texts: str | None) -> set[str]:
    words = set()
    for t in texts:
        for w in re.findall(r"[a-zåäö0-9]{4,}", (t or "").lower()):
            if w not in {"mikko", "keinonen", "receipt", "invoice", "kuitti", "kvitto",
                         "helsinki", "stockholm", "subscription", "payment"}:
                words.add(w)
    return words


def _tx_date(tx: dict) -> str:
    return str(tx.get("date") or tx.get("created_at") or "")


def _tx_lines(tx: dict) -> list[dict]:
    for k in ("vat_lines", "vat_lines_attributes", "lines"):
        v = tx.get(k)
        if isinstance(v, list) and v:
            return [x for x in v if isinstance(x, dict)]
    return []


def latest_own_expense(transactions: Iterable[dict], *vendor_texts: str | None) -> dict | None:
    """Senaste utlägget i Bezala vars text innehåller leverantörens ord och
    som har ett riktigt konto (inte standardkontot)."""
    words = _vendor_words(*vendor_texts)
    if not words:
        return None
    best = None
    for tx in transactions or []:
        if not isinstance(tx, dict):
            continue
        text = " ".join(str(tx.get(k) or "") for k in ("description", "vendor", "merchant",
                                                        "bill_line_description")).lower()
        if not any(w in text for w in words):
            continue
        lines = _tx_lines(tx)
        acc = (lines[0].get("expense_account_id") if lines else None) or tx.get("account_id")
        if not acc:
            continue
        if best is None or _tx_date(tx) > _tx_date(best):
            best = tx
    return best


def suggest_account(*vendor_texts: str | None, accounts: list[dict] | None = None,
                    transactions: list[dict] | None = None,
                    category: str | None = None) -> dict:
    if accounts is None or transactions is None:
        acc_c, tx_c = reference()
        accounts = acc_c if accounts is None else accounts
        transactions = tx_c if transactions is None else transactions
    if is_ai_tool(*vendor_texts):
        a = account_by_name(accounts, AI_ACCOUNT_NAME)
        return {"account_id": (a or {}).get("id", AI_ACCOUNT_ID), "name": AI_ACCOUNT_NAME,
                "source": "rule: AI tools", "missing": False}
    tx = latest_own_expense(transactions, *vendor_texts)
    if tx is not None:
        line = (_tx_lines(tx) or [{}])[0]
        acc_id = line.get("expense_account_id") or tx.get("account_id")
        a = account_by_id(accounts, acc_id)
        cc = line.get("cost_center_ids") or tx.get("cost_center_ids") or (
            [tx["cost_center_id"]] if tx.get("cost_center_id") else None)
        return {"account_id": acc_id, "name": (a or {}).get("name"),
                "vat_code_id": line.get("vat_code_id"), "cost_center_ids": cc,
                "source": f"your latest expense {_tx_date(tx)[:10]} (id {tx.get('id')})",
                "missing": False}
    # C51 Del 3 — reserv per kategori (Mikkos beslut), efter C50-regeln.
    from app.services.c51_vat import category_account

    name = category_account(category, *vendor_texts)
    if name:
        a = account_by_name(accounts, name)
        return {"account_id": (a or {}).get("id"), "name": name,
                "source": f"rule: category {category or 'company car'}", "missing": False}
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
