"""FAS 8 — feedback-loop + AI-inlärning.

AI lär sig av användarens rättelser. Varje gång en användare rättar
ett AI-extraherat fält (eller klickar 👎) sparas det här. Inför nästa
AI-analys hämtas de senaste rättelserna och bifogas Claude-prompten
som few-shot-exempel.

Alla DB-anrop är defensiva — vid fel returneras tomma listor / None.
Varken pipelinen eller endpointen ska krascha för att feedback inte
gick att spara/hämta.
"""

from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import desc

from app.models import AiFeedback, ProcessedMessage

logger = logging.getLogger(__name__)


# Fält där användarens rättelser räknas som lärande. Andra fält
# (currency, summary, file_name etc.) loggas inte automatiskt.
TRACKABLE_FIELDS: tuple[str, ...] = (
    "vendor",
    "amount",
    "receipt_date",  # backend-kolumnen heter receipt_date
    "date",          # frontend-alias — accepteras men normaliseras
    "category",
)


def _normalize_field_name(field_name: str) -> str:
    """'date' (frontend) → 'receipt_date' (DB-kolumnnamn)."""
    if field_name == "date":
        return "receipt_date"
    return field_name


def _vendor_context_for(db, message_id: str) -> str | None:
    """Plocka vendor från ProcessedMessage. Används som scope när vi
    senare hämtar few-shot-exempel."""
    if not message_id:
        return None
    try:
        row = (
            db.query(ProcessedMessage)
            .filter(ProcessedMessage.message_id == message_id)
            .first()
        )
    except Exception:  # noqa: BLE001
        logger.exception("vendor_context-slagning misslyckades")
        return None
    if row and row.vendor:
        return str(row.vendor)[:255]
    return None


def extract_vendor_for_context(sender: str | None) -> str | None:
    """Heuristisk vendor från avsändarens email-header.
    'Finnair <noreply@finnair.com>' → 'Finnair'.
    'noreply@arlandaexpress.se' → 'arlandaexpress'.
    Används bara som filter när ingen verklig vendor finns ännu (t.ex.
    inför första AI-anropet)."""
    if not sender:
        return None
    s = sender.strip()
    if "<" in s:
        name = s.split("<", 1)[0].strip().strip('"').strip()
        if name:
            return name[:255]
    if "@" in s:
        # 'noreply@arlandaexpress.se' → 'arlandaexpress'
        domain = s.split("@", 1)[-1]
        domain = domain.split(">", 1)[0].split(".")[0].strip()
        if domain:
            return domain[:255]
    return s[:255] if s else None


def save_correction(
    db,
    message_id: str,
    field_name: str,
    ai_value: str | None,
    correct_value: str | None,
) -> AiFeedback | None:
    """Spara en rättelse (när användaren ändrade ett AI-extraherat fält).
    Sväljer fel — kallaren ska aldrig krascha pga feedback."""
    try:
        if not message_id or not field_name:
            return None
        norm = _normalize_field_name(field_name)
        # Skippa om värdena är identiska (t.ex. samma string-konvertering)
        if ai_value is not None and correct_value is not None:
            if str(ai_value) == str(correct_value):
                return None
        vendor_context = _vendor_context_for(db, message_id)
        row = AiFeedback(
            message_id=message_id,
            feedback_type="correction",
            field_name=norm[:50],
            ai_value=str(ai_value) if ai_value is not None else None,
            correct_value=(
                str(correct_value) if correct_value is not None else None
            ),
            vendor_context=vendor_context,
        )
        db.add(row)
        db.flush()
        return row
    except Exception:  # noqa: BLE001
        logger.exception(
            "save_correction misslyckades message_id=%s field=%s",
            message_id, field_name,
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


def save_thumbs(
    db,
    message_id: str,
    is_positive: bool,
    fields: Iterable[str] | None = None,
) -> list[AiFeedback]:
    """Spara explicit 👍/👎.
    - thumbs_up: en rad utan field_name.
    - thumbs_down med fält: en rad per fält med field_name satt.
    - thumbs_down utan fält: en rad utan field_name."""
    try:
        if not message_id:
            return []
        vendor_context = _vendor_context_for(db, message_id)
        ftype = "thumbs_up" if is_positive else "thumbs_down"
        cleaned = [
            _normalize_field_name(f.strip()) for f in (fields or [])
            if f and f.strip()
        ]
        out: list[AiFeedback] = []
        if is_positive or not cleaned:
            row = AiFeedback(
                message_id=message_id,
                feedback_type=ftype,
                field_name=None,
                vendor_context=vendor_context,
            )
            db.add(row)
            out.append(row)
        else:
            for fld in cleaned:
                row = AiFeedback(
                    message_id=message_id,
                    feedback_type=ftype,
                    field_name=fld[:50],
                    vendor_context=vendor_context,
                )
                db.add(row)
                out.append(row)
        db.flush()
        return out
    except Exception:  # noqa: BLE001
        logger.exception(
            "save_thumbs misslyckades message_id=%s positive=%s",
            message_id, is_positive,
        )
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []


def get_few_shot_examples(
    db,
    vendor: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Hämta few-shot-exempel:
    - Upp till `limit/2` senaste 'correction' för samma vendor (om
      vendor ges)
    - Resten (upp till `limit`) från andra leverantörer

    Om vendor saknas → upp till `limit` senaste totalt.
    Returnerar tom lista vid DB-fel — analys ska aldrig blockeras."""
    try:
        if limit <= 0:
            return []
        per_bucket = max(1, limit // 2)
        results: list[dict] = []
        seen_ids: set[int] = set()

        if vendor:
            rows = (
                db.query(AiFeedback)
                .filter(AiFeedback.feedback_type == "correction")
                .filter(AiFeedback.vendor_context == vendor[:255])
                .order_by(desc(AiFeedback.created_at))
                .limit(per_bucket)
                .all()
            )
            for r in rows:
                seen_ids.add(r.id)
                results.append(_row_to_dict(r))

        # Övriga (annan vendor — eller ALLA om vendor saknas)
        remaining = max(0, limit - len(results))
        if remaining > 0:
            q_other = (
                db.query(AiFeedback)
                .filter(AiFeedback.feedback_type == "correction")
            )
            if vendor:
                q_other = q_other.filter(
                    (AiFeedback.vendor_context != vendor[:255])
                    | (AiFeedback.vendor_context.is_(None))
                )
            rows = (
                q_other.order_by(desc(AiFeedback.created_at))
                .limit(remaining + len(seen_ids))  # överextra ifall några är dubletter
                .all()
            )
            for r in rows:
                if r.id in seen_ids:
                    continue
                results.append(_row_to_dict(r))
                if len(results) >= limit:
                    break
        return results
    except Exception:  # noqa: BLE001
        logger.exception("get_few_shot_examples misslyckades vendor=%r", vendor)
        return []


def get_examples_for_sender(
    db,
    sender: str | None,
    limit: int = 10,
) -> list[dict]:
    """Bekvämlighets-helper: härled vendor från sender-headern och hämta
    few-shot-exempel. Säkert att kalla även när feedback-tabellen är tom
    eller saknas — returnerar tom lista vid fel."""
    vendor = extract_vendor_for_context(sender)
    return get_few_shot_examples(db, vendor=vendor, limit=limit)


def _row_to_dict(row: AiFeedback) -> dict:
    return {
        "field_name": row.field_name,
        "ai_value": row.ai_value,
        "correct_value": row.correct_value,
        "vendor_context": row.vendor_context,
    }


def format_examples_for_prompt(examples: list[dict]) -> str:
    """Formatera examples till ett textblock som kan klistras in efter
    SYSTEM_PROMPT i Claude-anropet. Returnerar tom sträng om listan är
    tom — analyzern ska då skicka original-prompten oförändrad."""
    if not examples:
        return ""
    lines = [
        "",
        "## Tidigare rättelser från användaren",
        "",
        ("När användaren tidigare har rättat AI-extraktioner, har de"
         " gjort följande korrigeringar. Dra lärdom av dessa exempel"
         " när du analyserar dokumentet."),
        "",
    ]
    for ex in examples:
        vendor = ex.get("vendor_context") or "okänd leverantör"
        field = ex.get("field_name") or "okänt fält"
        ai_v = ex.get("ai_value") if ex.get("ai_value") is not None else ""
        correct = (
            ex.get("correct_value") if ex.get("correct_value") is not None else ""
        )
        lines.append(f"- För kvitto från {vendor}:")
        lines.append(f'  - AI extraherade: {field}="{ai_v}"')
        lines.append(f'  - Användaren rättade till: "{correct}"')
    return "\n".join(lines)


def feedback_stats(db) -> dict:
    """Aggregat för en framtida statistikflik. Säkert att kalla även
    när tabellen är tom."""
    try:
        from sqlalchemy import func as sql_func
        from datetime import datetime, timedelta

        total = (
            db.query(sql_func.count(AiFeedback.id)).scalar() or 0
        )
        cutoff = datetime.utcnow() - timedelta(days=30)
        last_30 = (
            db.query(sql_func.count(AiFeedback.id))
            .filter(AiFeedback.created_at >= cutoff)
            .scalar() or 0
        )
        by_field: dict[str, int] = {}
        rows = (
            db.query(AiFeedback.field_name, sql_func.count(AiFeedback.id))
            .filter(AiFeedback.feedback_type == "correction")
            .group_by(AiFeedback.field_name)
            .all()
        )
        for name, count in rows:
            key = name or "_other"
            by_field[key] = int(count)
        return {
            "total": int(total),
            "last_30_days": int(last_30),
            "by_field": by_field,
        }
    except Exception:  # noqa: BLE001
        logger.exception("feedback_stats misslyckades")
        return {"total": 0, "last_30_days": 0, "by_field": {}}
