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
import re
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


# ================================================================ Del 1: läsning
#
# READ_PATH = GET-adressen där kvittots conditional_values syns. Fastställd med
# probe_reads i prod 2026-11-26 på 5594772: svaret är en lista med rader
# {id, expense_id, expense_type, field_id, field_type, value} (Train, 30.4, 1).
# GET /transactions/<id> (även ?include=…) visar dem inte.
READ_PATH: str | None = "/transactions/{id}/conditional_values"
READ_KEY = "conditional_values"


def _rows_from(data: Any) -> list[dict] | None:
    """conditional_values-raderna ur ett svar (var de än ligger)."""
    if isinstance(data, dict):
        for key in (READ_KEY, "conditional_values_attributes"):
            v = data.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        for v in data.values():
            if isinstance(v, (dict, list)):
                got = _rows_from(v)
                if got is not None:
                    return got
    elif isinstance(data, list):
        if data and all(isinstance(r, dict) and "field_id" in r for r in data):
            return list(data)
        for v in data:
            got = _rows_from(v)
            if got is not None:
                return got
    return None


def parse_values(rows: list[dict] | None) -> dict:
    """{"vehicle": {"id", "value"}, "km": {...}, "passengers": {...}} (saknas → None)."""
    out: dict = {k: None for k in FIELD_KEYS.values()}
    for r in rows or []:
        try:
            fid = int(r.get("field_id"))
        except (TypeError, ValueError):
            continue
        key = FIELD_KEYS.get(fid)
        if key is None:
            continue
        out[key] = {"id": r.get("id"), "value": r.get("value")}
    return out


def read_values(client, transaction_id: str) -> dict | None:
    """Läs fälten (GET READ_PATH). None = kan inte läsas → inget skrivs."""
    if not READ_PATH or not hasattr(client, "get_raw"):
        return None
    try:
        resp = client.get_raw(READ_PATH.format(id=transaction_id))
        if resp.status_code >= 400:
            return None
        rows = _rows_from(resp.json())
    except Exception:  # noqa: BLE001
        logger.exception("C62: conditional_values kunde inte läsas för %s", transaction_id)
        return None
    return parse_values(rows) if rows is not None else None


def value_of(values: dict | None, key: str):
    v = (values or {}).get(key)
    return v.get("value") if isinstance(v, dict) else None


# ================================================================ Del 2: vad som ska skrivas

# Benets färdsätt → Bezalas Vehicle. Flyg avgörs separat (flight_vehicle).
MODE_TO_VEHICLE = {
    "train": "Train",
    "bus": "Bus",
    "taxi": "Taxi",
    "metro": "Metro",
    "tram": "Tram",
    "ferry": "Ferry by foot (without car)",
    "ferry_foot": "Ferry by foot (without car)",
    "ferry_car": "Ferry with car",
}
# C63 — Flyg: Bezalas Vehicle gäller flygplanets typ och fart, inte sträckan.
# Alla flygningar = Large. Small bara när flygplanet är känt som litet
# propellerplan (typen finns på segmentet eller i kvittots text). Admins
# kategorier (kort/medel/lång efter km) påverkas inte.
AIRPLANE_SMALL = "Airplane: Small ~ 700km/h"
AIRPLANE_LARGE = "Airplane: Large ~ 900km/h"
PROPELLER_RE = re.compile(
    r"\b(ATR[\s-]?(42|72)|Dash[\s-]?8|DHC[\s-]?8|Q[\s-]?400|Saab[\s-]?(340|2000)"
    r"|Jetstream[\s-]?\d*|Dornier[\s-]?328|Twin[\s-]?Otter|turbo[\s-]?prop\w*"
    r"|propellerplan|propeller(?:flyg|kone|plane)?)\b", re.I)
SEGMENT_AIRCRAFT_KEYS = ("aircraft", "aircraft_type", "equipment", "plane", "plane_type")

# C63 — km från rutttabellen (uppskattning) får skrivas, men bara för dessa
# markfärdsätt. Flyg tar km ur airport_table som förut.
ESTIMATE_OK_MODES = frozenset({"train", "bus", "taxi"})
ESTIMATE_OK_SOURCES = frozenset({"route_table"})
ESTIMATE_NOTE = "km uppskattat, rutttabell"


def _segment_aircraft(segments, flight_no: str | None) -> str | None:
    from app.services.c60_draft import _flight_no_key

    key = _flight_no_key(flight_no)
    for seg in segments or []:
        if not isinstance(seg, dict) or _flight_no_key(seg.get("flight_no")) != key:
            continue
        for k in SEGMENT_AIRCRAFT_KEYS:
            if seg.get(k):
                return str(seg[k])
    return None


def _receipt_text(row) -> str:
    return " ".join(str(getattr(row, k, None) or "") for k in (
        "subject", "summary", "ai_description_en", "ai_description", "manual_comment"))


def flight_vehicle(legs: list, row=None, segments=None) -> tuple[str | None, str | None]:
    """(Vehicle, skäl). Large om inte ALLA ben är kända propellerplan."""
    if not legs:
        return None, None
    found = []
    for lg in legs:
        typ = getattr(lg, "aircraft", None) or _segment_aircraft(segments, lg.flight_no)
        if typ is None and len(legs) == 1 and row is not None:
            m = PROPELLER_RE.search(_receipt_text(row))
            typ = m.group(0) if m else None
        if not typ or not PROPELLER_RE.search(str(typ)):
            return AIRPLANE_LARGE, None
        found.append(str(typ))
    return AIRPLANE_SMALL, "propellerplan: " + ", ".join(dict.fromkeys(found))


def km_basis(legs: list) -> dict:
    """Kan km skrivas? {"ok", "km", "estimate", "note"}. Mikkos manuella km
    vinner alltid; rutttabellens uppskattning bara för tåg/buss/taxi; saknas
    km eller är riktningen okänd blir km tomt."""
    total, estimate = 0.0, False
    for lg in legs:
        if lg.km is None:
            reason = getattr(lg, "km_reason", None)
            return {"ok": False, "km": None, "estimate": False,
                    "note": f"km saknas ({lg.from_place}-{lg.to_place} {lg.leg_date}"
                            f"{', ' + reason if reason else ''})"}
        if lg.source != "manual" and lg.is_estimate:
            if lg.mode in ESTIMATE_OK_MODES and lg.source in ESTIMATE_OK_SOURCES:
                estimate = True
            else:
                return {"ok": False, "km": None, "estimate": False,
                        "note": f"km saknas: {lg.from_place}-{lg.to_place} är en uppskattning "
                                f"({lg.source}, {lg.mode})"}
        total += float(lg.km)
    if total <= 0:
        return {"ok": False, "km": None, "estimate": False, "note": "km saknas"}
    return {"ok": True, "km": round(total, 1), "estimate": estimate,
            "note": ESTIMATE_NOTE if estimate else None}


def km_text(km: float) -> str:
    """30.4 → "30.4", 39.0 → "39"."""
    return f"{round(float(km), 1):g}"


def _empty(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def plan(db, row, tx: dict, values: dict | None) -> dict:
    """Torrkörning: vad boten skulle skriva i Vehicle, Sträcka (km) och Antal
    passagerare. Bara utkast, bara tomma fält, km gissas aldrig."""
    from app.services import bezala_write_guard as guard
    from app.services.c60_draft import legs_for_receipt

    snap = guard.status_snapshot(tx)
    out: dict = {"wanted": {}, "fields": {}, "rows": [], "skipped": [], "notes": [],
                 "before": None, "legs": []}
    if not guard.is_draft(snap):
        out["skipped"].append(f"Inte ett utkast (state={guard.draft_state(snap)!r}) - "
                              "boten skriver inte")
        return out
    if values is None:
        out["skipped"].append("Vehicle/km/passagerare: kan inte läsas i Bezala - skrivs inte")
        return out
    out["before"] = {k: value_of(values, k) for k in FIELD_KEYS.values()}
    legs = legs_for_receipt(db, row)
    out["legs"] = [{"leg_id": lg.id, "date": lg.leg_date.isoformat() if lg.leg_date else None,
                    "mode": lg.mode, "from": lg.from_place, "to": lg.to_place, "km": lg.km,
                    "source": lg.source, "is_estimate": bool(lg.is_estimate),
                    "km_reason": lg.km_reason, "flight_no": lg.flight_no} for lg in legs]
    if not legs:
        out["notes"].append("Inget ben matchat till kvittot - inget fylls i")
        return out
    modes = {lg.mode for lg in legs}
    vehicle = None
    if len(modes) > 1:
        out["notes"].append(f"Flera färdsätt ({', '.join(sorted(modes))}) - oklar match, "
                            "inget fylls i")
        return out
    mode = next(iter(modes))
    own = [lg for lg in legs if lg.receipt_message_id == row.message_id]
    if mode == "flight" and not row.flight_segments and not own:
        # T.ex. ändringsavgift: flygen är lånade från biljetten med samma
        # bokningsnummer. Km finns redan på biljetten → fylls inte i (dubbelt).
        out["notes"].append("Flygen hör till ett annat kvitto (samma bokning, t.ex. "
                            "ändringsavgift) - oklar match, inget fylls i")
        return out
    basis = km_basis(legs)
    out["km_estimate"] = bool(basis["ok"] and basis["estimate"])
    out["km_kind"] = None if not basis["ok"] else ("uppskattad" if basis["estimate"]
                                                   else "riktig")
    if basis["ok"]:
        if mode == "flight":
            vehicle, why = flight_vehicle(legs, row, row.flight_segments)
            if why:
                out["notes"].append(f"Airplane: Small - {why}")
        else:
            vehicle = MODE_TO_VEHICLE.get(mode)
        if vehicle is None:
            out["notes"].append(f"Färdsätt '{mode}' har inget Vehicle i Bezala - km och "
                                "Vehicle lämnas tomma")
        else:
            out["wanted"]["vehicle"] = vehicle
            out["wanted"]["km"] = km_text(basis["km"])
            if basis["estimate"]:
                out["notes"].append(ESTIMATE_NOTE)
    else:
        if basis["note"] and basis["note"] != "km saknas":
            out["notes"].append(basis["note"])
        out["notes"].append("km saknas")
    out["wanted"]["passengers"] = "1"
    for key, want in out["wanted"].items():
        cur = value_of(values, key)
        if not _empty(cur):
            if str(cur).strip() != str(want):
                out["skipped"].append(f"{key}: värdet i Bezala behålls ({cur!r})")
            continue
        out["fields"][key] = want
    tx_id = int(guard._unwrap(tx).get("id"))
    fid = {v: k for k, v in FIELD_KEYS.items()}
    for key, want in out["fields"].items():
        r: dict = {"field_id": fid[key], "field_type": FIELD_TYPE, "value": want,
                   "expense_id": tx_id, "expense_type": EXPENSE_TYPE}
        existing = (values or {}).get(key)
        if isinstance(existing, dict) and existing.get("id") is not None:
            r = {"id": existing["id"], **r}
        out["rows"].append(r)
    return out


def confirm(values_after: dict | None, fields: dict) -> tuple[list[str], list[str]]:
    ok, missing = [], []
    for key, want in fields.items():
        got = value_of(values_after, key)
        same = got is not None and (str(got).strip() == str(want) or (
            key == "km" and _float(got) == _float(want)))
        (ok if same else missing).append(key)
    return ok, missing


def _float(v):
    try:
        return round(float(str(v).replace(",", ".")), 1)
    except (TypeError, ValueError):
        return None
