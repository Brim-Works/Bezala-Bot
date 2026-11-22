"""C47 Del 6 — rimlighetskontroll av kvittodatum.

Orsak till t.ex. "Emmanuel Taxi 2018-11-26" / "Liv Taxi 2025-10-01": AI:n
läser taxikvitton där året är tvåsiffrigt ("26.11.18" = klockslag/kvittonr
blandas ihop med året) eller saknas helt, och gissar då ett år. Det fanns
ingen kontroll mot när mailet faktiskt kom in.

Regel: ett kvittodatum mer än 13 månader FÖRE mottagningsdatumet, eller
EFTER det (mer än en dag, tidszoner), är orimligt. Då används
mottagningsdatumets år (eller året innan om datumet fortfarande hamnar
efter mottagningen) och raden flaggas för granskning
(review_flag='date_corrected').
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

FLAG_DATE_CORRECTED = "date_corrected"
MAX_MONTHS_BEFORE = 13
MAX_DAYS_AFTER = 1
# Bokningar (flyg/tåg/hotell/hyrbil) dateras ofta på rese-/vistelsedagen,
# som legitimt ligger EFTER mailet. För dem gäller bara "för gammalt".
FUTURE_OK_CATEGORIES = {"flyg", "tåg", "hotell", "bilhyra"}


def _parse(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _months_before(d: date, received: date) -> int:
    return (received.year - d.year) * 12 + (received.month - d.month)


def _with_year(d: date, year: int) -> date:
    try:
        return d.replace(year=year)
    except ValueError:  # 29 feb
        return d.replace(year=year, day=28)


def is_implausible(receipt_date, received_at, category: str | None = None) -> bool:
    d, r = _parse(receipt_date), _parse(received_at)
    if d is None or r is None:
        return False
    if d > r + timedelta(days=MAX_DAYS_AFTER):
        future_ok = (category or "").strip().lower() in FUTURE_OK_CATEGORIES
        # Även bokningar: mer än ett år fram = fel år.
        return not future_ok or d > r + timedelta(days=366)
    months = _months_before(d, r)
    return months > MAX_MONTHS_BEFORE or (
        months == MAX_MONTHS_BEFORE and d.day < r.day
    )


def sanitize_receipt_date(receipt_date, received_at,
                          category: str | None = None) -> tuple[str | None, bool]:
    """(datum YYYY-MM-DD, korrigerat?) — oförändrat om datumet är rimligt
    eller om något av datumen saknas."""
    d, r = _parse(receipt_date), _parse(received_at)
    if d is None:
        return (str(receipt_date) if receipt_date else None), False
    if r is None or not is_implausible(d, r, category):
        return d.isoformat(), False
    fixed = _with_year(d, r.year)
    if fixed > r + timedelta(days=MAX_DAYS_AFTER):
        fixed = _with_year(d, r.year - 1)
    return fixed.isoformat(), True


def fix_existing_rows(db) -> dict:
    """Rätta befintliga rader (körs en gång vid start). Committar."""
    from app.models import ProcessedMessage

    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.receipt_date.isnot(None))
        .filter(ProcessedMessage.received_at.isnot(None))
        .all()
    )
    fixed = []
    for m in rows:
        new, changed = sanitize_receipt_date(m.receipt_date, m.received_at, m.category)
        if changed:
            fixed.append({"id": m.id, "vendor": m.vendor, "old": m.receipt_date, "new": new})
            m.receipt_date = new
            m.review_flag = FLAG_DATE_CORRECTED
    db.commit()
    return {"fixed": len(fixed), "rows": fixed[:50]}
