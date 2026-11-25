"""C44 — kopplingsförslag för kortrader utan kvitto + läsverktyg.

* `suggest_receipts`: för varje Bezala-kortrad utan kvitto, bästa
  kandidatkvittot i boten enligt befintlig AI MATCH-scoring
  (receipt_matcher.find_matches, inkl. C43:s valutatolerans och
  plats-signal). Ett kvitto föreslås för högst en kortrad. Dubbletter
  (duplicate_of) och redan kopplade kvitton föreslås aldrig.
* `couple_receipt`: kör befintligt match-to-bezala-flöde (Bezala-utkast,
  `unapproved`) — BARA när Mikko uttryckligen ber om det.
* `get_card_line`: kortraden, kopplat kvitto, poäng och när/hur.
* `search_receipts`: läsverktyg över processed_messages (ersätter SSH).

Inget skickas för attest. Inga skrivningar till Netvisor.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import CardLine, ProcessedMessage

logger = logging.getLogger(__name__)


from app.services.calendar_trip_edit import TripEditError  # noqa: E402


class CouplingError(TripEditError):
    """Fel som visas för Mikko (engelska)."""


def _missing_from_card_line(db: Session, c: CardLine) -> dict:
    from app.services import bill_line_status as bls

    missing = bls.normalize_missing_receipt({
        "id": c.bill_line_id,
        "description": c.description or "",
        "amount": c.amount,
        "currency": c.currency,
        "date": c.txn_date.isoformat() if c.txn_date else None,
    })
    trip = bls.trip_context(db, missing.get("date"))
    if trip:
        missing["trip"] = trip
    return missing


def _serialize(m: ProcessedMessage) -> dict:
    from app.main import _serialize_message

    return _serialize_message(m)


def _candidates(db: Session) -> list[dict]:
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.status == "saved")
        .filter(ProcessedMessage.bezala_transaction_id.is_(None))
        .filter(ProcessedMessage.duplicate_of.is_(None))
        .all()
    )
    from app.services.c53_receipts import filter_candidates

    # C53 Del 0b — e-ticket före bokningsbekräftelse vid samma booking_ref.
    return filter_candidates(db, [_serialize(m) for m in rows
                                  if m.bezala_upload_status != "success"])


def _brief(entry: dict) -> dict:
    m = entry["message"]
    return {
        "message_id": m.get("message_id"),
        "id": m.get("id"),
        "vendor": m.get("vendor"),
        "amount": m.get("amount"),
        "currency": m.get("currency"),
        "date": m.get("receipt_date"),
        "kind": m.get("kind"),
        "score": entry["score"],
        **({"conversion": entry["conversion"]} if entry.get("conversion") else {}),
    }


def suggest_receipts(db: Session, card_lines: list[CardLine],
                     exclude_pairs: set[tuple[str, str]] | None = None) -> dict[str, dict | None]:
    """{bill_line_id: suggested_receipt | None}. Bästa par först (girigt), så
    att ett kvitto bara föreslås för en kortrad. C47: par som Mikko sagt nej
    till i svep-kön föreslås aldrig igen."""
    if exclude_pairs is None:
        try:
            from app.services.decision_queue import rejected_receipt_pairs

            exclude_pairs = rejected_receipt_pairs(db)
        except Exception:  # noqa: BLE001
            exclude_pairs = set()
    from app.services.currency_converter import make_db_rate_provider
    from app.services.receipt_matcher import find_matches

    cands = _candidates(db)
    rate = make_db_rate_provider(db)
    pairs: list[tuple[int, str, dict]] = []
    for c in card_lines:
        missing = _missing_from_card_line(db, c)
        for e in find_matches(missing, cands, rate_provider=rate):
            pairs.append((e["score"], c.bill_line_id, e))
    pairs.sort(key=lambda p: (-p[0], p[2]["score_breakdown"].get("date_days_off") or 99))
    out: dict[str, dict | None] = {c.bill_line_id: None for c in card_lines}
    used: set[str] = set()
    for _, bid, e in pairs:
        mid = e["message"].get("message_id")
        if (bid, mid) in exclude_pairs:
            continue
        if out.get(bid) is not None or mid in used:
            continue
        out[bid] = _brief(e)
        used.add(mid)
    # C50 Del 2 — Apple-kortrader (APPLE.COM/BILL) ↔ Apple-kvitton på
    # belopp + datum ±3 dagar; kvittots appnamn är leverantören.
    try:
        from app.services.c50_handled import apple_suggestions

        open_lines = [c for c in card_lines if out.get(c.bill_line_id) is None]
        for bid, m in apple_suggestions(db, open_lines, used, exclude_pairs).items():
            out[bid] = {"message_id": m.message_id, "id": m.id, "vendor": m.vendor,
                        "amount": m.amount, "currency": m.currency, "date": m.receipt_date,
                        "kind": m.kind, "score": 90, "match": "apple_card_line"}
    except Exception:  # noqa: BLE001
        logger.exception("C50: Apple-förslag misslyckades")
    # C53 Del 0b — köprad vars kvitto redan används för återbetalningen
    # (samma belopp, samma booking_ref): föreslå originalet, märkt.
    try:
        from app.services.c53_receipts import shared_refund_receipt

        for c in card_lines:
            cur = out.get(c.bill_line_id)
            # Exakt belopp på ett kvitto med samma booking_ref går före ett
            # svagt förslag med annat belopp (prod: 744 732,57 för 134,48).
            if cur is not None and cur.get("amount") is not None and c.amount is not None \
                    and abs(float(cur["amount"]) - float(c.amount)) < 0.01:
                continue
            s = shared_refund_receipt(db, c)
            if s and (c.bill_line_id, s["message_id"]) not in exclude_pairs:
                out[c.bill_line_id] = s
    except Exception:  # noqa: BLE001
        logger.exception("C53: återbetalningsförslag misslyckades")
    return out


# ---------- couple_receipt ----------


def _find_message(db: Session, message_id: str) -> ProcessedMessage:
    q = db.query(ProcessedMessage).filter(ProcessedMessage.deleted_at.is_(None))
    row = q.filter(ProcessedMessage.message_id == str(message_id)).first()
    if row is None and str(message_id).isdigit():
        row = q.filter(ProcessedMessage.id == int(message_id)).first()
    if row is None:
        raise CouplingError(f"Receipt '{message_id}' not found in Bezala Bot.")
    return row


def couple_receipt(db: Session, bill_line_id: str, message_id: str) -> dict:
    """Koppla kvittot till kortraden via befintligt match-to-bezala-flöde
    (skapar/uppdaterar ett Bezala-UTKAST, aldrig attest)."""
    bid = str(bill_line_id).strip()
    if not bid.isdigit():
        raise CouplingError("bill_line_id must be the numeric Bezala card line id.")
    row = _find_message(db, message_id)
    if row.duplicate_of:
        raise CouplingError(
            f"Receipt {row.message_id} is a duplicate of {row.duplicate_of} — couple that one."
        )
    if row.matched_at is not None and row.bezala_transaction_id \
            and str(row.bezala_transaction_id) != bid:
        # C53 Del 0b — kvittoraden bär bara en Bezala-koppling. Ladda inte upp
        # igen (skulle skriva över kopplingen till t.ex. återbetalningen).
        raise CouplingError(
            f"Receipt {row.message_id} is already coupled to Bezala "
            f"{row.bezala_transaction_id}. Bezala Bot does not upload it again - download "
            f"the PDF from {row.drive_link or 'Drive'} and add it to card line {bid} in "
            "Bezala yourself."
        )
    if not row.drive_file_id or not row.file_name:
        raise CouplingError("The receipt has no Drive file — cannot couple it to Bezala.")
    # C60 — kopplingar körs en i taget (prod 2026-11-25: 19 parallella gav 8
    # utkast som Bezala inte kopplade), med kort paus och max 2 försök vid
    # Bezala-fel som inte skapade något utkast.
    with _COUPLE_LOCK:
        _couple_with_retry(db, row, bid)
        time.sleep(_pause_seconds())
    line = db.query(CardLine).filter(CardLine.bill_line_id == bid).first()
    from app.services import c60_draft

    # C60 — kortraden är INTE löst förrän utkastet lästs om och kopplingen
    # verifierats (connected_to_bill_line, bill_id, belopp, datum).
    c60_draft.mark_unverified(
        db, line, row,
        f"Utkast {row.bezala_transaction_id} skapat - koppling inte verifierad än")
    if row.waiting_status in ("waiting_for_transaction", "no_transaction"):
        row.waiting_status = "coupled"
        db.commit()
    c60_draft.schedule_verification(row.bezala_transaction_id, bill_line_id=bid)
    logger.info("[c44-couple] bill_line=%s ← message=%s (Bezala draft, verifieras om ca 75 s)",
                bid, row.message_id)
    fill_on = c60_draft.draft_fill_enabled()
    return {
        "bill_line_id": bid,
        "message_id": row.message_id,
        "vendor": row.vendor,
        "amount": row.amount,
        "currency": row.currency,
        "bezala_transaction_id": row.bezala_transaction_id,
        "bezala_state": "draft — not submitted for approval",
        "status": "utkast skapat, koppling inte verifierad än",
        "coupling_status": c60_draft.STATUS_UNVERIFIED,
        "verification": ("Boten läser utkastet igen om ca 75 s. Kortraden blir löst först "
                         "när Bezala visar kopplingen, beloppet och datumet."),
        # C60 — beskrivning/konto/moms skrivs till utkastet vid verifieringen
        # bara när BEZALA_DRAFT_FILL_ENABLED=true (och bara tomma fält).
        "bezala_description_written": False,
        "draft_fill_enabled": fill_on,
        "draft_fill": ("Fylls i vid verifieringen (bara tomma fält, bara utkast)" if fill_on
                       else "Av (BEZALA_DRAFT_FILL_ENABLED=false) - klistra in själv"),
        "description_to_paste": _description_to_paste(row, line),
        "account_suggestion": _account_suggestion(row, line),
        "bezala_fill": _bezala_fill(row, line),
    }


_COUPLE_LOCK = threading.Lock()


def _pause_seconds() -> float:
    import os

    try:
        return float(os.environ.get("C60_COUPLE_PAUSE_SECONDS", "") or 2.0)
    except ValueError:
        return 2.0


def _retryable(exc) -> bool:
    code = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if "kill switch" in text or "not a draft" in text or "left draft" in text:
        return False
    return code is None or code == 429 or code >= 500


def _couple_with_retry(db: Session, row: ProcessedMessage, bid: str, attempts: int = 2) -> None:
    from fastapi import HTTPException

    from app.main import _do_match_to_bezala
    from app.services.bezala_client import BezalaClient, BezalaError

    last: Exception | None = None
    for n in range(1, attempts + 1):
        try:
            bezala = BezalaClient()
        except BezalaError as exc:
            raise CouplingError(f"Bezala unavailable: {exc}") from exc
        tx_before = row.bezala_transaction_id
        try:
            _do_match_to_bezala(row=row, bill_line_id=bid, db=db, bezala=bezala)
            return
        except HTTPException as exc:
            db.rollback()
            detail = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
            raise CouplingError(f"Not coupled: {detail}") from exc
        except BezalaError as exc:
            db.rollback()
            last = exc
            created = row.bezala_transaction_id and row.bezala_transaction_id != tx_before
            if n < attempts and not created and _retryable(exc):
                logger.warning("C60: Bezala-fel vid koppling %s (försök %d) - provar igen: %s",
                               bid, n, exc)
                time.sleep(_pause_seconds())
                continue
            row.bezala_upload_status = "failed"
            row.bezala_error_message = str(exc)[:2000]
            db.commit()
            raise CouplingError(f"Bezala refused the coupling: {exc}") from exc
        finally:
            bezala.close()
    raise CouplingError(f"Bezala refused the coupling: {last}")


def _description_to_paste(row: ProcessedMessage, line: CardLine | None) -> str | None:
    """C60 — flyg: flygens datum (aldrig köp-/kortradens datum)."""
    from sqlalchemy.orm import object_session

    from app.services.c60_draft import description_for

    d = (line.txn_date if line is not None and line.txn_date else None) or row.receipt_date
    return description_for(object_session(row), row, d)


def _account_suggestion(row: ProcessedMessage, line: CardLine | None) -> dict:
    from app.services.c50_accounts import suggest_account

    return suggest_account(row.vendor, row.sender, row.subject, row.ai_description_en,
                           category=row.category,
                           card_description=(line.description if line is not None
                                             else row.bezala_payment_merchant))


def _bezala_fill(row: ProcessedMessage, line: CardLine | None,
                 account: dict | None = None) -> dict:
    """C51 — konto, moms, kostnadsställe och beskrivning att klistra in i
    Bezala (skrivs inte via API)."""
    from app.services.c51_vat import bezala_fill

    return bezala_fill(
        account=account if account is not None else _account_suggestion(row, line),
        category=row.category,
        texts=[row.vendor, row.sender, row.subject, row.ai_description_en, row.file_name],
        card_description=line.description if line is not None else row.bezala_payment_merchant,
        card_amount=(line.amount if line is not None else row.bezala_payment_amount),
        receipt_date=(line.txn_date if line is not None and line.txn_date else row.receipt_date),
        vat_info=row.vat_info, flight_segments=_segments(row),
        description=_description_to_paste(row, line),
    )


def _segments(row: ProcessedMessage) -> list:
    """C60 — egna flygsegment eller samma bokningsnummers (ändringsavgift)."""
    from sqlalchemy.orm import object_session

    from app.services.c60_draft import segments_for

    return segments_for(object_session(row), row)


# ---------- get_card_line ----------


def _receipt_info(m: ProcessedMessage) -> dict:
    return {
        "id": m.id,
        "message_id": m.message_id,
        "vendor": m.vendor,
        "amount": m.amount,
        "currency": m.currency,
        "receipt_date": m.receipt_date,
        "subject": m.subject,
        "sender": m.sender,
        "file_name": m.file_name,
        "drive_link": m.drive_link,
        "kind": m.kind,
        "bezala_transaction_id": m.bezala_transaction_id,
        "bezala_upload_status": m.bezala_upload_status,
        "matched_at": m.matched_at.isoformat() if m.matched_at else None,
        "upload_source": m.upload_source,
        "source_account": m.source_account or "visma",
        "waiting_status": m.waiting_status,
        "bill_line_snapshot": {
            "merchant": m.bezala_payment_merchant,
            "amount": m.bezala_payment_amount,
            "currency": m.bezala_payment_currency,
            "date": m.bezala_payment_date,
        } if m.bezala_payment_merchant or m.bezala_payment_amount else None,
        "duplicate_of": m.duplicate_of,
        "bezala_error_message": m.bezala_error_message,
        # C60 — vad boten skrivit till utkastet (före/efter).
        "bot_bezala_writes": _writes(m),
    }


def _writes(m: ProcessedMessage) -> list[dict]:
    from sqlalchemy.orm import object_session

    from app.services.c60_draft import writes_for

    s = object_session(m)
    return writes_for(s, m.bezala_transaction_id) if s is not None else []


def _how(m: ProcessedMessage) -> str:
    if m.upload_source == "manual_upload":
        return "manual upload in Travel Tinder (coupled on upload)"
    if m.bezala_upload_status == "coupling_unverified":
        return "utkast skapat i Bezala, koppling inte verifierad än"
    if m.matched_at is not None:
        return "coupled to the card line (Travel Tinder / match-to-bezala)"
    if m.bezala_transaction_id:
        return "uploaded to Bezala (auto-upload or legacy flow, no match timestamp)"
    return "not coupled"


def _snapshot_matches(m: ProcessedMessage, c: CardLine) -> bool:
    if c.txn_date is None or not m.bezala_payment_date:
        return False
    if str(m.bezala_payment_date)[:10] != c.txn_date.isoformat():
        return False
    if c.amount is not None and m.bezala_payment_amount is not None:
        return abs(float(c.amount) - float(m.bezala_payment_amount)) < 0.02
    return True


def get_card_line(db: Session, bill_line_id: str, scan_bezala: bool = True) -> dict:
    from app.services.trip_completeness import _today, card_line_dict

    bid = str(bill_line_id).strip()
    line = db.query(CardLine).filter(CardLine.bill_line_id == bid).first()
    out: dict = {"bill_line_id": bid}
    if line is not None:
        out["card_line"] = card_line_dict(line, _today())
        out["card_line"].update({
            "first_seen_at": line.first_seen_at.isoformat() if line.first_seen_at else None,
            "last_seen_at": line.last_seen_at.isoformat() if line.last_seen_at else None,
            "resolved_at": line.resolved_at.isoformat() if line.resolved_at else None,
        })
    else:
        out["card_line"] = None
        out["note"] = ("Card line not in Bezala Bot's mirror (it never showed up as "
                       "missing a receipt — probably coupled before C42).")

    coupled: list[tuple[ProcessedMessage, str]] = []
    q = db.query(ProcessedMessage).filter(ProcessedMessage.deleted_at.is_(None))
    for m in q.filter(ProcessedMessage.bezala_transaction_id == bid).all():
        coupled.append((m, "bezala_transaction_id equals the card line id (legacy coupling)"))
    if line is not None:
        for m in q.filter(ProcessedMessage.bezala_payment_date.isnot(None)).all():
            if _snapshot_matches(m, line) and all(m is not c for c, _ in coupled):
                coupled.append((m, "bill line snapshot (date + amount) matches"))
    if not coupled and scan_bezala:
        coupled += _scan_bezala_for_bill_line(db, bid, line)

    from app.services.currency_converter import make_db_rate_provider
    from app.services.receipt_matcher import score_match

    receipts = []
    for m, why in coupled:
        info = _receipt_info(m)
        info["found_by"] = why
        info["how"] = _how(m)
        if line is not None:
            s = score_match(_missing_from_card_line(db, line), _serialize(m),
                            rate_provider=make_db_rate_provider(db))
            info["score"] = s["total"]
            info["score_breakdown"] = s["breakdown"]
        receipts.append(info)
    out["coupled_receipts"] = receipts
    if scan_bezala:
        # C51 Del 1 — Bezala-transaktionen för kortraden och dess `state`
        # (endast GET). "unapproved" = utkast, "reviewing" = väntar på attest.
        try:
            from app.services.c51_bezala_inspect import inspect

            out["bezala_transaction"] = inspect(
                bill_line_id=bid,
                amount=line.amount if line is not None else None,
                day=line.txn_date.isoformat() if line is not None and line.txn_date else None,
            )
        except Exception as exc:  # noqa: BLE001
            out["bezala_transaction"] = {"error": str(exc)[:300]}
    if line is not None and line.status == "missing":
        sugg = suggest_receipts(db, [line]).get(bid)
        out["suggested_receipt"] = sugg
    return out


def _scan_bezala_for_bill_line(db: Session, bid: str, line: CardLine | None,
                               max_calls: int = 80) -> list[tuple[ProcessedMessage, str]]:
    """Läs Bezala-transaktionerna (GET, read-only) för kopplade kvitton och
    leta efter kortradens id i svaret."""
    import json

    from app.services.bezala_client import BezalaClient

    q = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.bezala_transaction_id.isnot(None))
    )
    rows = q.all()
    if line is not None and line.txn_date is not None:
        lo, hi = line.txn_date - timedelta(days=45), line.txn_date + timedelta(days=45)

        def near(m: ProcessedMessage) -> bool:
            from app.services.trip_completeness import _parse_day

            d = _parse_day(m.bezala_payment_date) or _parse_day(m.receipt_date)
            return d is not None and lo <= d <= hi
        rows = [m for m in rows if near(m)]
    rows.sort(key=lambda m: m.matched_at or m.processed_at, reverse=True)
    try:
        bezala = BezalaClient()
    except Exception as exc:  # noqa: BLE001
        logger.warning("get_card_line: Bezala init misslyckades: %s", exc)
        return []
    hits = []
    try:
        for m in rows[:max_calls]:
            try:
                tx = bezala.get_transaction(m.bezala_transaction_id)
            except Exception:  # noqa: BLE001
                continue
            if re.search(rf"(?<!\d){re.escape(bid)}(?!\d)", json.dumps(tx, default=str)):
                hits.append((m, "Bezala transaction references the card line"))
    finally:
        bezala.close()
    return hits


# ---------- search_receipts ----------


def search_receipts(db: Session, query: str | None = None, date_from: str | None = None,
                    date_to: str | None = None, limit: int = 30) -> dict:
    """Read-only sökning bland kvitton i boten (vendor, avsändare, ämne,
    filnamn, belopp). Datum = kvittodatum (YYYY-MM-DD)."""
    from app.services.trip_completeness import _parse_day

    limit = max(1, min(int(limit or 30), 100))
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .order_by(ProcessedMessage.id.desc())
        .all()
    )
    lo = date.fromisoformat(date_from) if date_from else None
    hi = date.fromisoformat(date_to) if date_to else None
    words = [w for w in re.findall(r"[\w.,]+", (query or "").lower()) if w]
    out = []
    for m in rows:
        d = _parse_day(m.receipt_date) or _parse_day(m.received_at)
        if lo and (d is None or d < lo):
            continue
        if hi and (d is None or d > hi):
            continue
        if words:
            hay = " ".join(str(v or "") for v in (
                m.vendor, m.sender, m.subject, m.file_name, m.amount, m.message_id,
                m.bezala_payment_merchant, m.booking_ref)).lower()
            if not all(w in hay for w in words):
                continue
        info = _receipt_info(m)
        info["status"] = m.status
        info["category"] = m.category
        info["parking"] = (
            {"entry": m.parking_entry_at, "exit": m.parking_exit_at}
            if m.parking_entry_at else None
        )
        info["flight_segments"] = m.flight_segments
        info["booking_ref"] = m.booking_ref
        info["details_status"] = m.details_status
        out.append(info)
        if len(out) >= limit:
            break
    return {"count": len(out), "receipts": out}
