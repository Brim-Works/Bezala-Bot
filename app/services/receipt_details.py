"""C44 — detaljer ur kvittomailens text (ren logik + läsning från Gmail).

* Moovy/Finavia-parkering: "Finavia P1 Premium 05.03.2026 05:51, 16h 49min
  10s" → infart 05:51 och utfart (infart + varaktighet), Helsingfors-tid.
* Finnair/Amadeus: flygsegment (flightnummer, datum, avgång/ankomst),
  bokningsnummer (PNR) och e-biljettnummer. Används för
  - flygkvitton i checklistan (måste matcha resans flyg, inte bara
    leverantör),
  - ombokningar ("Bekräftelse på din bokningsändring" ger de nya flygen),
  - dubblettkontroll (samma belopp + datum + e-biljett/bokning).

Mailtexten läses med Gmail-API:t (endast läsning) en gång per kvitto och
resultatet sparas på `processed_messages`. Inget skickas till Bezala.
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import ProcessedMessage

logger = logging.getLogger(__name__)

HELSINKI = ZoneInfo("Europe/Helsinki")

# Land → tidszon för flygtider (lokal tid på respektive flygplats).
_COUNTRY_TZ = {
    "FI": "Europe/Helsinki", "EE": "Europe/Tallinn", "LV": "Europe/Riga",
    "LT": "Europe/Vilnius", "GB": "Europe/London", "IE": "Europe/Dublin",
    "PT": "Europe/Lisbon", "IS": "Atlantic/Reykjavik", "TR": "Europe/Istanbul",
    "US": "America/New_York",
}
_DEFAULT_TZ = "Europe/Stockholm"  # CET/CEST: SE, DK, NO, DE, NL, FR …

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}

DETAILS_SINCE = date(2026, 1, 1)


# ---------- Text ----------


def html_to_text(raw: str | None) -> str:
    """HTML → en rad text där tabellceller skiljs med ' | '."""
    if not raw:
        return ""
    s = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", " ", raw)
    s = re.sub(r"(?i)</t[dh]\s*>", " | ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", " \n ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = _html.unescape(s)
    return re.sub(r"[ \t\r\f\v\xa0]+", " ", s)


def message_text(subject: str | None, body_text: str | None, body_html: str | None) -> str:
    parts = [subject or "", body_text or "", html_to_text(body_html)]
    return "\n".join(p for p in parts if p)


# ---------- Parkering (Moovy / Finavia) ----------

_PARKING_RE = re.compile(
    r"(?P<d>\d{1,2})\.(?P<m>\d{1,2})\.(?P<y>\d{4})\s+(?P<hh>\d{1,2})[:.](?P<mm>\d{2})"
    r"\s*,\s*"
    r"(?:(?P<days>\d+)\s*(?:dygn|vrk|vuorokau\w*|days?|d(?:ay)?)\b\.?\s*)?"
    r"(?:(?P<h>\d+)\s*h\b\s*)?"
    r"(?:(?P<min>\d+)\s*min\b\s*)?"
    r"(?:(?P<s>\d+)\s*s\b)?",
    re.IGNORECASE,
)


def parse_parking(text: str | None) -> tuple[datetime, datetime] | None:
    """(infart, utfart) i Helsingfors-tid, eller None.

    '05.03.2026 05:51, 16h 49min 10s' → 05:51 → 22:40:10.
    '07.05.2026 05:19, 1 dygn 15h 00min' → 05:19 → 8.5 20:19."""
    for m in _PARKING_RE.finditer(text or ""):
        if not any(m.group(k) for k in ("days", "h", "min")):
            continue
        try:
            entry = datetime(int(m.group("y")), int(m.group("m")), int(m.group("d")),
                             int(m.group("hh")), int(m.group("mm")), tzinfo=HELSINKI)
        except ValueError:
            continue
        dur = timedelta(
            days=int(m.group("days") or 0), hours=int(m.group("h") or 0),
            minutes=int(m.group("min") or 0), seconds=int(m.group("s") or 0),
        )
        if dur <= timedelta(0) or dur > timedelta(days=30):
            continue
        return entry, entry + dur
    return None


# ---------- Flyg (Finnair / Amadeus) ----------

_SEP = r"[\s|]+"
# Finnair-mail (bokning/ändring): "20:55 GOT 23:15 HEL … AY868", datum
# "2026-10-13" före tiderna.
_FINNAIR_TIMES_RE = re.compile(
    rf"(?P<dep>\d{{2}}:\d{{2}}){_SEP}(?P<o>[A-Z]{{3}}){_SEP}(?P<arr>\d{{2}}:\d{{2}}){_SEP}(?P<d>[A-Z]{{3}})\b"
)
_ISO_DATE_RE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
_FLIGHT_NO_RE = re.compile(r"\b(AY|SK|D8|DY|LH|KL|BT|LX|OS|AF|BA|LO|N7)\s?0*(\d{2,4})\b")
# Amadeus e-ticket: "| HELSINKI HELSINKI VANTAA | COPENHAGEN KASTRUP | AY0953 | Y |
# 25Aug | 07:25 | 08:05 |"
_AMADEUS_ROW_RE = re.compile(
    r"\|\s*(?P<o>[A-Z][A-Z .'-]{2,40}?)\s*\|\s*(?P<d>[A-Z][A-Z .'-]{2,40}?)\s*\|\s*"
    r"(?P<al>[A-Z0-9]{2})0*(?P<no>\d{1,4})\s*\|\s*[A-Z]\s*\|\s*"
    r"(?P<day>\d{1,2})(?P<mon>[A-Za-z]{3})\s*\|\s*(?P<dep>\d{2}:\d{2})\s*\|\s*(?P<arr>\d{2}:\d{2})"
)
_DEP_YEAR_RE = re.compile(r"\bDEP:\s*(\d{1,2})([A-Z]{3})(\d{4})\b", re.IGNORECASE)
_ISSUED_RE = re.compile(r"\b(\d{2})([A-Z]{3})(\d{2})\b\s*IATA", re.IGNORECASE)
_PNR_RE = re.compile(
    r"(?:booking reference|bokningsnummer|varaustunnus|booking number|bokningsreferens)"
    r"\s*[:|]?\s*\|?\s*([A-Z0-9]{6})\b",
    re.IGNORECASE,
)
_TICKET_RE = re.compile(
    r"(?:ticket number|e-biljettnummer|e-ticket number|e-lipun numero|biljettnummer)"
    r"\s*[:|]?\s*\|?\s*[:]?\s*(\d{3}-?\d{10})\b",
    re.IGNORECASE,
)


def _tz(iata: str | None) -> ZoneInfo:
    from app.services.trip_detector import iata_country

    cc = iata_country(iata) if iata else None
    return ZoneInfo(_COUNTRY_TZ.get(cc or "", _DEFAULT_TZ))


def _segment(flight_no: str, origin: str | None, dest: str | None, day: date,
             dep: str, arr: str) -> dict:
    dh, dm = (int(x) for x in dep.split(":"))
    ah, am = (int(x) for x in arr.split(":"))
    dep_dt = datetime(day.year, day.month, day.day, dh, dm, tzinfo=_tz(origin))
    arr_dt = datetime(day.year, day.month, day.day, ah, am, tzinfo=_tz(dest))
    if arr_dt <= dep_dt:
        arr_dt += timedelta(days=1)  # landar efter midnatt
    return {
        "flight_no": flight_no,
        "origin": origin,
        "dest": dest,
        "date": day.isoformat(),
        "dep": dep_dt.isoformat(),
        "arr": arr_dt.isoformat(),
    }


def parse_flight_segments(text: str | None) -> list[dict]:
    """Flygsegment ur Finnair- eller Amadeus-text. Tom lista om inget hittas."""
    from app.services.trip_detector import city_to_iata

    text = text or ""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # Amadeus-kvitto (tabellrader).
    year = None
    m = _DEP_YEAR_RE.search(text)
    if m:
        year = int(m.group(3))
    else:
        m = _ISSUED_RE.search(text)
        if m:
            year = 2000 + int(m.group(3))
    for r in _AMADEUS_ROW_RE.finditer(text):
        mon = _MONTHS.get(r.group("mon").lower())
        if not mon or year is None:
            continue
        try:
            day = date(year, mon, int(r.group("day")))
        except ValueError:
            continue
        no = f"{r.group('al')} {int(r.group('no'))}"
        key = (no, day.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(_segment(no, city_to_iata(r.group("o")), city_to_iata(r.group("d")),
                            day, r.group("dep"), r.group("arr")))
    if out:
        return out

    # Finnair-mail (bokningsbekräftelse / bokningsändring).
    for t in _FINNAIR_TIMES_RE.finditer(text):
        dates = list(_ISO_DATE_RE.finditer(text, 0, t.start()))
        if not dates:
            continue
        y, mo, d = (int(x) for x in dates[-1].groups())
        fm = _FLIGHT_NO_RE.search(text, t.end(), t.end() + 400)
        if not fm:
            continue
        no = f"{fm.group(1)} {int(fm.group(2))}"
        day = date(y, mo, d)
        key = (no, day.isoformat())
        if key in seen:
            continue
        seen.add(key)
        out.append(_segment(no, t.group("o"), t.group("d"), day, t.group("dep"), t.group("arr")))
    return out


def parse_booking_ref(text: str | None) -> str | None:
    m = _PNR_RE.search(text or "")
    return m.group(1).upper() if m else None


def parse_ticket_number(text: str | None) -> str | None:
    m = _TICKET_RE.search(text or "")
    return m.group(1).replace("-", "") if m else None


def extract_details(subject: str | None, body_text: str | None,
                    body_html: str | None) -> dict:
    text = message_text(subject, body_text, body_html)
    out: dict = {}
    park = parse_parking(text)
    if park:
        out["parking_entry_at"] = park[0].isoformat()
        out["parking_exit_at"] = park[1].isoformat()
    segs = parse_flight_segments(text)
    if segs:
        out["flight_segments"] = segs
    pnr = parse_booking_ref(text)
    if pnr:
        out["booking_ref"] = pnr
    tkt = parse_ticket_number(text)
    if tkt:
        out["ticket_number"] = tkt
    return out


# ---------- Vilka kvitton har detaljer att läsa ----------

_PARKING_SENDERS = ("moovy", "finavia")
_FLIGHT_SENDERS = ("finnair", "amadeus")


def needs_details(m: ProcessedMessage) -> str | None:
    """'parking' | 'flight' | None."""
    hay = " ".join(str(v or "") for v in (m.sender, m.vendor, m.subject)).lower()
    if any(s in hay for s in _PARKING_SENDERS):
        return "parking"
    if any(s in hay for s in _FLIGHT_SENDERS) or getattr(m, "kind", None) == "change_fee":
        return "flight"
    return None


def apply_details(m: ProcessedMessage, details: dict) -> None:
    for key in ("parking_entry_at", "parking_exit_at", "flight_segments",
                "booking_ref", "ticket_number"):
        if key in details:
            setattr(m, key, details[key])
    m.details_status = "ok" if details else "no_details"


def enrich_receipts(db: Session, gmail=None, limit: int = 150,
                    since: date = DETAILS_SINCE) -> dict:
    """Läs mailtexten för parkerings- och flygkvitton som saknar detaljer.
    Varje rad läses en gång (details_status sätts). Endast Gmail-läsning.
    Committar."""
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.details_status.is_(None))
        .order_by(ProcessedMessage.id.desc())
        .all()
    )
    todo = []
    for m in rows:
        if not needs_details(m):
            continue
        if m.message_id.startswith("manual:"):
            continue
        when = m.received_at.date() if m.received_at else None
        if when is not None and when < since:
            continue
        todo.append(m)
    todo = todo[:limit]
    if not todo:
        return {"status": "ok", "read": 0}
    if gmail is None:
        try:
            from app.services.gmail_client import GmailClient

            gmail = GmailClient()
        except Exception as exc:  # noqa: BLE001
            logger.warning("C44: Gmail ej tillgängligt för kvittodetaljer: %s", exc)
            return {"status": "gmail_unavailable", "read": 0}
    read = found = 0
    for m in todo:
        try:
            subject, body_text, body_html = gmail.fetch_body(m.message_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("C44: kunde inte läsa %s: %s", m.message_id, exc)
            m.details_status = "error"
            continue
        details = extract_details(subject or m.subject, body_text, body_html)
        apply_details(m, details)
        read += 1
        found += 1 if details else 0
    db.commit()
    logger.info("C44: kvittodetaljer lästa för %d kvitton (%d med detaljer)", read, found)
    return {"status": "ok", "read": read, "with_details": found}


# ---------- Dubbletter ----------


def _norm_vendor(v: str | None) -> str:
    return re.sub(r"[^a-z]", "", (v or "").lower())[:12]


def _day_key(m: ProcessedMessage) -> str:
    return str(m.receipt_date or m.bezala_payment_date or "")[:10]


def find_duplicates(rows: list[ProcessedMessage]) -> list[dict]:
    """Grupper av kvitton som är samma köp: samma belopp + datum och samma
    e-biljett/bokningsnummer (eller samma Gmail-meddelande / samma ämne när
    numren saknas). Returnerar [{'keep': m, 'dupes': [m, …]}]."""
    groups: dict[tuple, list[ProcessedMessage]] = {}
    for m in rows:
        if m.amount is None or not _day_key(m):
            continue
        base = (round(float(m.amount), 2), (m.currency or "").upper(), _day_key(m),
                _norm_vendor(m.vendor))
        groups.setdefault(base, []).append(m)
    out: list[dict] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # Dela upp efter identitet: biljett > PNR > message_id/ämne.
        clusters: list[list[ProcessedMessage]] = []
        for m in sorted(members, key=lambda r: r.id or 0):
            placed = False
            for c in clusters:
                if _same_purchase(c[0], m):
                    c.append(m)
                    placed = True
                    break
            if not placed:
                clusters.append([m])
        for c in clusters:
            if len(c) < 2:
                continue
            # Behåll det kopplade (Bezala) kvittot, annars det äldsta.
            keep = next((m for m in c if m.bezala_transaction_id), c[0])
            out.append({"keep": keep, "dupes": [m for m in c if m is not keep]})
    return out


def _same_purchase(a: ProcessedMessage, b: ProcessedMessage) -> bool:
    if a.message_id == b.message_id:
        return True
    if a.ticket_number and b.ticket_number:
        return a.ticket_number == b.ticket_number
    if a.booking_ref and b.booking_ref:
        return a.booking_ref == b.booking_ref
    return bool(a.subject) and (a.subject or "").strip().lower() == (b.subject or "").strip().lower()


def mark_duplicates(db: Session) -> dict:
    """Sätt duplicate_of på dubbletterna (Bezala rörs aldrig). Idempotent.
    Committar. Returnerar rapport."""
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.status == "saved")
        .all()
    )
    groups = find_duplicates(rows)
    marked = 0
    for g in groups:
        for d in g["dupes"]:
            if d.duplicate_of != g["keep"].message_id:
                d.duplicate_of = g["keep"].message_id
                marked += 1
    db.commit()
    return {"groups": len(groups), "newly_marked": marked}


def duplicate_report(db: Session, year: int | None = None) -> dict:
    rows = (
        db.query(ProcessedMessage)
        .filter(ProcessedMessage.deleted_at.is_(None))
        .filter(ProcessedMessage.status == "saved")
        .all()
    )
    out = []
    for g in find_duplicates(rows):
        keep = g["keep"]
        if year and not _day_key(keep).startswith(str(year)):
            continue
        members = [keep] + g["dupes"]
        out.append({
            "date": _day_key(keep),
            "vendor": keep.vendor,
            "amount": keep.amount,
            "currency": keep.currency,
            "booking_ref": keep.booking_ref,
            "ticket_number": keep.ticket_number,
            "kept_message_id": keep.message_id,
            "receipts": [
                {
                    "id": m.id,
                    "message_id": m.message_id,
                    "subject": m.subject,
                    "bezala_draft": bool(m.bezala_transaction_id),
                    "bezala_transaction_id": m.bezala_transaction_id,
                    "duplicate": m is not keep,
                }
                for m in members
            ],
        })
    out.sort(key=lambda g: g["date"])
    drafts = sum(1 for g in out for r in g["receipts"] if r["duplicate"] and r["bezala_draft"])
    return {
        "groups": len(out),
        "duplicate_receipts": sum(len(g["receipts"]) - 1 for g in out),
        "duplicates_with_bezala_draft": drafts,
        "items": out,
    }
