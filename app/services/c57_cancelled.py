"""C57 - köp + återbetalning av samma bokning = avbokad, räknas som noll.

Fall i prod:
  - Finnair 8HZCXX 674,48 EUR (resa 82): köpt 13 aug, återbetald 15 aug
    (kortraden '... -674.48 EUR'). Mikko bokade om som 7OTOGJ 514,48 EUR.
  - Finnair 8NLAI4 134,48 EUR (26 nov): e-biljetten är kopplad till
    återbetalningsraden '-134.48 EUR' (trip cancelled).

En bokning är avbokad när något av detta finns (bara bottens egna data läses):
  1. Ett köpkvitto (positivt belopp, ingen återbetalningstext) som är kopplat
     till en negativ kortrad med samma belopp (kvitto + Bezala-rad).
  2. Två kvitton från samma leverantör med samma belopp där det ena är en
     återbetalning (negativt belopp, negativ kortrad eller ordet refund /
     återbetalning / avbokning / cancelled) och det andra inte är det, samma
     bokningsnummer (eller inget), återbetalningen högst 180 dagar efter köpet.
Hela bokningen följer med: alla kvitton med samma leverantör + bokningsnummer
och alla dubbletter (duplicate_of) av dem.

Bokningsbekräftelse + e-biljett av samma bokning (båda positiva, ingen
återbetalning) är en vanlig dubblett (C44) och berörs inte.

Delvis återbetalning (olika belopp) räknas inte som avbokad.

Ångra: BotFlag 'c57_keep:<bokning>' = 'true' gör att bokningen räknas igen
(set_cancelled_booking). Ingenting raderas, inget skrivs till Bezala.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import BotFlag, ProcessedMessage

LABEL_SV = "Avbokad och återbetald (räknas inte)"
LABEL_EN = "Cancelled and refunded (not counted)"
MAX_DAYS = 180
FLAG_PREFIX = "c57_keep:"

_REFUND_WORDS = re.compile(
    r"refund|återbetal|aterbetal|avbok|\bcancell?ed\b|peruutettu|hyvitys|palautus|"
    r"credit ?note|kreditnota", re.IGNORECASE)
# Kortradens text slutar med ett negativt belopp: '... FI -674.48 EUR'.
_NEG_LINE = re.compile(r"(?:^|\s)-\s?\d+(?:[.,]\d+)?\s*[A-Z]{3}\s*$")


def _g(m, key):
    return m.get(key) if isinstance(m, dict) else getattr(m, key, None)


def vendor_key(v: str | None) -> str:
    t = unicodedata.normalize("NFKD", (v or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    words = re.findall(r"[a-z0-9]+", t)
    return words[0] if words else ""


def _day(*values) -> date | None:
    for v in values:
        if not v:
            continue
        try:
            return date.fromisoformat(str(v)[:10])
        except ValueError:
            continue
    return None


def _abs(v) -> float | None:
    try:
        return round(abs(float(v)), 2) if v is not None else None
    except (TypeError, ValueError):
        return None


def refund_line(m) -> bool:
    """Kvittot är kopplat till en negativ kortrad (återbetalning)."""
    amt = _g(m, "bezala_payment_amount")
    if amt is not None and amt < 0:
        return True
    return bool(_NEG_LINE.search((_g(m, "bezala_payment_merchant") or "").strip()))


def refund_text(m) -> bool:
    return bool(_REFUND_WORDS.search(" ".join(
        str(_g(m, k)) for k in ("subject", "file_name") if _g(m, k))))


def is_refund(m) -> bool:
    a = _g(m, "amount")
    return (a is not None and a < 0) or refund_line(m) or refund_text(m)


def _same(a, b) -> bool:
    return a is not None and b is not None and abs(a - b) < 0.01


def _ref(m) -> str:
    return (_g(m, "booking_ref") or "").strip().upper()


def booking_key(m) -> str:
    return f"{vendor_key(_g(m, 'vendor'))[:15]}:{_ref(m) or _g(m, 'message_id')}"


def detect(messages) -> dict[str, dict]:
    """message_id -> bokningsinfo för varje avbokat kvitto. Ren funktion
    (ProcessedMessage eller dict), utan BotFlag-undantag."""
    msgs = [m for m in messages if _g(m, "message_id") and _g(m, "vendor")]
    by_id = {_g(m, "message_id"): m for m in msgs}
    groups: dict[tuple[str, str], list] = {}
    for m in msgs:
        if _ref(m):
            groups.setdefault((vendor_key(_g(m, "vendor")), _ref(m)), []).append(m)

    seeds: list[tuple[object, object, str]] = []  # (köp, återbetalning, skäl)
    # 1. Köpkvitto kopplat till negativ kortrad med samma belopp.
    for m in msgs:
        a = _g(m, "amount")
        if a is None or a <= 0 or refund_text(m) or not refund_line(m):
            continue
        line = _abs(_g(m, "bezala_payment_amount"))
        if line is None or _same(line, _abs(a)):
            seeds.append((m, m, "receipt_on_refund_card_line"))
    # 2. Köp + återbetalning som två kvitton.
    refunds = [m for m in msgs if is_refund(m)]
    used: set[str] = set()
    for r in sorted(refunds, key=lambda x: str(_g(x, "message_id"))):
        rd = _day(_g(r, "bezala_payment_date"), _g(r, "receipt_date"))
        best = None
        for p in msgs:
            pid = _g(p, "message_id")
            if p is r or pid in used or is_refund(p):
                continue
            if vendor_key(_g(p, "vendor")) != vendor_key(_g(r, "vendor")):
                continue
            if (_g(p, "currency") or "EUR").upper() != (_g(r, "currency") or "EUR").upper():
                continue
            if not _same(_abs(_g(p, "amount")), _abs(_g(r, "amount"))):
                continue
            if _ref(p) and _ref(r) and _ref(p) != _ref(r):
                continue
            pd = _day(_g(p, "bezala_payment_date"), _g(p, "receipt_date"))
            if rd and pd and not (pd <= rd <= pd + timedelta(days=MAX_DAYS)):
                continue
            # Samma bokningsnummer går före bara samma belopp.
            rank = (int(bool(_ref(p)) and _ref(p) == _ref(r)), str(pid))
            if best is None or rank > best[0]:
                best = (rank, p)
        if best is not None:
            used.add(_g(best[1], "message_id"))
            seeds.append((best[1], r, "purchase_and_refund"))

    out: dict[str, dict] = {}
    for purchase, refund, reason in seeds:
        members = {_g(purchase, "message_id"), _g(refund, "message_id")}
        for m in (purchase, refund):
            if _ref(m):
                members |= {_g(x, "message_id")
                            for x in groups.get((vendor_key(_g(m, "vendor")), _ref(m)), [])}
        # Dubbletter (bokningsbekräftelse/e-biljett) följer originalet.
        changed = True
        while changed:
            changed = False
            for m in msgs:
                mid = _g(m, "message_id")
                if mid not in members and (_g(m, "duplicate_of") in members):
                    members.add(mid)
                    changed = True
        key = booking_key(purchase)
        overlap = sorted(members & set(out))
        info = out[overlap[0]] if overlap else None
        if info is None:
            info = {
                "booking": key,
                "vendor": _g(purchase, "vendor"),
                "booking_ref": _ref(purchase) or _ref(refund) or None,
                "amount": _abs(_g(purchase, "amount")),
                "currency": _g(purchase, "currency") or "EUR",
                "purchase_date": None,
                "refund_date": None,
                "reason": reason,
                "message_ids": [],
                "primary_message_id": _g(purchase, "message_id"),
                "label_sv": LABEL_SV,
                "label_en": LABEL_EN,
            }
        info["message_ids"] = sorted(set(info["message_ids"]) | members)
        # Köpdatum = köpets kortrad. Finns den inte i boten (köpraden saknar
        # kvitto, som för 8NLAI4) visas inget datum - kvittots datum är ofta
        # flygdagen och kan ligga efter återbetalningen.
        buy = [d for d in (_day(_g(by_id[x], "bezala_payment_date"))
                           for x in members if x in by_id and not refund_line(by_id[x])
                           and not is_refund(by_id[x])) if d]
        info["purchase_date"] = min(buy).isoformat() if buy else None
        rdays = [d for d in (_day(_g(by_id[x], "bezala_payment_date"))
                             for x in members if x in by_id and refund_line(by_id[x])) if d]
        info["refund_date"] = max(rdays).isoformat() if rdays else None
        for mid in members:
            out[mid] = info
    return out


def kept_bookings(db: Session) -> set[str]:
    rows = db.query(BotFlag).filter(BotFlag.key.like(f"{FLAG_PREFIX}%")).all()
    return {r.key[len(FLAG_PREFIX):] for r in rows if (r.value or "").lower() == "true"}


def _fingerprint(db: Session) -> tuple:
    """Billig signatur: ändras när kvitton läggs till, kopplas eller tas bort."""
    from sqlalchemy import func

    pm = ProcessedMessage
    row = db.query(func.count(pm.id), func.max(pm.id), func.max(pm.matched_at),
                   func.max(pm.deleted_at), func.count(pm.deleted_at),
                   func.count(pm.bezala_payment_merchant)).one()
    flags = tuple(sorted((r.key, r.value) for r in db.query(BotFlag)
                         .filter(BotFlag.key.like(f"{FLAG_PREFIX}%")).all()))
    return tuple(str(v) for v in row) + flags


def cancelled_messages(db: Session) -> dict[str, dict]:
    """message_id -> bokningsinfo, utan bokningar Mikko sagt ska räknas.
    Cachas per databassession så länge kvittotabellen inte ändrats."""
    fp = _fingerprint(db)
    cached = db.info.get("c57_cancelled")
    if cached and cached[0] == fp:
        return cached[1]
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None))
            .all())
    found = detect(rows)
    keep = kept_bookings(db)
    out = {mid: info for mid, info in found.items() if info["booking"] not in keep}
    db.info["c57_cancelled"] = (fp, out)
    return out


def list_cancelled(db: Session) -> list[dict]:
    """En rad per avbokad bokning (även de Mikko sagt ska räknas, markerade)."""
    rows = (db.query(ProcessedMessage)
            .filter(ProcessedMessage.deleted_at.is_(None)).all())
    keep = kept_bookings(db)
    seen: dict[str, dict] = {}
    for info in detect(rows).values():
        seen.setdefault(info["booking"], info | {"counted": info["booking"] in keep})
    return sorted(seen.values(), key=lambda i: (i.get("purchase_date") or "", i["booking"]))


def set_cancelled_booking(db: Session, booking: str, cancelled: bool,
                          note: str | None = None) -> dict:
    """cancelled=False: räkna bokningen igen (ångra). cancelled=True: tillbaka
    till automatiken. Flaggan raderas aldrig, bara värdet ändras."""
    from app.services.calendar_trip_edit import TripEditError

    known = {i["booking"] for i in list_cancelled(db)}
    if booking not in known:
        raise TripEditError(f"Unknown cancelled booking '{booking}'. Known: {sorted(known)}")
    key = f"{FLAG_PREFIX}{booking}"[:64]
    row = db.query(BotFlag).filter(BotFlag.key == key).first()
    if row is None:
        row = BotFlag(key=key, value="false")
        db.add(row)
    row.value = "false" if cancelled else "true"
    row.reason = note or ("Mikko: counted again" if not cancelled else "back to automatic")
    db.commit()
    return {"booking": booking, "cancelled": cancelled}
