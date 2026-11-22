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
    "id", "state", "status", "date", "description", "amount", "currency", "sum",
    "total", "account_id", "credit_account_id", "cost_center_id", "cost_center_ids",
    "bill_id", "bill_line_id", "connected_to_bill_line", "approvers", "approver_id",
    "current_approver", "submitted_at", "sent_at", "created_at", "updated_at",
)


def _compact(tx: dict) -> dict:
    inner = guard._unwrap(tx)
    out: dict[str, Any] = {k: inner.get(k) for k in _COMPARE_KEYS if k in inner}
    for key in ("vat_lines", "vat_lines_attributes", "lines"):
        lines = inner.get(key)
        if isinstance(lines, list):
            out[key] = [
                {k: ln.get(k) for k in ("expense_account_id", "vat_code_id", "tax_percentage",
                                        "amount", "sum", "cost_center_ids", "vat_name")
                 if isinstance(ln, dict) and k in ln}
                for ln in lines[:10]
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


def inspect(transaction_id: str | None = None, bill_line_id: str | None = None,
            client=None) -> dict:
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
        if not ids:
            found_via = "GET /transactions list (bill_line_id found in the transaction)"
            for tx in _find_by_bill_line(client, str(bill_line_id)):
                tid = guard._unwrap(tx).get("id")
                if tid is not None:
                    ids.append(str(tid))
        if not ids:
            return {"bill_line_id": bill_line_id, "transactions": [],
                    "note": "No Bezala transaction references this card line."}
        out = []
        for tid in ids[:5]:
            try:
                tx = client.get_transaction(tid)
            except Exception as exc:  # noqa: BLE001
                out.append({"transaction_id": tid, "error": str(exc)[:300]})
                continue
            out.append({"transaction_id": tid, **describe(tx)})
        return {"bill_line_id": bill_line_id, "found_via": found_via,
                "draft_states": sorted(guard.DRAFT_STATES), "transactions": out,
                "note": "Read-only (GET). Nothing was written to Bezala."}
    finally:
        if close:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
