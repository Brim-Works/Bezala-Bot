"""C64 — automatisk ifyllnad av nya Bezala-utkast efter verifierad koppling.

Bakgrund (prod 2026-11-26): ett taxikvitto laddades upp och kopplades direkt
till kortraden. Verifieringen startade aldrig (bara couple_receipt och
kopplingsknappen startade den), så utkastet blev halvfärdigt: tom beskrivning,
Bezalas platshållarkonto 4060 och Bezalas momskod 1913/1914. Förslaget vid
uppladdningen sa 25,5 % moms, ifyllnaden 0 % - två olika regler.

Hårdregler (ändras inte):
* Inget skickas för attest. Bara state "draft" skrivs till; state läses före
  och efter varje skrivning (bezala_write_guard.guarded_put).
* Kräver BEZALA_DRAFT_FILL_ENABLED=true (false i koden) OCH att Mikko sagt ja
  i chatten (`set_auto_fill`, sparas i databasen, av som standard).
* Mikkos egna värden skrivs aldrig över. Bara på ett ORÖRT utkast får boten
  byta Bezalas standardvärden (konto 4060, momskod 1913/1914, tecken, tomma
  0-rader). Tomma fält fylls alltid.
* EN regel för konto och moms (`draft_rule`) - både förslaget vid
  uppladdningen och ifyllnaden använder den.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.models import CardLine, ProcessedMessage
from app.services import bezala_write_guard as guard

logger = logging.getLogger(__name__)

SWITCH_KEY = "c64_auto_fill"
STATUS_DONE = "done"
STATUS_UNVERIFIED = "fill_unverified"
STATUS_SKIPPED = "skipped"
UNTOUCHED_SECONDS = 60

# Bezalas platshållarkonto "4060 Tiliöimättömät ostolaskut" (id 67121) och
# momskoderna Bezala själv sätter vid skapandet (1913 EU, 1914 Non-EU).
PLACEHOLDER_ACCOUNT_IDS = frozenset({67121})
BEZALA_DEFAULT_VAT_IDS = frozenset({1913, 1914})


# ================================================================ på/av


def auto_fill_on(db: Session | None = None) -> bool:
    """Automatiken är på bara med BEZALA_DRAFT_FILL_ENABLED=true OCH Mikkos ja
    (bot_flags c64_auto_fill=true). Kan flaggan inte läsas: av."""
    from app.services.c60_draft import draft_fill_enabled

    if not draft_fill_enabled():
        return False
    try:
        with guard._session(db) as s:
            return (guard._get_flag(s, SWITCH_KEY) or "").strip().lower() == "true"
    except Exception as exc:  # noqa: BLE001
        if not guard._missing_table(exc):
            logger.exception("C64: kunde inte läsa c64_auto_fill - behandlar som av")
        return False


def set_auto_fill(db: Session, on: bool, reason: str | None = None) -> dict:
    """Slå på/av automatiken. Bara efter Mikkos ja i chatten."""
    from app.services.c60_draft import FLAG_DRAFT_FILL, draft_fill_enabled

    guard._set_flag(db, SWITCH_KEY, "true" if on else "false",
                    reason or ("Mikko said yes in the chat" if on else "turned off"))
    return {"c64_auto_fill": bool(on), FLAG_DRAFT_FILL: draft_fill_enabled(),
            "active": auto_fill_on(db),
            "note": ("Automatiken är på för nya utkast (bara state draft)." if auto_fill_on(db)
                     else "Automatiken är av - inget skrivs automatiskt.")}


# ================================================================ kvittots km/tid/ort

_RIDE_KM_RE = re.compile(
    r"(?:färd|fard|resa|sträcka|stracka|körsträcka|distance|distans|matka|ride|trip|km-?tal)"
    r"\s*[:=]?\s*(\d{1,4}(?:[.,]\d{1,3})?)\s*km\b", re.IGNORECASE)
_TIME_SPAN_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\s*[-–]\s*([01]?\d|2[0-3])[:.]([0-5]\d)\b")


def _row_texts(row: ProcessedMessage) -> list[str]:
    return [str(v) for v in (row.summary, row.ai_description_en, row.subject,
                             row.manual_comment, row.file_name) if v]


def receipt_ride(row: ProcessedMessage) -> dict:
    """km, tid och ort från kvittot: AI:ns ride_info först, annars texten
    ("Färd: 1.10 km"). Gissar aldrig."""
    from app.services.trip_legs import parse_km

    ri = row.ride_info if isinstance(getattr(row, "ride_info", None), dict) else {}
    out = {"km": ri.get("km"), "start": ri.get("start"), "end": ri.get("end"),
           "city": ri.get("city"), "source": "receipt" if ri else None}
    texts = _row_texts(row)
    if out["km"] is None:
        for t in texts:
            m = _RIDE_KM_RE.search(t)
            if m:
                out["km"] = float(m.group(1).replace(",", "."))
                break
        else:
            if receipt_mode(row) == "taxi":
                for t in texts:
                    km = parse_km(t)
                    if km is not None:
                        out["km"] = km
                        break
        if out["km"] is not None:
            out["source"] = "receipt text"
    if out["start"] is None:
        for t in texts:
            m = _TIME_SPAN_RE.search(t)
            if m:
                out["start"] = f"{int(m.group(1)):02d}:{m.group(2)}"
                out["end"] = f"{int(m.group(3)):02d}:{m.group(4)}"
                break
    return out


def receipt_mode(row: ProcessedMessage) -> str | None:
    from app.services.trip_legs import receipt_mode as _mode

    text = " ".join(str(v) for v in (row.vendor, row.sender, row.subject, row.summary,
                                     row.file_name, row.ai_description_en,
                                     row.bezala_payment_merchant) if v)
    return _mode(row.category, text)


# ================================================================ Del 4: koppla benet


def _receipt_day(row: ProcessedMessage) -> date | None:
    from app.services.trip_completeness import _parse_day

    return _parse_day(row.receipt_date) or _parse_day(row.bezala_payment_date)


def _vendor_word(row: ProcessedMessage) -> str | None:
    from app.services.c50_accounts import vendor_key

    return vendor_key(row.vendor)


def _locked(leg) -> bool:
    return leg.set_by == "user" or (leg.source == "manual" and leg.km is not None)


def link_legs(db: Session, row: ProcessedMessage, *, apply: bool = True) -> list:
    """Kvittot har inget ben: koppla ETT ben i resan när typ (taxi/tåg),
    datum och leverantör stämmer (benets anteckning nämner leverantören eller
    är tom). Flera kandidater = oklart, inget kopplas."""
    from app.models import TripLeg

    mode = receipt_mode(row)
    day = _receipt_day(row)
    if mode not in ("taxi", "train") or day is None:
        return []
    known = {m for (m,) in db.query(ProcessedMessage.message_id)
             .filter(ProcessedMessage.deleted_at.is_(None)).all()}
    word = _vendor_word(row)
    cands = []
    for lg in (db.query(TripLeg).filter(TripLeg.leg_date == day)
               .filter(TripLeg.mode == mode).filter(TripLeg.excluded.is_(False)).all()):
        if lg.receipt_message_id and lg.receipt_message_id in known:
            continue
        note = _fold(lg.note or "")
        manual = lg.set_by == "user" or lg.source == "manual"
        if note.strip() and not manual and not (word and word in note):
            continue  # ett automatiskt ben från en annan leverantör
        cands.append(lg)
    if len(cands) != 1:
        return []
    leg = cands[0]
    if apply:
        leg.receipt_message_id = row.message_id
        apply_receipt_km(db, row, [leg], commit=False)
        db.commit()
        logger.info("C64: ben %s kopplat till kvitto %s (%s %s)", leg.id, row.message_id,
                    mode, day)
    return [leg]


def _fold(text: str) -> str:
    from app.services.c50_accounts import _norm

    return _norm(text)


def apply_receipt_km(db: Session, row: ProcessedMessage, legs: list, *,
                     commit: bool = True) -> None:
    """Kvittots km vinner över rutttabellen (aldrig över Mikkos manuella km)."""
    if len(legs) != 1:
        return
    leg = legs[0]
    km = receipt_ride(row)["km"]
    if km is None or leg.mode not in ("taxi", "train", "bus"):
        return
    if leg.km is not None and _locked(leg):
        return
    if leg.source == "receipt" and not leg.is_estimate and leg.km is not None:
        return
    leg.km = float(km)
    leg.source = "receipt"
    leg.is_estimate = False
    leg.km_reason = None
    leg.note = ((leg.note or row.vendor or "Receipt").split(" - km not on receipt")[0]
                + "; km from receipt")
    if commit:
        db.commit()


# ================================================================ Del 5: taxibeskrivning

_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")
CONFERENCE_TEXT = "between office and customer conference"
_CONFERENCE_RE = re.compile(r"office\s+and\s+customer\s+conference|customer\s+conference",
                            re.IGNORECASE)


def _conference_noted(db: Session | None, row: ProcessedMessage, day: date | None) -> bool:
    """Bara om Mikko skrivit det i resans anteckning eller benets note."""
    if db is None or day is None:
        return False
    from app.models import CalendarTrip, CalendarTripDay, TripLeg

    texts = [lg.note for lg in db.query(TripLeg).filter(TripLeg.leg_date == day).all()
             if lg.receipt_message_id in (None, row.message_id)]
    for trip in (db.query(CalendarTrip).filter(CalendarTrip.start_date <= day)
                 .filter(CalendarTrip.end_date >= day).all()):
        texts.append(trip.purpose)
        for d in db.query(CalendarTripDay).filter(CalendarTripDay.trip_id == trip.id).all():
            texts += [getattr(d, "free_meal_note", None), getattr(d, "purpose", None)]
    texts.append(row.manual_comment)
    return any(t and _CONFERENCE_RE.search(str(t)) for t in texts)


def taxi_description(db: Session | None, row: ProcessedMessage,
                     line: CardLine | None) -> str | None:
    """"Taxi ride within <ort>, <km> km, <datum>". Ort saknas → None (gissas inte)."""
    if receipt_mode(row) != "taxi":
        return None
    from app.services.trip_legs import fmt_km

    ride = receipt_ride(row)
    city = (ride.get("city") or "").strip()
    if not city:
        return None
    day = (line.txn_date if line is not None and line.txn_date else None) or _receipt_day(row)
    head = f"Taxi ride within {city}"
    if _conference_noted(db, row, day):
        head += f" {CONFERENCE_TEXT}"
    parts = [head]
    if ride.get("km") is not None:
        parts.append(f"{fmt_km(float(ride['km']))} km")
    if day is not None:
        parts.append(f"{day.day} {_MONTHS[day.month - 1]} {day.year}")
    return ", ".join(parts)


# ================================================================ EN regel


def draft_rule(db: Session | None, row: ProcessedMessage, line: CardLine | None) -> dict:
    """Konto, moms och beskrivning för ett kvitto. Samma svar för förslaget
    vid uppladdningen och för ifyllnaden."""
    from app.services.c51_vat import refund_description
    from app.services.c60_draft import _VAT_TO_LINE
    from app.services.card_line_coupling import _account_suggestion, _bezala_fill

    acc = _account_suggestion(row, line)
    fill = _bezala_fill(row, line, account=acc)
    desc = fill.get("description")
    if not (row.ai_description_en or "").strip():
        # Del 5: kvittot saknar engelsk beskrivning (svensk summary duger inte).
        td = taxi_description(db, row, line)
        if td:
            desc = refund_description(td) if fill.get("refund") else td
    vat_line = _VAT_TO_LINE.get(fill.get("vat") or "")
    return {
        "account_id": acc.get("account_id") if not acc.get("missing") else None,
        "account": acc.get("name"),
        "account_source": acc.get("source"),
        "vat": fill.get("vat"),
        "vat_line": dict(vat_line) if vat_line else None,
        "vat_rule": fill.get("vat_rule"),
        "flags": fill.get("flags") or [],
        "description": desc,
        "refund": bool(fill.get("refund")),
    }


def apply_rule_to_params(params: dict, rule: dict) -> dict:
    """Förslaget vid uppladdningen (match-to-bezala) får regelns konto och
    moms. Skrivs bara om BEZALA_METADATA_PUT_ENABLED (false) - annars syns det
    i put_skipped-loggen."""
    for ln in params.get("vat_lines_attributes") or []:
        if rule.get("account_id"):
            ln["expense_account_id"] = rule["account_id"]
        if rule.get("vat_line"):
            ln.update(rule["vat_line"])
            ln.pop("vat_code_id", None)
    params["rule"] = {k: rule.get(k) for k in ("account", "account_id", "vat", "vat_rule")}
    return params


# ================================================================ orört utkast


def _num(v) -> float | None:
    try:
        return round(float(v), 2) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=None) - (d.utcoffset() or timedelta(0)) if d.tzinfo else d


def _lines(t: dict) -> list[dict]:
    return [ln for ln in (t.get("vat_lines") or []) if isinstance(ln, dict)]


def fingerprint(tx: dict) -> dict:
    """Fälten Mikko kan ändra i Bezala."""
    t = guard._unwrap(tx)
    return {
        "description": (t.get("description") or "").strip(),
        "original_amount": _num(t.get("original_amount")),
        "date": str(t.get("date") or ""),
        "lines": sorted(
            [[str(ln.get("id")), str(ln.get("expense_account_id")), str(ln.get("vat_id")),
              sorted(str(c) for c in (ln.get("cost_center_ids") or [])),
              _num(ln.get("taxable"))] for ln in _lines(t)], key=lambda x: x[0]),
    }


def record_seen(row: ProcessedMessage, tx: dict, *, after_write: bool = False) -> None:
    """Spara vad boten läste: första läsningen efter skapandet, och efter
    botens egna skrivningar. Mikkos ändringar däremellan sparas aldrig här."""
    t = guard._unwrap(tx)
    tx_id = str(t.get("id") or row.bezala_transaction_id)
    seen = dict(row.bezala_draft_seen or {})
    if str(seen.get("tx")) != tx_id:
        seen = {"tx": tx_id, "created_at": t.get("created_at"),
                "base_updated_at": t.get("updated_at"),
                "first_read_at": datetime.utcnow().isoformat()}
    elif not after_write:
        return
    seen["last_updated_at"] = t.get("updated_at")
    seen["fingerprint"] = fingerprint(t)
    seen["last_by"] = "bot_write" if after_write else "bot_read"
    row.bezala_draft_seen = seen


def is_untouched(tx: dict, seen: dict | None) -> tuple[bool, str]:
    """Orört = updated_at högst en minut efter skapandet OCH inga fält ändrade
    av någon annan än boten (jämfört med botens egen läsning)."""
    t = guard._unwrap(tx)
    if not seen or str(seen.get("tx")) != str(t.get("id")):
        return False, "boten har ingen egen läsning av utkastet - räknas som rört"
    created = _ts(t.get("created_at") or seen.get("created_at"))
    base = _ts(seen.get("base_updated_at"))
    if created is None or base is None:
        return False, "created_at/updated_at saknas i Bezala - räknas som rört"
    if (base - created).total_seconds() > UNTOUCHED_SECONDS:
        return False, (f"ändrat {int((base - created).total_seconds())} s efter skapandet "
                       "(mer än en minut)")
    if fingerprint(t) != seen.get("fingerprint"):
        return False, "fält ändrade av någon annan än boten"
    now, last = _ts(t.get("updated_at")), _ts(seen.get("last_updated_at"))
    if now is not None and last is not None and now != last:
        return False, "ändrat efter botens senaste läsning"
    return True, "orört (bara Bezala och boten har skrivit)"


# ================================================================ plan


def _is_plain_zero(ln: dict, ref: dict, defaults: set) -> bool:
    """Tom 0-rad: taxable 0 och inget eget konto/kostnadsställe som skiljer sig."""
    if _num(ln.get("taxable")) not in (0.0, None) or ln.get("id") is None:
        return False
    acc = ln.get("expense_account_id")
    if acc not in ({None, ref.get("expense_account_id")} | defaults):
        return False
    cc = sorted(str(c) for c in (ln.get("cost_center_ids") or []))
    ref_cc = sorted(str(c) for c in (ref.get("cost_center_ids") or []))
    from app.services.c60_draft import COST_CENTER_ID

    return cc in ([], ref_cc, [str(COST_CENTER_ID)])


def plan_auto_fill(db: Session, row: ProcessedMessage, line: CardLine | None, tx: dict, *,
                   accounts: list[dict] | None = None, conditional: dict | None = None,
                   seen: dict | None = None, untouched: bool | None = None,
                   link: bool = True) -> dict:
    """Vad automatiken skulle skriva (torrkörning). `untouched` kan tvingas
    (bara för torrkörningar som visar "om orört")."""
    from app.services import c60_draft as c60
    from app.services import c62_conditional as c62

    t = guard._unwrap(tx)
    snap = guard.status_snapshot(tx)
    tx_id = str(t.get("id") or row.bezala_transaction_id)
    base = {"transaction_id": tx_id, "message_id": row.message_id, "vendor": row.vendor,
            "bill_line_id": line.bill_line_id if line is not None else None,
            "state": guard.draft_state(snap), "mode": "c64"}
    if not guard.is_draft(snap):
        return {**base, "changes": {}, "skipped": [
            f"Inte ett utkast (state={guard.draft_state(snap)!r}) - boten skriver inte"],
            "before": {}, "after": {}}
    if untouched is None:
        untouched, why = is_untouched(tx, seen)
    else:
        why = "antaget i torrkörningen"
    accounts = c60._accounts() if accounts is None else accounts
    rule = draft_rule(db, row, line)
    changes, skipped = c60.plan_amount(db, row, line, t)
    negative = line is not None and line.amount is not None and float(line.amount) < 0

    # Del 3 — tecknet följer kortraden.
    cur_amt = _num(t.get("original_amount"))
    if negative and "original_amount" not in changes and cur_amt is not None and cur_amt > 0 \
            and abs(cur_amt - abs(float(line.amount))) < 0.011:
        if untouched:
            changes["original_amount"] = f"{float(line.amount):.2f}"
            changes["original_amount_currency"] = (t.get("original_amount_currency")
                                                   or line.currency or "").upper()
        else:
            skipped.append(f"Belopp: +{cur_amt:.2f} på en negativ kortrad, men utkastet är "
                           "rört - ändras inte")

    # Beskrivning: tomt fylls alltid.
    cur_desc = (t.get("description") or "").strip()
    desc = rule.get("description")
    if not cur_desc and desc:
        changes["description"] = desc
    elif not cur_desc:
        skipped.append("Beskrivning: inget säkert förslag - fyll i själv")
    elif desc and cur_desc != desc:
        skipped.append("Beskrivning: texten i Bezala behålls (redan ifylld)")

    # Kostnadsrader.
    defaults = c60._default_account_ids(accounts) | set(PLACEHOLDER_ACCOUNT_IDS)
    lines = _lines(t)
    main, zero = lines, []
    if len(lines) > 1:
        nonzero = [ln for ln in lines if _num(ln.get("taxable")) not in (0.0, None)]
        ref = nonzero[0] if len(nonzero) == 1 else lines[0]
        rest = [ln for ln in lines if ln is not ref]
        if untouched and len(nonzero) <= 1 and all(_is_plain_zero(ln, ref, defaults)
                                                    for ln in rest):
            main, zero = [ref], rest
        else:
            main = []
            skipped.append("Kostnadsrader: flera rader i Bezala - rörs inte"
                           + ("" if untouched else f" (utkastet är rört: {why})"))
    entries: list[dict] = []
    if len(main) == 1 or not lines:
        ln = main[0] if main else {}
        new: dict = {}
        acc_cur = ln.get("expense_account_id")
        if acc_cur in ({None} | defaults):
            if rule.get("account_id"):
                new["expense_account_id"] = rule["account_id"]
            else:
                skipped.append("Konto: inget säkert förslag - fyll i själv")
        elif rule.get("account_id") and str(acc_cur) != str(rule["account_id"]):
            skipped.append(f"Konto: {acc_cur} i Bezala behålls")
        vl = rule.get("vat_line")
        vat_cur = ln.get("vat_id")
        if vl is None:
            skipped.append(f"Moms: '{rule.get('vat')}' har inget känt vat_id - välj den själv"
                           if rule.get("vat") else "Moms: okänd - välj den själv i Bezala")
        elif not vat_cur:
            new.update(vl)
        elif str(vat_cur) == str(vl["vat_id"]):
            pass
        elif untouched and _int(vat_cur) in BEZALA_DEFAULT_VAT_IDS:
            new.update(vl)
        else:
            skipped.append(f"Moms: vat_id {vat_cur} i Bezala behålls"
                           + ("" if untouched else " (utkastet är rört)"))
        if not ln.get("cost_center_ids"):
            new["cost_center_ids"] = [c60.COST_CENTER_ID]
        tax = _num(ln.get("taxable"))
        if negative and tax is not None and tax > 0:
            if untouched:
                new["taxable"] = f"{-abs(tax):.2f}"
            else:
                skipped.append("Kostnadsrad: positivt belopp på negativ kortrad, men "
                               "utkastet är rört - ändras inte")
        if new:
            entries.append({"id": ln["id"], **new} if ln.get("id") is not None else new)
    for z in zero:
        entries.append({"id": z["id"], "_destroy": True})
    if entries:
        changes["vat_lines_attributes"] = entries

    # Vehicle, Sträcka (km), Antal passagerare.
    cv = c62.plan(db, row, tx, conditional, link=link)
    if cv["rows"]:
        changes[c60.CV_KEY] = cv["rows"]
    skipped.extend(cv["skipped"])

    before = {k: t.get(k) for k in c60.FILL_LOG_KEYS}
    before["conditional"] = cv["before"]
    after = dict(before)
    if cv["fields"]:
        after["conditional"] = {**(cv["before"] or {}), **cv["fields"]}
    for k, v in changes.items():
        if k == c60.CV_KEY:
            continue
        if k == "vat_lines_attributes":
            after["vat_lines"] = _apply_entries(lines, v)
        else:
            after[k] = v
    return {**base, "changes": changes, "skipped": skipped, "before": before, "after": after,
            "untouched": bool(untouched), "untouched_reason": why,
            "account": rule.get("account"), "vat": rule.get("vat"),
            "vat_rule": rule.get("vat_rule"), "flags": rule.get("flags") or [],
            "distance_fill": cv}


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _apply_entries(lines: list[dict], entries: list[dict]) -> list[dict]:
    out = [dict(ln) for ln in lines] or [{}]
    for e in entries:
        idx = next((i for i, ln in enumerate(out) if e.get("id") is not None
                    and str(ln.get("id")) == str(e["id"])), 0 if "id" not in e else None)
        if idx is None:
            continue
        if e.get("_destroy"):
            out[idx] = None
        else:
            out[idx] = {**out[idx], **{k: v for k, v in e.items() if k != "id"}}
    return [ln for ln in out if ln is not None]


# ================================================================ Del 1: efter verifierad koppling


def auto_fill_after_verified(db: Session, row: ProcessedMessage, line: CardLine | None,
                             client, tx_id: str) -> dict | None:
    """Kör ifyllnaden en gång på ett verifierat utkast som boten skapat.
    Misslyckas den eller kan den inte bekräftas: lämna utkastet, markera
    fill_unverified och logga. Inget nytt försök utan ny läsning."""
    from app.services import c60_draft as c60
    from app.services import c62_conditional as c62

    if not auto_fill_on(db):
        return None
    if row.draft_fill_status in (STATUS_DONE, STATUS_UNVERIFIED):
        return {"result": f"Redan körd ({row.draft_fill_status}) - körs inte igen"}
    if row.matched_at is None or str(row.bezala_transaction_id) != str(tx_id):
        return _finish(db, row, STATUS_SKIPPED,
                       "Inte ett utkast som boten själv skapat/kopplat - hoppas över")
    try:
        tx = client.get_transaction(tx_id)
    except Exception as exc:  # noqa: BLE001
        return _finish(db, row, STATUS_UNVERIFIED, f"Utkastet kunde inte läsas: {exc}"[:500])
    if not guard.is_draft(guard.status_snapshot(tx)):
        return _finish(db, row, STATUS_SKIPPED,
                       f"Inte ett utkast (state={guard.draft_state(guard.status_snapshot(tx))!r})"
                       " - boten skriver inte")
    record_seen(row, tx)
    plan = plan_auto_fill(db, row, line, tx, conditional=c62.read_values(client, tx_id),
                          seen=row.bezala_draft_seen)
    if not plan["changes"]:
        out = _finish(db, row, STATUS_DONE, "Inget att fylla i")
        out["plan"] = plan
        return out
    res = c60.apply_fill(client, tx_id, plan, db=db)
    failed = not res.get("written") or res.get("not_confirmed")
    if failed:
        reason = res.get("result") or "okänt fel"
        logger.warning("C64: ifyllnad av %s inte bekräftad: %s", tx_id, reason)
        out = _finish(db, row, STATUS_UNVERIFIED, f"Ifyllnad inte bekräftad: {reason}"[:1000])
    else:
        try:
            record_seen(row, client.get_transaction(tx_id), after_write=True)
        except Exception:  # noqa: BLE001
            logger.exception("C64: kunde inte läsa %s efter skrivningen", tx_id)
        out = _finish(db, row, STATUS_DONE, "Ifyllt och bekräftat i Bezala")
    out["fill"] = res
    return out


def _finish(db: Session, row: ProcessedMessage, status: str, note: str) -> dict:
    row.draft_fill_status = status
    row.draft_fill_note = note
    db.commit()
    logger.info("C64: utkast %s → %s (%s)", row.bezala_transaction_id, status, note)
    return {"status": status, "note": note}


# ================================================================ Del 6: torrkörning


REFERENCE_DRAFTS = ("5596172", "5596173", "5596175", "5596176", "5596178", "5597345")


def preview(db: Session, transaction_ids: list[str] | None = None, client=None) -> dict:
    """Torrkörning mot Bezala (bara GET): alla nuvarande utkast + referenserna.
    Skriver INGET - varken till Bezala eller botens egen databas."""
    from app.services import c60_draft as c60
    from app.services import c62_conditional as c62

    own = client is None
    if own:
        from app.services.bezala_client import BezalaClient

        client = BezalaClient()
    rows_out = []
    try:
        ids = [str(x) for x in transaction_ids] if transaction_ids else list(dict.fromkeys(
            c60._bezala_draft_ids(client) + list(REFERENCE_DRAFTS)))
        accounts = c60._accounts()
        for tx_id in ids:
            item: dict = {"transaction_id": tx_id}
            row = c60._row_for_tx(db, tx_id)
            try:
                tx = client.get_transaction(tx_id)
            except Exception as exc:  # noqa: BLE001
                item["error"] = f"GET misslyckades: {str(exc)[:200]}"
                rows_out.append(item)
                continue
            t = guard._unwrap(tx)
            item["now"] = _summary(t)
            if row is None:
                item["error"] = "Inget kvitto i boten är kopplat till utkastet"
                rows_out.append(item)
                continue
            line = c60._line_for(db, tx_id, row)
            cond = c62.read_values(client, tx_id)
            plan = plan_auto_fill(db, row, line, tx, accounts=accounts, conditional=cond,
                                  seen=row.bezala_draft_seen, link=False)
            item.update({"vendor": row.vendor, "untouched": plan.get("untouched"),
                         "untouched_reason": plan.get("untouched_reason"),
                         "would_write": plan["changes"], "skipped": plan["skipped"],
                         "rule": {"account": plan.get("account"), "vat": plan.get("vat"),
                                  "vat_rule": plan.get("vat_rule")},
                         "after": _summary(plan.get("after") or {}),
                         "draft_fill_status": row.draft_fill_status})
            if plan.get("untouched") is False and guard.is_draft(guard.status_snapshot(tx)):
                alt = plan_auto_fill(db, row, line, tx, accounts=accounts, conditional=cond,
                                     untouched=True, link=False)
                item["if_untouched"] = alt["changes"]
            rows_out.append(item)
        db.rollback()  # aldrig något sparat av torrkörningen
    finally:
        if own:
            client.close()
    return {"dry_run": True, "auto_fill_on": auto_fill_on(db),
            "note": "Torrkörning - inget skrivet till Bezala eller boten.",
            "count": len(rows_out), "items": rows_out}


def _summary(t: dict) -> dict:
    return {
        "state": t.get("state"),
        "description": t.get("description"),
        "amount": t.get("original_amount"),
        "currency": t.get("original_amount_currency"),
        "lines": [{"id": ln.get("id"), "account": ln.get("expense_account_id"),
                   "vat_id": ln.get("vat_id"), "taxable": ln.get("taxable"),
                   "cost_center_ids": ln.get("cost_center_ids")} for ln in _lines(t)],
        "created_at": t.get("created_at"), "updated_at": t.get("updated_at"),
    }
