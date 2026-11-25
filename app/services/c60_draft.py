"""C60 — verifiera kopplingen, fyll i Bezala-utkast, rätta belopp och flygdatum.

Bakgrund (prod 2026-11-25 22:49 UTC): 19 kopplingar kördes parallellt. 8
utkast skapades men kopplades inte till kortraden i Bezala
(connected_to_bill_line=false, bill_id=null) och Bezala läste fel belopp
(Finnair: "Fare" i stället för "Grand Total"; Skånetrafiken: 0). Boten
rapporterade ändå "resolved".

Hårdregler (ändras inte):
* `state` skickas aldrig. Bara utkast med state "draft" skrivs till och
  state läses före och efter varje skrivning (bezala_write_guard.guarded_put).
* Skrivningar kräver `BEZALA_DRAFT_FILL_ENABLED=true` (default false).
* Mikkos egna ändringar skrivs aldrig över: ett fält fylls bara när det är
  tomt eller har Bezalas standardvärde.
* Belopp gissas aldrig: bara kortradens belopp/valuta när kvittot har samma
  belopp/valuta (annan valuta: kvittots belopp, bara om omräkningen stämmer
  med kortraden inom 3 %).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import CardLine, ProcessedMessage
from app.services import bezala_write_guard as guard

logger = logging.getLogger(__name__)

FLAG_DRAFT_FILL = "BEZALA_DRAFT_FILL_ENABLED"
STATUS_UNVERIFIED = "coupling_unverified"
DEFAULT_ACCOUNT_NAME = "Tiliöimättömät ostolaskut"
COST_CENTER_ID = 927151
CROSS_CURRENCY_TOLERANCE = 0.03

# De 8 utkasten från 2026-11-25 som inte kopplades: transaktion → kortrad.
BROKEN_DRAFTS_2026_11_25: dict[str, str] = {
    "5594776": "2478468",  # Finnair 446,77 (Bezala läste 418,00, inget datum)
    "5594779": "2506976",  # Finnair 732,57 (705,00)
    "5594781": "2513381",  # Finnair 380,21 (353,05)
    "5594782": "2513375",  # Finnair 209,48 (213,21)
    "5594772": "2493711",  # Skånetrafiken 14,01 (0)
    "5594773": "2493708",  # Skånetrafiken 14,01 (0)
    "5594774": "2502480",  # Skånetrafiken 22,80 (0)
    "5594775": "2504861",  # Skånetrafiken 22,81 (0)
}

# Bezalas momsval → vat_lines[].tax_percentage (läst med GET på Mikkos egna
# utkast 2026-11-25: 25,5 % = "0.255", 0 % = "0.0", vat_code_id = null).
# "Purchases Abroad (EU/Non-EU)" syns inte som egen kod i API:t ännu (Lovable
# 5594767: tax_percentage "0.0", vat_code_id null) - skrivs därför inte.
_VAT_TO_TAX_PERCENTAGE = {
    "0%": "0.0",
    "10%": "0.10",
    "13,5%": "0.135",
    "25,5%": "0.255",
}

FILL_LOG_KEYS = ("description", "date", "original_amount", "original_amount_currency",
                 "billed_amount", "billed_amount_currency", "connected_to_bill_line",
                 "bill_id", "vat_lines")


def draft_fill_enabled() -> bool:
    return guard._env_bool(FLAG_DRAFT_FILL, False)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


# ================================================================ Del 4: flygdatum

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
_M = "(?:" + "|".join(_MONTHS) + ")"
# "24-25 November 2026", "30 November - 2 December 2026", "24 November 2026"
_DATE_SPAN_RE = re.compile(
    rf"\b\d{{1,2}}(?:\s+{_M})?(?:\s+\d{{4}})?\s*[-–]\s*\d{{1,2}}\s+{_M}\s+\d{{4}}\b"
    rf"|\b\d{{1,2}}\s+{_M}\s+\d{{4}}\b"
)
_MONTH_YEAR_RE = re.compile(rf"\b{_M}\s+(?:19|20)\d{{2}}\b")


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def flight_dates(segments: Any) -> list[date]:
    out = []
    for seg in segments or []:
        if isinstance(seg, dict):
            d = _as_date(seg.get("date") or seg.get("dep"))
            if d is not None:
                out.append(d)
    return sorted(set(out))


def flight_date_text(dates: list[date]) -> str | None:
    """5 November 2026 / 24-25 November 2026 / 30 November - 2 December 2026."""
    if not dates:
        return None
    a, b = min(dates), max(dates)
    if a == b:
        return f"{a.day} {_MONTHS[a.month - 1]} {a.year}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}-{b.day} {_MONTHS[b.month - 1]} {b.year}"
    if a.year == b.year:
        return f"{a.day} {_MONTHS[a.month - 1]} - {b.day} {_MONTHS[b.month - 1]} {b.year}"
    return (f"{a.day} {_MONTHS[a.month - 1]} {a.year} - "
            f"{b.day} {_MONTHS[b.month - 1]} {b.year}")


def flight_description(description: str | None, segments: Any) -> str | None:
    """Beskrivningens datum = flygens datum (aldrig köp- eller kortradens
    datum). Prod: "24-16 November 2026" för flygen 24-25 nov (köpt 16 nov)."""
    text = flight_date_text(flight_dates(segments))
    if not description or not text:
        return description
    if _DATE_SPAN_RE.search(description):
        out = _DATE_SPAN_RE.sub(lambda m: text, description, count=1)
        # Ytterligare datum (dubbletter) tas bort.
        head, tail = out.split(text, 1)
        tail = _DATE_SPAN_RE.sub("", tail)
        return re.sub(r"\s*,\s*(,|$)", r"\1", (head + text + tail)).strip()
    if _MONTH_YEAR_RE.search(description):
        return _MONTH_YEAR_RE.sub(lambda m: text, description, count=1)
    return f"{description.rstrip(' ,.')}, {text}"


def segments_for(db: Session | None, row: ProcessedMessage) -> list[dict]:
    """Kvittots flygsegment, annars segmenten från ett annat kvitto med samma
    bokningsnummer (t.ex. ändringsavgift 8LXVP2 utan egna segment)."""
    if row.flight_segments:
        return list(row.flight_segments)
    if db is None or not row.booking_ref:
        return []
    others = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.booking_ref == row.booking_ref)
        .filter(ProcessedMessage.id != row.id)
        .filter(ProcessedMessage.flight_segments.isnot(None))
        .order_by(ProcessedMessage.id.desc())
        .all()
    )
    for m in others:
        if m.flight_segments:
            return list(m.flight_segments)
    return []


def description_for(db: Session | None, row: ProcessedMessage, fallback_date) -> str | None:
    """Beskrivningen att klistra in/skriva. Flyg: flygens datum. Annat: C50:s
    datumrättning mot kortradens/kvittots datum."""
    from app.services.c50_accounts import fix_description_date

    base = row.ai_description_en or row.summary
    segs = segments_for(db, row) if (row.category == "Flyg" or row.flight_segments) else []
    if segs:
        return flight_description(base, segs)
    return fix_description_date(base, fallback_date)


# ================================================================ Del 1: verifiering


def _num(v) -> float | None:
    try:
        return round(float(v), 2) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _same_amount(a, cur_a, b, cur_b) -> bool:
    a, b = _num(a), _num(b)
    if a is None or b is None:
        return False
    if (cur_a or "").upper() != (cur_b or "").upper():
        return False
    return abs(abs(a) - abs(b)) < 0.011


def verify_coupling(tx: dict, line: CardLine | None) -> dict:
    """Kontroll efter koppling: kopplad till kortraden, bill_id satt, belopp
    stämmer med kortraden, datum satt. Problem beskrivs på enkel svenska."""
    t = guard._unwrap(tx)
    problems: list[str] = []
    snap = guard.status_snapshot(tx)
    if not guard.is_draft(snap):
        problems.append(f"Utkastet är inte längre ett utkast (state={guard.draft_state(snap)!r})")
    if t.get("connected_to_bill_line") is not True:
        problems.append("Utkastet är inte kopplat till kortraden i Bezala")
    if not t.get("bill_id"):
        problems.append("bill_id saknas i Bezala")
    if line is not None and line.amount is not None:
        ok = (_same_amount(t.get("billed_amount"), t.get("billed_amount_currency"),
                           line.amount, line.currency)
              or _same_amount(t.get("original_amount"), t.get("original_amount_currency"),
                              line.amount, line.currency))
        if not ok:
            problems.append(
                f"Beloppet i Bezala ({t.get('original_amount')} "
                f"{t.get('original_amount_currency') or ''}) stämmer inte med kortraden "
                f"({line.amount:.2f} {line.currency or ''})".replace("  ", " "))
    if not t.get("date"):
        problems.append("Datum saknas i utkastet")
    return {
        "ok": not problems,
        "problems": problems,
        "bezala": {k: t.get(k) for k in ("state", "connected_to_bill_line", "bill_id", "date",
                                         "original_amount", "original_amount_currency",
                                         "billed_amount", "billed_amount_currency")},
    }


def _row_for_tx(db: Session, tx_id: str) -> ProcessedMessage | None:
    return (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .filter(ProcessedMessage.bezala_transaction_id == str(tx_id))
            .order_by(ProcessedMessage.id.desc()).first())


def _line_for(db: Session, tx_id: str, row: ProcessedMessage | None,
              bill_line_id: str | None = None) -> CardLine | None:
    q = db.query(CardLine)
    if bill_line_id:
        line = q.filter(CardLine.bill_line_id == str(bill_line_id)).first()
        if line is not None:
            return line
    line = q.filter(CardLine.bezala_transaction_id == str(tx_id)).first()
    if line is not None or row is None or not row.bezala_payment_date:
        return line
    from app.services.card_line_coupling import _snapshot_matches

    for c in q.filter(CardLine.txn_date == _as_date(row.bezala_payment_date)).all():
        if _snapshot_matches(row, c):
            return c
    return None


def mark_unverified(db: Session, line: CardLine | None, row: ProcessedMessage,
                    note: str | None = None) -> None:
    row.bezala_upload_status = STATUS_UNVERIFIED
    row.bezala_error_message = note
    if line is not None and line.status in ("missing", "resolved", STATUS_UNVERIFIED):
        line.status = STATUS_UNVERIFIED
        line.resolved_at = None
        line.bezala_transaction_id = row.bezala_transaction_id
        line.note = note or f"Utkast {row.bezala_transaction_id} skapat - koppling inte verifierad än"
    db.commit()


def verify_and_update(db: Session, transaction_id: str, *, bill_line_id: str | None = None,
                      client=None, fill: bool = True, attempt: int = 1) -> dict:
    """Läs utkastet (GET). Fyll i det om BEZALA_DRAFT_FILL_ENABLED (bara
    draft), läs igen och sätt kortraden till resolved bara om kopplingen
    verifierats. Annars coupling_unverified."""
    tx_id = str(transaction_id)
    row = _row_for_tx(db, tx_id)
    line = _line_for(db, tx_id, row, bill_line_id)
    own = client is None
    if own:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
    try:
        tx = client.get_transaction(tx_id)
        fill_result = None
        if fill and draft_fill_enabled() and row is not None \
                and guard.is_draft(guard.status_snapshot(tx)):
            plan = plan_fill(db, row, line, tx)
            fill_result = apply_fill(client, tx_id, plan, db=db) if plan["changes"] else plan
            if plan["changes"]:
                tx = client.get_transaction(tx_id)
        v = verify_coupling(tx, line)
    finally:
        if own:
            client.close()
    if row is not None:
        if v["ok"]:
            row.bezala_upload_status = "success"
            row.bezala_error_message = None
            if line is not None and line.status in ("missing", STATUS_UNVERIFIED):
                line.status = "resolved"
                line.resolved_at = datetime.utcnow()
                line.note = f"Kopplad och verifierad i Bezala ({tx_id})"
            db.commit()
        else:
            mark_unverified(db, line, row,
                            "Koppling inte verifierad: " + "; ".join(v["problems"]))
    out = {"transaction_id": tx_id, "bill_line_id": line.bill_line_id if line else bill_line_id,
           "verified": v["ok"], "problems": v["problems"], "bezala": v["bezala"]}
    if fill_result is not None:
        out["fill"] = fill_result
    wrote_amount = bool(fill_result and set(fill_result.get("written") or {})
                        & {"original_amount", "date"})
    if not v["ok"] and wrote_amount and attempt == 1:
        # Bezala kan behöva en stund för att koppla efter att beloppet rättats.
        schedule_verification(tx_id, bill_line_id=out["bill_line_id"], attempt=2)
        out["next_check"] = "om ca 75 s"
    return out


def schedule_verification(transaction_id: str, *, bill_line_id: str | None = None,
                          attempt: int = 1, delay: float | None = None) -> None:
    """Läs om utkastet efter 60-90 s (Bezala läser kvittot i bakgrunden)."""
    if os.environ.get("C60_VERIFY_SCHEDULE", "true").strip().lower() == "false":
        return
    delay = _env_float("C60_VERIFY_DELAY_SECONDS", 75.0) if delay is None else delay

    def work():
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            res = verify_and_update(db, transaction_id, bill_line_id=bill_line_id,
                                    attempt=attempt)
            logger.info("C60 verifiering tx=%s: %s", transaction_id, res)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("C60: verifiering av tx %s misslyckades", transaction_id)
        finally:
            db.close()

    timer = threading.Timer(delay, work)
    timer.daemon = True
    timer.start()


# ================================================================ Del 2/3: ifyllning


def _accounts() -> list[dict]:
    try:
        from app.services.c50_accounts import reference

        return reference()[0] or []
    except Exception:  # noqa: BLE001
        logger.exception("C60: kontolistan kunde inte läsas")
        return []


def _default_account_ids(accounts: list[dict]) -> set:
    return {a.get("id") for a in accounts
            if (a.get("name") or "").strip().casefold() == DEFAULT_ACCOUNT_NAME.casefold()}


def _cross_currency_ok(db: Session, row: ProcessedMessage, line: CardLine) -> bool:
    try:
        from app.services.currency_converter import make_db_rate_provider

        rate = make_db_rate_provider(db)
        day = line.txn_date or _as_date(row.receipt_date)
        r = rate(day.isoformat() if day else "", row.currency.upper(), line.currency.upper())
        if not r:
            return False
        converted = float(row.amount) * float(r)
        return abs(converted - abs(float(line.amount))) <= CROSS_CURRENCY_TOLERANCE * abs(
            float(line.amount))
    except Exception:  # noqa: BLE001
        logger.exception("C60: valutakontroll misslyckades")
        return False


def plan_amount(db: Session, row: ProcessedMessage, line: CardLine | None,
                t: dict) -> tuple[dict, list[str]]:
    """Belopp/valuta/datum att rätta. Gissar aldrig."""
    changes: dict = {}
    notes: list[str] = []
    if line is None or line.amount is None or row.amount is None:
        notes.append("Belopp: kortrad eller kvittobelopp saknas - ändras inte")
        return changes, notes
    if _same_amount(t.get("billed_amount"), t.get("billed_amount_currency"),
                    line.amount, line.currency):
        pass
    elif (row.currency or "").upper() == (line.currency or "").upper():
        if _same_amount(row.amount, row.currency, line.amount, line.currency):
            if not _same_amount(t.get("original_amount"), t.get("original_amount_currency"),
                                line.amount, line.currency):
                changes["original_amount"] = f"{abs(float(line.amount)):.2f}"
                changes["original_amount_currency"] = (line.currency or "").upper()
        else:
            notes.append(f"Belopp: kvittot ({row.amount} {row.currency}) och kortraden "
                         f"({line.amount} {line.currency}) skiljer sig - ändras inte")
    elif row.currency and line.currency:
        if _same_amount(t.get("original_amount"), t.get("original_amount_currency"),
                        row.amount, row.currency):
            pass
        elif _cross_currency_ok(db, row, line):
            changes["original_amount"] = f"{abs(float(row.amount)):.2f}"
            changes["original_amount_currency"] = row.currency.upper()
        else:
            notes.append(f"Belopp: {row.amount} {row.currency} kunde inte kontrolleras mot "
                         f"kortraden {line.amount} {line.currency} - ändras inte")
    if not t.get("date") and line.txn_date is not None:
        changes["date"] = line.txn_date.isoformat()
    return changes, notes


def plan_fill(db: Session, row: ProcessedMessage, line: CardLine | None, tx: dict,
              accounts: list[dict] | None = None) -> dict:
    """Vad boten skulle skriva till utkastet (torrkörning). Fyller bara tomma
    fält eller Bezalas standardvärden."""
    from app.services.card_line_coupling import _account_suggestion, _bezala_fill

    t = guard._unwrap(tx)
    snap = guard.status_snapshot(tx)
    tx_id = str(t.get("id") or row.bezala_transaction_id)
    base = {"transaction_id": tx_id, "message_id": row.message_id, "vendor": row.vendor,
            "bill_line_id": line.bill_line_id if line is not None else None,
            "state": guard.draft_state(snap)}
    if not guard.is_draft(snap):
        return {**base, "changes": {}, "skipped": [
            f"Inte ett utkast (state={guard.draft_state(snap)!r}) - boten skriver inte"],
            "before": {}, "after": {}}
    accounts = _accounts() if accounts is None else accounts
    acc = _account_suggestion(row, line)
    fill = _bezala_fill(row, line, account=acc)
    changes, skipped = plan_amount(db, row, line, t)

    # Beskrivning.
    cur_desc = (t.get("description") or "").strip()
    desc = fill.get("description")
    if not cur_desc and desc:
        changes["description"] = desc
    elif cur_desc and desc and cur_desc != desc:
        skipped.append("Beskrivning: texten i Bezala behålls (redan ifylld)")

    # Konto, moms, kostnadsställe på momsraden.
    lines = [ln for ln in (t.get("vat_lines") or []) if isinstance(ln, dict)]
    defaults = _default_account_ids(accounts)
    if len(lines) > 1:
        skipped.append("Momsrader: flera rader i Bezala - rörs inte")
    else:
        ln = lines[0] if lines else {}
        untouched = ln.get("expense_account_id") in ({None} | defaults)
        new: dict = {}
        if untouched:
            if acc.get("account_id") and not acc.get("missing"):
                new["expense_account_id"] = acc["account_id"]
            else:
                skipped.append("Konto: inget säkert förslag - fyll i själv")
            pct = _VAT_TO_TAX_PERCENTAGE.get(fill.get("vat") or "")
            if pct is not None:
                new["tax_percentage"] = pct
            elif fill.get("vat"):
                skipped.append(f"Moms: '{fill['vat']}' kan inte skrivas via API:t än - "
                               "välj den själv i Bezala")
            else:
                skipped.append("Moms: okänd - välj den själv i Bezala")
            if not ln.get("cost_center_ids"):
                new["cost_center_ids"] = [COST_CENTER_ID]
        else:
            skipped.append("Konto/moms: redan valt i Bezala - rörs inte")
        if new:
            if ln.get("id") is not None:
                new = {"id": ln["id"], **new}
            changes["vat_lines_attributes"] = [new]

    before = {k: t.get(k) for k in FILL_LOG_KEYS}
    after = dict(before)
    for k, v in changes.items():
        if k == "vat_lines_attributes":
            after["vat_lines"] = [{**(lines[0] if lines else {}),
                                   **{kk: vv for kk, vv in v[0].items() if kk != "id"}}]
        else:
            after[k] = v
    return {**base, "changes": changes, "skipped": skipped, "before": before, "after": after,
            "account": acc.get("name"), "vat": fill.get("vat"), "vat_rule": fill.get("vat_rule"),
            "flags": fill.get("flags") or []}


def apply_fill(client, transaction_id: str, plan: dict, *, db=None) -> dict:
    """Skriv planen (skyddad PUT: state läses före/efter, bara draft)."""
    out = dict(plan)
    if not draft_fill_enabled():
        out["written"] = {}
        out["result"] = f"{FLAG_DRAFT_FILL}=false - inget skrivet"
        return out
    if not plan.get("changes"):
        out["written"] = {}
        out["result"] = "Inget att fylla i"
        return out
    try:
        res = guard.guarded_put(client, transaction_id, dict(plan["changes"]), db=db,
                                allowed=guard.DRAFT_FILL_FIELDS, log_keys=FILL_LOG_KEYS,
                                operation="draft_fill")
    except guard.BezalaWriteBlocked as exc:
        out["written"] = {}
        out["result"] = f"Nekad: {exc}"
        return out
    except Exception as exc:  # noqa: BLE001
        guard.record_write(operation="draft_fill", transaction_id=transaction_id,
                           fields=plan["changes"], outcome="error", detail=str(exc), db=db)
        out["written"] = {}
        out["result"] = f"Bezala-fel: {str(exc)[:300]}"
        return out
    after = (res or {}).get("after_values") or {}
    confirmed, missing = [], []
    for k, v in plan["changes"].items():
        if k == "vat_lines_attributes":
            got = (after.get("vat_lines") or [{}])[0] if after.get("vat_lines") else {}
            ok = all(str(got.get(kk)) == str(vv) for kk, vv in v[0].items() if kk != "id")
        elif k == "original_amount":
            ok = _num(after.get(k)) == _num(v)
        else:
            ok = str(after.get(k) or "") == str(v)
        (confirmed if ok else missing).append(k)
    out["written"] = plan["changes"]
    out["after_read"] = after
    out["confirmed"] = confirmed
    out["not_confirmed"] = missing
    out["result"] = "Skrivet och bekräftat" if not missing else \
        "Skrivet, men Bezala visar inte allt: " + ", ".join(missing)
    return out


# ================================================================ verktyg


def fill_drafts(db: Session, transaction_ids: list[str] | None = None, *, apply: bool = False,
                days: int = 7, client=None, bill_lines: dict[str, str] | None = None) -> dict:
    """Torrkörning (apply=False) eller skrivning (apply=True + flaggan på) för
    redan skapade utkast. Raderar aldrig något."""
    from datetime import timedelta

    bill_lines = bill_lines or {}
    if not transaction_ids:
        cutoff = datetime.utcnow() - timedelta(days=max(1, int(days or 7)))
        rows = (db.query(ProcessedMessage)
                .filter(ProcessedMessage.deleted_at.is_(None))
                .filter(ProcessedMessage.bezala_transaction_id.isnot(None))
                .filter(ProcessedMessage.matched_at >= cutoff)
                .order_by(ProcessedMessage.matched_at).all())
        transaction_ids = [r.bezala_transaction_id for r in rows]
    own = client is None
    if own:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
    items = []
    try:
        accounts = _accounts()
        for tx_id in transaction_ids:
            tx_id = str(tx_id)
            row = _row_for_tx(db, tx_id)
            if row is None:
                items.append({"transaction_id": tx_id,
                              "error": "Inget kvitto i boten är kopplat till utkastet"})
                continue
            line = _line_for(db, tx_id, row, bill_lines.get(tx_id))
            try:
                tx = client.get_transaction(tx_id)
            except Exception as exc:  # noqa: BLE001
                items.append({"transaction_id": tx_id, "error": f"GET misslyckades: {exc}"})
                continue
            plan = plan_fill(db, row, line, tx, accounts=accounts)
            plan["coupling_before"] = verify_coupling(tx, line)
            if apply:
                plan = apply_fill(client, tx_id, plan, db=db)
                try:
                    tx2 = client.get_transaction(tx_id)
                    plan["coupling_after"] = verify_coupling(tx2, line)
                except Exception as exc:  # noqa: BLE001
                    plan["coupling_after"] = {"error": str(exc)[:200]}
            items.append(plan)
    finally:
        if own:
            client.close()
    return {
        "apply": bool(apply),
        "draft_fill_enabled": draft_fill_enabled(),
        "note": ("Torrkörning - inget skrivet till Bezala." if not apply else
                 ("Skrivet bara till utkast (state draft). Inget skickat för attest."
                  if draft_fill_enabled() else
                  f"{FLAG_DRAFT_FILL}=false - inget skrivet till Bezala.")),
        "count": len(items),
        "items": items,
    }


def repair_broken_drafts(db: Session, *, apply: bool = False, client=None) -> dict:
    """De 8 utkasten från 2026-11-25 (inte kopplade, fel belopp)."""
    out = fill_drafts(db, list(BROKEN_DRAFTS_2026_11_25), apply=apply, client=client,
                      bill_lines=BROKEN_DRAFTS_2026_11_25)
    if apply and draft_fill_enabled():
        # Kopplingen kontrolleras igen när Bezala hunnit läsa (60-90 s).
        for tx_id, bid in BROKEN_DRAFTS_2026_11_25.items():
            schedule_verification(tx_id, bill_line_id=bid, attempt=2)
        out["next_check"] = "Kopplingen läses om automatiskt om ca 75 s"
    return out


# ================================================================ engångsjobb

TASK_RESET = "c60_reset_unverified_v1"


def reset_broken_card_lines(db: Session, *, force: bool = False) -> list[dict] | None:
    """De 8 kortraderna tillbaka till 'missing' i botens egen tabell (inte i
    Bezala). Kvittona markeras coupling_unverified (utkastet finns kvar)."""
    from app.models import MaintenanceTask

    if not force and db.query(MaintenanceTask).filter(MaintenanceTask.name == TASK_RESET).first():
        return None
    out = []
    for tx_id, bid in BROKEN_DRAFTS_2026_11_25.items():
        line = db.query(CardLine).filter(CardLine.bill_line_id == bid).first()
        row = _row_for_tx(db, tx_id)
        item = {"bill_line_id": bid, "transaction_id": tx_id,
                "before": line.status if line is not None else None}
        if line is not None and line.status in ("resolved", STATUS_UNVERIFIED):
            line.status = "missing"
            line.resolved_at = None
            line.bezala_transaction_id = tx_id
            line.note = (f"C60: utkast {tx_id} skapades 2026-11-25 men kopplades inte till "
                         "kortraden i Bezala - återställd till saknas")
        if row is not None:
            row.bezala_upload_status = STATUS_UNVERIFIED
            row.bezala_error_message = ("Koppling inte verifierad: utkastet är inte kopplat till "
                                        "kortraden i Bezala (2026-11-25)")
        item["after"] = line.status if line is not None else None
        out.append(item)
    db.add(MaintenanceTask(name=TASK_RESET))
    db.commit()
    logger.info("C60: återställda kortrader: %s", out)
    return out


# ================================================================ kvittovyn


def writes_for(db: Session, transaction_id: str | None, limit: int = 20) -> list[dict]:
    """Botens skrivningar till ett Bezala-utkast (före/efter), nyast först."""
    if not transaction_id:
        return []
    from app.models import BezalaWriteLog

    try:
        rows = (db.query(BezalaWriteLog)
                .filter(BezalaWriteLog.transaction_id == str(transaction_id))
                .order_by(BezalaWriteLog.id.desc()).limit(limit).all())
    except Exception:  # noqa: BLE001
        db.rollback()
        return []
    return [{
        "when": r.created_at.isoformat() if r.created_at else None,
        "operation": r.operation,
        "outcome": r.outcome,
        "fields": r.fields,
        "before": (r.state_before or {}).get("values") if isinstance(r.state_before, dict)
        else None,
        "after": (r.state_after or {}).get("values") if isinstance(r.state_after, dict)
        else None,
        "state_before": (r.state_before or {}).get("state")
        if isinstance(r.state_before, dict) else None,
        "state_after": (r.state_after or {}).get("state")
        if isinstance(r.state_after, dict) else None,
        "detail": r.detail,
    } for r in rows]
