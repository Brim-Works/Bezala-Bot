"""C51 Del 1 — läs en Bezala-transaktion (ENDAST GET) och visa dess state.

Används för att jämföra ett utkast (t.ex. Bolt, kortrad 2513372) med ett
utlägg som väntar på attest (t.ex. Arlanda, transaction 5579319). Inget
skrivs. Se docs/bezala_states.md.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services import bezala_write_guard as guard

logger = logging.getLogger(__name__)

# Fält som är intressanta att jämföra mellan utkast och attest.
_COMPARE_KEYS = (
    "id", "state", "status", "type", "date", "description", "billed_amount",
    "billed_amount_currency", "original_amount", "original_amount_currency",
    "amount", "currency", "sum", "credit_account_name", "tax_percentage",
    "current_approvers_ids", "approved_by", "transaction_approvers", "not_connected_to_card",
    "total", "account_id", "credit_account_id", "cost_center_id", "cost_center_ids",
    "bill_id", "bill_line_id", "connected_to_bill_line", "approvers", "approver_id",
    "taxable", "reimbursement_type", "emissions", "participants", "is_amount_mismatch", "exchange_rate", "vendor_id",
    "current_approver", "submitted_at", "sent_at", "created_at", "updated_at",
)


def _compact(tx: dict) -> dict:
    inner = guard._unwrap(tx)
    out: dict[str, Any] = {k: inner.get(k) for k in _COMPARE_KEYS if k in inner}
    for key in ("vat_lines", "vat_lines_attributes", "lines"):
        lines = inner.get(key)
        if isinstance(lines, list):
            # C60 — hela momsraden (id, taxable, vat_code_id …) så att det syns
            # hur Bezala sparar t.ex. "Purchases Abroad (Non-EU)".
            out[key] = [
                {k: v for k, v in ln.items() if not isinstance(v, (dict, list)) or k in (
                    "cost_center_ids",)}
                for ln in lines[:10] if isinstance(ln, dict)
            ]
    return out


def describe(tx: dict) -> dict:
    snap = guard.status_snapshot(tx)
    state = guard.draft_state(snap)
    return {
        "state": state,
        "is_draft": guard.is_draft(snap),
        "meaning": ("draft (Utkast) - the bot may write to it"
                    if guard.is_draft(snap) else
                    "NOT a draft - the bot refuses to write to it"),
        "status_fields": snap,
        "top_level_keys": sorted(guard._unwrap(tx).keys()),
        "fields": _compact(tx),
    }


def _find_by_bill_line(client, bill_line_id: str) -> list[dict]:
    pat = re.compile(rf"(?<!\d){re.escape(str(bill_line_id))}(?!\d)")
    hits = []
    for tx in client.list_transactions() or []:
        if isinstance(tx, dict) and pat.search(json.dumps(tx, default=str)):
            hits.append(tx)
    return hits


def _amount_of(tx: dict) -> float | None:
    t = guard._unwrap(tx)
    for k in ("billed_amount", "original_amount", "amount", "sum", "total", "total_amount"):
        try:
            if t.get(k) is not None:
                return abs(float(t[k]))
        except (TypeError, ValueError):
            continue
    return None


def _find_by_amount_date(txs: list[dict], amount: float, day: str | None) -> list[dict]:
    """Reserv när listan inte nämner kortraden: samma belopp (±0,01) och
    datum inom ±3 dagar."""
    from datetime import date as _d

    want = abs(float(amount))
    try:
        d0 = _d.fromisoformat(day[:10]) if day else None
    except ValueError:
        d0 = None
    hits = []
    for tx in txs:
        a = _amount_of(tx)
        if a is None or abs(a - want) > 0.011:
            continue
        if d0 is not None:
            raw = str(guard._unwrap(tx).get("date") or "")[:10]
            try:
                if abs((_d.fromisoformat(raw) - d0).days) > 3:
                    continue
            except ValueError:
                pass
        hits.append(tx)
    return hits


def inspect(transaction_id: str | None = None, bill_line_id: str | None = None,
            client=None, *, amount: float | None = None, day: str | None = None) -> dict:
    """GET /transactions/{id}, eller hitta transaktionen för en kortrad via
    GET /transactions (listan). Skriver aldrig."""
    if not transaction_id and not bill_line_id:
        return {"error": "Give transaction_id or bill_line_id"}
    close = False
    if client is None:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
        close = True
    try:
        ids: list[str] = [str(transaction_id)] if transaction_id else []
        found_via = "transaction_id"
        listing: dict = {}
        if not ids:
            txs = [t for t in (client.list_transactions() or []) if isinstance(t, dict)]
            listing = {"count": len(txs),
                       "sample_keys": sorted(guard._unwrap(txs[0]).keys()) if txs else [],
                       "states": sorted({str(guard._unwrap(t).get("state")) for t in txs})}
            pat = re.compile(rf"(?<!\d){re.escape(str(bill_line_id))}(?!\d)")
            hits = [t for t in txs if pat.search(json.dumps(t, default=str))]
            found_via = "GET /transactions list (bill_line_id found in the transaction)"
            if not hits and amount is not None:
                hits = _find_by_amount_date(txs, amount, day)
                found_via = "GET /transactions list (same amount, date within 3 days)"
            for tx in hits:
                tid = guard._unwrap(tx).get("id")
                if tid is not None:
                    ids.append(str(tid))
        if not ids:
            return {"bill_line_id": bill_line_id, "transactions": [], "listing": listing,
                    "note": "No Bezala transaction references this card line."}
        out = []
        for tid in ids[:5]:
            try:
                tx = client.get_transaction(tid)
            except Exception as exc:  # noqa: BLE001
                out.append({"transaction_id": tid, "error": str(exc)[:300]})
                continue
            item = {"transaction_id": tid, **describe(tx)}
            # C62 — Vehicle, Sträcka (km), Antal passagerare (bara GET).
            if transaction_id and hasattr(client, "get_raw"):
                try:
                    from app.services.c62_conditional import read_values

                    item["conditional_values"] = read_values(client, tid)
                except Exception as exc:  # noqa: BLE001
                    item["conditional_values"] = {"error": str(exc)[:300]}
            out.append(item)
        return {"bill_line_id": bill_line_id, "found_via": found_via, "listing": listing,
                "draft_states": sorted(guard.DRAFT_STATES), "transactions": out,
                "note": "Read-only (GET). Nothing was written to Bezala."}
    finally:
        if close:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
