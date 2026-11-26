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

# Bezalas momsval → momsrad. Läst med GET på Mikkos egna utkast 2026-11-25/26:
#   0 %        → vat_id 1,    tax_percentage "0.0"   (Arlanda 5594765)
#   25,5 %     → vat_id 864,  tax_percentage "0.255" (Moovy 5594768)
#   Purchases Abroad (Non-EU) → vat_id 1914, tax_percentage "0.0" (Lovable 5594767)
# vat_code_id är alltid null. Övriga val (EU, 10 %, 13,5 %) har okänt vat_id
# och skrivs inte - Mikko väljer dem själv.
_VAT_TO_LINE = {
    "0%": {"vat_id": 1, "tax_percentage": "0.0"},
    "25,5%": {"vat_id": 864, "tax_percentage": "0.255"},
    "Purchases Abroad (Non-EU)": {"vat_id": 1914, "tax_percentage": "0.0"},
}

FILL_LOG_KEYS = ("description", "date", "original_amount", "original_amount_currency",
                 "billed_amount", "billed_amount_currency", "connected_to_bill_line",
                 "bill_id", "vat_lines", "emissions")
CV_KEY = "conditional_values_attributes"


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


def bezala_read_zero(t: dict) -> bool:
    """C61 — Bezala läste 0 (eller inget) som kvittots belopp."""
    t = guard._unwrap(t)
    v = _num(t.get("original_amount"))
    return v is None or abs(v) < 0.005


def verify_coupling(tx: dict, line: CardLine | None) -> dict:
    """Kontroll efter koppling: kopplad till kortraden, bill_id satt, belopp
    stämmer med kortraden, datum satt. Problem beskrivs på enkel svenska."""
    t = guard._unwrap(tx)
    problems: list[str] = []
    snap = guard.status_snapshot(tx)
    # Ett utlägg som lämnat utkastläget (t.ex. skickat för attest i Bezalas
    # gränssnitt) kan ändå vara rätt kopplat - state är inget kopplingsfel.
    # Boten skriver aldrig till det (guarded_put kräver "draft").
    if t.get("connected_to_bill_line") is not True:
        problems.append("Utkastet är inte kopplat till kortraden i Bezala")
    if not t.get("bill_id"):
        problems.append("bill_id saknas i Bezala")
    if line is not None and line.amount is not None:
        ok = (_same_amount(t.get("billed_amount"), t.get("billed_amount_currency"),
                           line.amount, line.currency)
              or _same_amount(t.get("original_amount"), t.get("original_amount_currency"),
                              line.amount, line.currency))
        if ok and bezala_read_zero(t) and abs(float(line.amount)) >= 0.005:
            problems.append(f"Bezala läste 0 som belopp ({t.get('original_amount')} "
                            f"{t.get('original_amount_currency') or ''})".replace("  ", " "))
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
                      client=None, fill: bool = True, attempt: int = 1,
                      c64_only: bool = False) -> dict:
    """Läs utkastet (GET). Fyll i det om BEZALA_DRAFT_FILL_ENABLED (bara
    draft), läs igen och sätt kortraden till resolved bara om kopplingen
    verifierats. Annars coupling_unverified.

    C64: med automatiken på (`c64_autofill.auto_fill_on`) verifieras
    kopplingen FÖRST; ifyllnaden (konto, moms, beskrivning, km ...) körs bara
    när kopplingen är verifierad. Före verifieringen rättas bara belopp/datum
    som Bezala läst fel (krävs för att kopplingen ska gå igenom).
    `c64_only`: fyll bara i om C64-automatiken är på (manuell uppladdning)."""
    from app.services import c64_autofill as c64

    tx_id = str(transaction_id)
    row = _row_for_tx(db, tx_id)
    line = _line_for(db, tx_id, row, bill_line_id)
    c64_on = bool(fill and row is not None and c64.auto_fill_on(db))
    if c64_only and not c64_on:
        fill = False
    own = client is None
    if own:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
    auto = None
    try:
        tx = client.get_transaction(tx_id)
        if row is not None and guard.is_draft(guard.status_snapshot(tx)):
            c64.record_seen(row, tx)
        fill_result = None
        can_fill = (fill and draft_fill_enabled() and row is not None
                    and guard.is_draft(guard.status_snapshot(tx)))
        if can_fill and c64_on:
            v = verify_coupling(tx, line)
            if not v["ok"] and attempt == 1:
                amount, notes = plan_amount(db, row, line, guard._unwrap(tx))
                plan = {"transaction_id": tx_id, "changes": amount, "skipped": notes,
                        "before": {k: guard._unwrap(tx).get(k) for k in FILL_LOG_KEYS},
                        "after": {}, "mode": "c64_amount_before_verification"}
                fill_result = apply_fill(client, tx_id, plan, db=db) if amount else plan
                if amount:
                    tx = client.get_transaction(tx_id)
                    if fill_result.get("written"):
                        c64.record_seen(row, tx, after_write=True)
        elif can_fill:
            from app.services import c62_conditional as c62

            plan = plan_fill(db, row, line, tx, conditional=c62.read_values(client, tx_id))
            fill_result = apply_fill(client, tx_id, plan, db=db) if plan["changes"] else plan
            if plan["changes"]:
                tx = client.get_transaction(tx_id)
                if fill_result.get("written"):
                    c64.record_seen(row, tx, after_write=True)
        v = verify_coupling(tx, line)
        if v["ok"] and c64_on and fill:
            auto = c64.auto_fill_after_verified(db, row, line, client, tx_id)
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
    if auto is not None:
        out["auto_fill"] = auto
    wrote_amount = bool(fill_result and set(fill_result.get("written") or {})
                        & {"original_amount", "date"})
    if not v["ok"] and wrote_amount and attempt == 1:
        # Bezala kan behöva en stund för att koppla efter att beloppet rättats.
        schedule_verification(tx_id, bill_line_id=out["bill_line_id"], attempt=2,
                              c64_only=c64_only)
        out["next_check"] = "om ca 75 s"
    return out


def schedule_verification(transaction_id: str, *, bill_line_id: str | None = None,
                          attempt: int = 1, delay: float | None = None,
                          c64_only: bool = False) -> None:
    """Läs om utkastet efter 60-90 s (Bezala läser kvittot i bakgrunden)."""
    if os.environ.get("C60_VERIFY_SCHEDULE", "true").strip().lower() == "false":
        return
    delay = _env_float("C60_VERIFY_DELAY_SECONDS", 75.0) if delay is None else delay

    def work():
        from app.db import SessionLocal

        db = SessionLocal()
        try:
            res = verify_and_update(db, transaction_id, bill_line_id=bill_line_id,
                                    attempt=attempt, c64_only=c64_only)
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
    if abs(float(row.amount)) < 0.005:
        notes.append("Belopp: kvittots belopp är 0 i boten - ändras inte (gissas aldrig)")
        return changes, notes
    # C61 — Skånetrafiken: Bezala läste "Varav moms 0,0%: 0 SEK" som totalen.
    # billed_amount kan ändå visa kortradens belopp, så 0 räknas alltid som fel.
    if not bezala_read_zero(t) and _same_amount(
            t.get("billed_amount"), t.get("billed_amount_currency"), line.amount, line.currency):
        pass
    elif (row.currency or "").upper() == (line.currency or "").upper():
        if _same_amount(row.amount, row.currency, line.amount, line.currency):
            if not _same_amount(t.get("original_amount"), t.get("original_amount_currency"),
                                line.amount, line.currency):
                # C64 — tecknet följer kortraden (aldrig +22,95 på en -22,95-rad).
                changes["original_amount"] = f"{float(line.amount):.2f}"
                changes["original_amount_currency"] = (line.currency or "").upper()
        else:
            notes.append(f"Belopp: kvittot ({row.amount} {row.currency}) och kortraden "
                         f"({line.amount} {line.currency}) skiljer sig - ändras inte")
    elif row.currency and line.currency:
        if _same_amount(t.get("original_amount"), t.get("original_amount_currency"),
                        row.amount, row.currency):
            pass
        elif _cross_currency_ok(db, row, line):
            sign = -1 if float(line.amount) < 0 else 1
            changes["original_amount"] = f"{sign * abs(float(row.amount)):.2f}"
            changes["original_amount_currency"] = row.currency.upper()
        else:
            notes.append(f"Belopp: {row.amount} {row.currency} kunde inte kontrolleras mot "
                         f"kortraden {line.amount} {line.currency} - ändras inte")
    if not t.get("date") and line.txn_date is not None:
        changes["date"] = line.txn_date.isoformat()
    return changes, notes


def plan_fill(db: Session, row: ProcessedMessage, line: CardLine | None, tx: dict,
              accounts: list[dict] | None = None, conditional: dict | None = None,
              link: bool = True) -> dict:
    """Vad boten skulle skriva till utkastet (torrkörning). Fyller bara tomma
    fält eller Bezalas standardvärden. `conditional` = utkastets Vehicle/km/
    passagerare lästa med c62_conditional.read_values (None = okänt → skrivs inte)."""
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
            vat_line = _VAT_TO_LINE.get(fill.get("vat") or "")
            if vat_line is not None and not ln.get("vat_id"):
                new.update(vat_line)
            elif vat_line is not None:
                skipped.append("Moms: redan vald i Bezala - rörs inte")
            elif fill.get("vat"):
                skipped.append(f"Moms: '{fill['vat']}' har inget känt vat_id - "
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

    # C62 — Vehicle, Sträcka (km), Antal passagerare (conditional_values).
    from app.services import c62_conditional as c62

    cv = c62.plan(db, row, tx, conditional, link=link)
    if cv["rows"]:
        changes[CV_KEY] = cv["rows"]
    skipped.extend(cv["skipped"])

    before = {k: t.get(k) for k in FILL_LOG_KEYS}
    before["conditional"] = cv["before"]
    after = dict(before)
    if cv["fields"]:
        after["conditional"] = {**(cv["before"] or {}), **cv["fields"]}
    for k, v in changes.items():
        if k == CV_KEY:
            continue
        if k == "vat_lines_attributes":
            after["vat_lines"] = [{**(lines[0] if lines else {}),
                                   **{kk: vv for kk, vv in v[0].items() if kk != "id"}}]
        else:
            after[k] = v
    return {**base, "changes": changes, "skipped": skipped, "before": before, "after": after,
            "account": acc.get("name"), "vat": fill.get("vat"), "vat_rule": fill.get("vat_rule"),
            "flags": fill.get("flags") or [], "distance_fill": cv}


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
    from app.services import c62_conditional as c62

    try:
        res = guard.guarded_put(client, transaction_id, dict(plan["changes"]), db=db,
                                allowed=guard.DRAFT_FILL_FIELDS, log_keys=FILL_LOG_KEYS,
                                operation="draft_fill",
                                extra_read=c62.read_values if CV_KEY in plan["changes"]
                                else None)
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
        if k == CV_KEY:
            fields = (plan.get("distance_fill") or {}).get("fields") or {}
            _, miss = c62.confirm(after.get("conditional"), fields)
            ok = not miss
        elif k == "vat_lines_attributes":
            ok = _lines_confirmed(after.get("vat_lines") or [], v)
        elif k == "original_amount":
            ok = _num(after.get(k)) == _num(v)
        else:
            ok = str(after.get(k) or "") == str(v)
        (confirmed if ok else missing).append(k)
    out["written"] = plan["changes"]
    out["after_read"] = after
    if CV_KEY in plan["changes"]:
        out["emissions_before"] = (plan.get("before") or {}).get("emissions")
        out["emissions_after"] = after.get("emissions")
    out["confirmed"] = confirmed
    out["not_confirmed"] = missing
    out["result"] = "Skrivet och bekräftat" if not missing else \
        "Skrivet, men Bezala visar inte allt: " + ", ".join(missing)
    return out


def _same_value(got, want) -> bool:
    if isinstance(want, list) or isinstance(got, list):
        return sorted(str(x) for x in (got or [])) == sorted(str(x) for x in (want or []))
    a, b = _num(got), _num(want)
    if a is not None and b is not None:
        return a == b
    return str(got) == str(want)


def _lines_confirmed(got_lines: list, entries: list[dict]) -> bool:
    """C64 — varje skickad kostnadsrad syns i Bezala efter skrivningen
    (borttagna 0-rader syns inte längre)."""
    got_lines = [g for g in got_lines if isinstance(g, dict)]
    for e in entries:
        same_id = [g for g in got_lines
                   if e.get("id") is not None and str(g.get("id")) == str(e["id"])]
        if e.get("_destroy"):
            if same_id:
                return False
            continue
        got = same_id[0] if same_id else (got_lines[0] if got_lines else {})
        if not all(_same_value(got.get(kk), vv) for kk, vv in e.items() if kk != "id"):
            return False
    return True


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
        by_default = True
    else:
        by_default = False
    own = client is None
    if own:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
    items = []
    try:
        if by_default:
            # C62 — även alla utkast i Bezala (state draft) som boten har ett kvitto för.
            transaction_ids = list(dict.fromkeys(
                [str(x) for x in transaction_ids] + _bezala_draft_ids(client)))
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
            from app.services import c62_conditional as c62

            plan = plan_fill(db, row, line, tx, accounts=accounts,
                             conditional=c62.read_values(client, tx_id), link=apply)
            plan["coupling_before"] = verify_coupling(tx, line)
            try:
                plan["distance"] = plan_distance(db, row, tx)
            except Exception as exc:  # noqa: BLE001 - km-förslaget får aldrig fälla verktyget
                db.rollback()
                plan["distance"] = {"error": str(exc)[:200]}
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


def _bezala_draft_ids(client) -> list[str]:
    """Alla transaktioner i Bezala med state "draft" (GET /transactions)."""
    try:
        txs = client.list_transactions() or []
    except Exception:  # noqa: BLE001
        logger.exception("C62: listan över Bezala-utkast kunde inte läsas")
        return []
    out = []
    for tx in txs:
        t = guard._unwrap(tx) if isinstance(tx, dict) else {}
        if guard.is_draft(guard.status_snapshot(t)) and t.get("id") is not None:
            out.append(str(t["id"]))
    return out


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
TASK_REVERIFY = "c60_reverify_v2"


def needs_reverify(db: Session) -> bool:
    """Engång: läs om de 8 efter hotfixen (state räknas inte som kopplingsfel)."""
    from app.models import MaintenanceTask

    if db.query(MaintenanceTask).filter(MaintenanceTask.name == TASK_REVERIFY).first():
        return False
    db.add(MaintenanceTask(name=TASK_REVERIFY))
    db.commit()
    return True


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


def _money(amount, currency) -> str:
    v = _num(amount)
    return f"{v:.2f} {currency or ''}".strip() if v is not None else f"tomt {currency or ''}".strip()


def amount_fix(writes: list[dict]) -> dict | None:
    """C61 — kvittovyn: senaste gången boten rättade beloppet (före/efter)."""
    for w in writes or []:
        fields = w.get("fields") or {}
        if w.get("outcome") != "ok" or "original_amount" not in fields:
            continue
        b, a = w.get("before") or {}, w.get("after") or {}
        before = _money(b.get("original_amount"), b.get("original_amount_currency"))
        after = _money(a.get("original_amount") or fields.get("original_amount"),
                       a.get("original_amount_currency")
                       or fields.get("original_amount_currency"))
        return {"when": w.get("when"), "before": before, "after": after,
                "text": f"Boten rättade beloppet i Bezala: {before} → {after}"}
    return None


# ================================================================ Del 3b: km, Vehicle, passagerare
#
# Bezalas GET /transactions/{id} visar bara `emissions` (kg CO2e, text, t.ex.
# "24.77" på Arlanda Express 5594765 = ca 0,454 kg/EUR, alltså beloppsbaserat)
# och `participants` (null). Fälten "Sträcka (km)" och "Vehicle" syns inte i
# API-svaret, så fältnamnen är okända. Boten skriver dem därför INTE - den
# visar bara vad den skulle fylla i (torrkörning) tills fältnamnen är kända.

LEG_MODE_TO_VEHICLE = {
    "flight": "Flyg",
    "train": "Tåg",
    "bus": "Buss",
    "taxi": "Taxi",
    "car_phev": "Bil (laddhybrid)",
}


def _flight_no_key(no: str | None) -> str | None:
    m = re.match(r"^\s*([A-Z0-9]{2})\s*0*(\d{1,4})\s*$", (no or "").upper())
    return f"{m.group(1)}{int(m.group(2))}" if m else None


def legs_for_receipt(db: Session, row: ProcessedMessage, *, link: bool = True) -> list:
    """Ben som hör till kvittot: ben med kvittots message_id, och för flyg
    ben med samma flygnummer (samma eller närliggande datum).

    C64: taxi/tåg utan ben → ett ben i resan med samma typ, datum och
    leverantör kopplas till kvittot (link=False: bara visa, spara inget).
    Kvittots km vinner över rutttabellen."""
    from app.models import TripLeg
    from app.services import c64_autofill as c64

    legs = (db.query(TripLeg)
            .filter(TripLeg.receipt_message_id == row.message_id)
            .filter(TripLeg.excluded.is_(False)).all())
    if not legs:
        legs = c64.link_legs(db, row, apply=link)
    elif link:
        c64.apply_receipt_km(db, row, legs)
    seen = {lg.id for lg in legs}
    segs = segments_for(db, row) if row.category == "Flyg" or row.flight_segments else []
    for seg in segs:
        key = _flight_no_key(seg.get("flight_no"))
        d = _as_date(seg.get("date"))
        if not key:
            continue
        q = db.query(TripLeg).filter(TripLeg.mode == "flight").filter(TripLeg.excluded.is_(False))
        for lg in q.all():
            if lg.id in seen or _flight_no_key(lg.flight_no) != key:
                continue
            if d is not None and lg.leg_date is not None and abs((lg.leg_date - d).days) > 1:
                continue
            legs.append(lg)
            seen.add(lg.id)
    return sorted(legs, key=lambda lg: (lg.leg_date or date.min, lg.seq or 0))


def plan_distance(db: Session, row: ProcessedMessage, tx: dict | None = None) -> dict:
    """Km, Vehicle och passagerare för utkastet (bara förslag, skrivs inte).
    Gissar aldrig km: saknas km eller är riktningen okänd blir km tomt.
    Rutttabellens uppskattning används bara för tåg/buss/taxi (C63).
    Mikkos manuella km vinner alltid."""
    t = guard._unwrap(tx) if tx else {}
    legs = legs_for_receipt(db, row, link=False)
    out: dict = {
        "legs": [{"leg_id": lg.id, "trip_id": lg.trip_id,
                  "date": lg.leg_date.isoformat() if lg.leg_date else None,
                  "mode": lg.mode, "from": lg.from_place, "to": lg.to_place, "km": lg.km,
                  "source": lg.source, "is_estimate": bool(lg.is_estimate),
                  "km_reason": getattr(lg, "km_reason", None), "flight_no": lg.flight_no}
                 for lg in legs],
        "km": None, "vehicle": None, "passengers": 1,
        "bezala_emissions_kg": t.get("emissions"),
        "bezala_participants": t.get("participants"),
        "written": False,
        "why_not_written": "Förslag - skrivningen sker via distance_fill (C62).",
        "notes": [],
    }
    if not legs:
        out["notes"].append("Inget ben kopplat till kvittot - km saknas")
        return out
    modes = {lg.mode for lg in legs}
    out["vehicle"] = LEG_MODE_TO_VEHICLE.get(next(iter(modes))) if len(modes) == 1 else None
    if len(modes) > 1:
        out["notes"].append(f"Flera färdsätt ({', '.join(sorted(modes))}) - Vehicle lämnas tomt")
    # C63 — samma regel som skrivningen: rutttabellens uppskattning får
    # användas för tåg/buss/taxi (märks "km uppskattat, rutttabell").
    from app.services.c62_conditional import km_basis

    basis = km_basis(legs)
    if not basis["ok"]:
        out["notes"].append(basis["note"] or "km saknas")
        return out
    total = float(basis["km"])
    out["km_estimate"] = basis["estimate"]
    out["km_kind"] = "uppskattad" if basis["estimate"] else "riktig"
    if basis["note"]:
        out["notes"].append(basis["note"])
    out["km"] = round(total, 1)
    # C61 — Bezalas utsläppssiffra är beloppsbaserad; visa kg per km som jämförelse.
    kg = _num(t.get("emissions"))
    if kg is not None and total > 0:
        out["bezala_kg_per_km"] = round(kg / total, 3)
    return out


def distance_fix(writes: list[dict]) -> dict | None:
    """C62 — kvittovyn: senaste gången boten fyllde Vehicle/km/passagerare
    (före/efter per fält och Bezalas utsläppssiffra före/efter)."""
    from app.services.c62_conditional import FIELD_KEYS, value_of

    for w in writes or []:
        fields = w.get("fields") or {}
        if w.get("outcome") != "ok" or CV_KEY not in fields:
            continue
        b, a = w.get("before") or {}, w.get("after") or {}
        rows = []
        for key in FIELD_KEYS.values():
            before = value_of(b.get("conditional"), key)
            after = value_of(a.get("conditional"), key)
            if before != after:
                rows.append({"field": key, "before": before, "after": after})
        return {"when": w.get("when"), "fields": rows,
                "emissions_before": b.get("emissions"), "emissions_after": a.get("emissions"),
                "text": "Boten fyllde i " + ", ".join(
                    f"{r['field']}: {r['before'] or 'tomt'} → {r['after'] or 'tomt'}"
                    for r in rows) + (f" (Bezalas utsläpp {b.get('emissions')} → "
                                      f"{a.get('emissions')} kg)" if a.get("emissions") else "")}
    return None
