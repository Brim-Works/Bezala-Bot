"""C49 Del 3 (fas R3) - tripexpense.nv-payload per godkänd resa.

Bygger XML (och samma innehåll som JSON) ur underlaget i netvisor_entry.
Bara `dailycompensationlines` - inga `customlines` (kvitton går via Bezala)
och inga `travellines`.

Källa: Netvisors dokumentation "Import travel expense tripexpense.nv"
(support.netvisor.fi, artikel 769771). Saker dokumentationen INTE avgör är
markerade med OSÄKERT nedan och listas i `UNCERTAIN_FIELDS` (visas också i
förhandsvisningen).

Medarbetar-id (employeeidentifier) skrivs aldrig in i förhandsvisningen:
den visar platshållaren EMPLOYEE_PLACEHOLDER. NetvisorClient sätter in det
riktiga värdet från env först vid sändning.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime

from sqlalchemy.orm import Session

from app.services.netvisor_entry import build_entry

EMPLOYEE_PLACEHOLDER = "EMPLOYEE_NUMBER"
COMPENSATION_TYPES = ("domesticfull", "domestichalf", "foreign")
LINE_STATUS = "open"  # Hårdregel: alltid open - Riikka granskar i Netvisor.
_NV_TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")
_FANCY_DASH_RE = re.compile("[‐‑‒–—―−]")

# Det dokumentationen inte avgör. Ändra här när Netvisor/Riikka bekräftat.
UNCERTAIN_FIELDS: dict[str, str] = {
    "root": "The docs give <tripexpense> as the top element but show no full XML "
            "example. Netvisor imports are usually wrapped in <root>; we send "
            "<root><tripexpense>...</tripexpense></root>.",
    "amount": "Required. The docs say 'Amount of compensation' with example 1; it looks "
              "like a number of days, not money. We send the number of started "
              "24-hour periods of the row and no unitprice, so Netvisor picks the "
              "price from the compensation type. Netvisor may count days differently.",
    "compensationtype": "Required (domesticfull | domestichalf | foreign). Bezala Bot "
                        "only classifies (country + length), Netvisor decides the rate. "
                        "Domestic trips of 6 h or less get no line.",
    "meals_included": "The API has no documented field for 'Inklusive kost' (free meals). "
                      "It is only visible in linedescription. Check with Netvisor before "
                      "sending rows with meals_included=true via the API.",
    "routedescription": "Documented only on travellines (mileage), not on "
                        "dailycompensationline, so it is NOT sent. The route is in "
                        "linedescription instead.",
    "country": "dailycompensationline has no documented country element. For foreign "
               "lines Netvisor may need the country to pick the rate - unknown how it "
               "is given via the API.",
    "employeeidentifier": "Required; sent with type=\"number\" (employee number from "
                          "NETVISOR_EMPLOYEE_IDENTIFIER).",
    "returntime": "The docs describe it with the same text as timeofdeparture "
                  "(copy-paste); assumed to be the return time.",
}


def _nv_time(d: str | None, t: str | None) -> str | None:
    """'13.1.2026' + '05:07' -> '2026-01-13 05:07:00.000' (Helsingfors-tid)."""
    if not d or not t:
        return None
    day, month, year = (int(x) for x in d.split("."))
    dt = datetime(year, month, day, int(t[:2]), int(t[3:5]))
    return dt.strftime("%Y-%m-%d %H:%M:%S") + ".000"


def build_payload(db: Session, trip_id: int, employee: str = EMPLOYEE_PLACEHOLDER) -> dict:
    """Payload som dict (samma innehåll som XML:en) + varningar."""
    entry = build_entry(db, trip_id)
    lines = []
    skipped = []
    for r in entry["rows"]:
        if not r["compensation_type"]:
            skipped.append(
                f"Row {r['row']}: no per diem line (domestic, {r['hours']} h or no country)."
            )
            continue
        lines.append({
            "employeeidentifier": {"type": "number", "value": employee},
            "compensationtype": r["compensation_type"],
            "amount": r["started_24h_periods"],
            "linedescription": r["description"],
            "timeofdeparture": _nv_time(r["start_date"], r["start_time"]),
            "returntime": _nv_time(r["end_date"], r["end_time"]),
            "linestatus": LINE_STATUS,
            # Bara information i förhandsvisningen - skickas inte i XML.
            "_country": r["country"],
            "_meals_included": r["settings"]["meals_included"],
        })
    payload = {
        "tripexpense": {
            "header": entry["title"],
            "description": entry["description"],
            "dailycompensationlines": lines,
        }
    }
    return {
        "trip_id": trip_id,
        "resource": "tripexpense.nv",
        "method": "POST",
        "payload": payload,
        "warnings": list(entry["warnings"]),
        "notes": list(entry["notes"]) + skipped,
        "uncertain_fields": UNCERTAIN_FIELDS,
        "ready": entry["ready"] and bool(lines),
    }


def to_xml(payload: dict) -> str:
    te = payload["tripexpense"]
    root = ET.Element("root")
    node = ET.SubElement(root, "tripexpense")
    ET.SubElement(node, "header").text = te["header"]
    if te.get("description"):
        ET.SubElement(node, "description").text = te["description"]
    lines_el = ET.SubElement(node, "dailycompensationlines")
    for ln in te["dailycompensationlines"]:
        el = ET.SubElement(lines_el, "dailycompensationline")
        emp = ET.SubElement(el, "employeeidentifier", {"type": ln["employeeidentifier"]["type"]})
        emp.text = str(ln["employeeidentifier"]["value"])
        for key in ("compensationtype", "amount", "linedescription",
                    "timeofdeparture", "returntime", "linestatus"):
            ET.SubElement(el, key).text = str(ln[key]) if ln[key] is not None else ""
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode")


def public_json(payload: dict) -> dict:
    """JSON-nedladdning utan interna _-fält."""
    te = dict(payload["tripexpense"])
    te["dailycompensationlines"] = [
        {k: v for k, v in ln.items() if not k.startswith("_")}
        for ln in te["dailycompensationlines"]
    ]
    return {"tripexpense": te}


def validate_xml(xml: str) -> list[str]:
    """Kontroll mot Netvisors dokumenterade schema (så långt det räcker)."""
    errors: list[str] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"XML does not parse: {exc}"]
    te = root.find("tripexpense") if root.tag == "root" else root
    if te is None or te.tag != "tripexpense":
        return ["Missing <tripexpense>."]
    if not (te.findtext("header") or "").strip():
        errors.append("header is required.")
    if te.find("customlines") is not None:
        errors.append("customlines must not be sent (receipts go via Bezala).")
    lines = te.findall("dailycompensationlines/dailycompensationline")
    if not lines:
        errors.append("At least one dailycompensationline is required.")
    for i, ln in enumerate(lines, 1):
        for req in ("employeeidentifier", "compensationtype", "amount",
                    "linedescription", "timeofdeparture", "returntime"):
            if not (ln.findtext(req) or "").strip():
                errors.append(f"Line {i}: {req} is required.")
        ctype = ln.findtext("compensationtype")
        if ctype and ctype not in COMPENSATION_TYPES:
            errors.append(f"Line {i}: compensationtype '{ctype}' is not allowed.")
        for tkey in ("timeofdeparture", "returntime"):
            v = ln.findtext(tkey)
            if v and not _NV_TIME_RE.match(v):
                errors.append(f"Line {i}: {tkey} must be 'YYYY-MM-DD HH:MM:SS.000'.")
        dep, ret = ln.findtext("timeofdeparture"), ln.findtext("returntime")
        if dep and ret and _NV_TIME_RE.match(dep) and _NV_TIME_RE.match(ret) and ret <= dep:
            errors.append(f"Line {i}: returntime must be after timeofdeparture.")
        amount = ln.findtext("amount") or ""
        if amount and not re.fullmatch(r"\d+([.,]\d+)?", amount):
            errors.append(f"Line {i}: amount must be a number.")
        if ln.findtext("linestatus") != LINE_STATUS:
            errors.append(f"Line {i}: linestatus must be '{LINE_STATUS}'.")
        if ln.find("routedescription") is not None:
            errors.append(f"Line {i}: routedescription is not a dailycompensationline field.")
    if _FANCY_DASH_RE.search(xml):
        errors.append("Text contains a long dash; use a plain '-'.")
    return errors


def preview(db: Session, trip_id: int) -> dict:
    out = build_payload(db, trip_id)
    xml = to_xml(out["payload"])
    out["xml"] = xml
    out["schema_errors"] = validate_xml(xml) if out["payload"]["tripexpense"][
        "dailycompensationlines"] else ["No per diem lines."]
    out["json"] = public_json(out["payload"])
    out["ready"] = out["ready"] and not out["schema_errors"]
    return out
