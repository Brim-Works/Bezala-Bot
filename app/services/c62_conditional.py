"""C62 — Sträcka (km), Vehicle och Antal passagerare i Bezala-utkast.

Bezala sparar fälten som "ConditionalField" på kvittot (arkitekten läste
webbformulärets anrop 2026-11-26 på utkast 5594772):

    PUT /api/transactions/<id>
    {"conditional_values_attributes": [
        {"field_id": 1, "field_type": "ExpenseCategory::ConditionalField",
         "value": "Train", "expense_id": 5594772, "expense_type": "Receipt"},
        {"field_id": 2, ..., "value": "30.4"},
        {"id": 266932, "field_id": 3, ..., "value": "1"}]}

field_id 1 = Vehicle, 2 = Sträcka (km), 3 = Antal passagerare.

Del 1 (denna fil, början): läsning. GET /transactions/<id> visar inte
conditional_values. `probe_reads` provar en fast lista med GET-adresser och
rapporterar var värdena finns. Bara GET - inget skrivs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

FIELD_VEHICLE = 1
FIELD_KM = 2
FIELD_PASSENGERS = 3
FIELD_TYPE = "ExpenseCategory::ConditionalField"
EXPENSE_TYPE = "Receipt"
FIELD_KEYS = {FIELD_VEHICLE: "vehicle", FIELD_KM: "km", FIELD_PASSENGERS: "passengers"}

# Bezalas tillåtna Vehicle-värden (exakt text, läst i webbformuläret 2026-11-26).
VEHICLE_OPTIONS = (
    "Airplane: Large ~ 900km/h", "Airplane: Small ~ 700km/h", "Bus",
    "Ferry by foot (without car)", "Ferry with car", "Metro", "Taxi", "Train", "Tram",
)

# GET-adresser att prova (relativt API-basen, t.ex. https://app.bezala.com/api).
PROBE_PATHS = (
    "/transactions/{id}?include=conditional_values",
    "/transactions/{id}?expand=conditional_values",
    "/transactions/{id}?with=conditional_values",
    "/transactions/{id}/conditional_values",
    "/transactions/{id}/edit",
    "/transactions/{id}.json",
    "/receipts/{id}",
    "/receipts/{id}/edit",
    "/receipts/{id}/conditional_values",
    "/expenses/{id}",
    "/conditional_values?expense_id={id}",
    "/conditional_values?expense_id={id}&expense_type=Receipt",
    "/expense_categories",
    "/expense_categories/conditional_fields",
    "/conditional_fields",
)


def _find_conditional(obj: Any, path: str = "$", out: list | None = None) -> list[dict]:
    """Hitta nycklar som innehåller 'conditional' och listor med field_id."""
    out = [] if out is None else out
    if len(out) > 20:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}"
            if "conditional" in str(k).lower():
                out.append({"path": p, "value": _short(v)})
            _find_conditional(v, p, out)
    elif isinstance(obj, list):
        if obj and all(isinstance(x, dict) and "field_id" in x for x in obj[:5]):
            out.append({"path": path, "value": _short(obj)})
            return out
        for i, v in enumerate(obj[:50]):
            _find_conditional(v, f"{path}[{i}]", out)
    return out


def _short(v: Any, limit: int = 1500) -> Any:
    s = json.dumps(v, default=str, ensure_ascii=False)
    return v if len(s) <= limit else s[:limit] + "…"


def probe_reads(transaction_id: str, client=None, paths: tuple[str, ...] = PROBE_PATHS) -> dict:
    """Prova GET-adresserna (bara GET) och visa var conditional_values finns."""
    own = client is None
    if own:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
    results = []
    try:
        for tmpl in paths:
            path = tmpl.format(id=transaction_id)
            item: dict = {"path": path}
            try:
                resp = client.get_raw(path)
            except Exception as exc:  # noqa: BLE001
                item["error"] = str(exc)[:200]
                results.append(item)
                continue
            item["status"] = resp.status_code
            item["content_type"] = resp.headers.get("content-type", "")
            try:
                data = resp.json()
            except ValueError:
                data = None
                item["body_start"] = (resp.text or "")[:200]
            if data is not None:
                inner = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(inner, dict):
                    item["top_level_keys"] = sorted(inner.keys())[:80]
                elif isinstance(inner, list):
                    item["list_len"] = len(inner)
                    if inner and isinstance(inner[0], dict):
                        item["item_keys"] = sorted(inner[0].keys())[:80]
                found = _find_conditional(data)
                if found:
                    item["conditional"] = found
                elif isinstance(inner, list) and len(json.dumps(inner, default=str)) < 4000:
                    item["body"] = inner
            results.append(item)
    finally:
        if own:
            client.close()
    hits = [r["path"] for r in results if r.get("conditional")]
    return {"transaction_id": str(transaction_id), "found_in": hits, "results": results,
            "note": "Read-only (GET). Nothing was written to Bezala."}
