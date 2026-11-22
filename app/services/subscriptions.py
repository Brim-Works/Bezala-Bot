"""C47 Del 4 — prenumerationer på autopilot.

* Tabell `subscriptions`. Nya förslag (detekterade eller förifyllda) är
  inactive tills Mikko bekräftar dem i svep-kön.
* Förifyllda: Cursor, Lovable, Anthropic (Claude), Bolt/StackBlitz.
* Föreslås automatiskt: samma leverantör ≥2 olika månader med liknande
  belopp (±10 %). Resor/mat räknas aldrig.
* Kortrad som matchar en aktiv prenumeration + kvitto finns → kopplas
  automatiskt med beskrivning "Cursor subscription - Nov 2026".
* Flaggas till svep-kön: beloppet avviker >10 %, eller inget kvitto 5 dagar
  efter dragningen.
* Konto: det Mikko valt tidigare för leverantören (bezala_vendor_mappings).
  Gissas aldrig — saknas det lämnas fältet tomt och flaggas.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import CardLine, MaintenanceTask, ProcessedMessage, Subscription

logger = logging.getLogger(__name__)

AMOUNT_TOLERANCE = 0.10
NO_RECEIPT_DAYS = 5
SEED_TASK = "c47_seed_subscriptions"

SEEDS: tuple[dict, ...] = (
    {"name": "Cursor", "vendor_pattern": "cursor|anysphere", "sender": "cursor.com"},
    {"name": "Lovable", "vendor_pattern": "lovable", "sender": "lovable.dev"},
    {"name": "Anthropic (Claude)", "vendor_pattern": "anthropic|claude.ai",
     "sender": "mail.anthropic.com"},
    {"name": "Bolt/StackBlitz", "vendor_pattern": "stackblitz|bolt.new",
     "sender": "stripe.com"},
)

# Kategorier som aldrig är prenumerationer (resor, mat).
NON_SUBSCRIPTION_CATEGORIES = {
    "flyg", "tåg", "kollektivtrafik", "taxi", "bilhyra", "parkering", "hotell",
    "mat", "representation",
}


def _short_name(name: str) -> str:
    return re.sub(r"\s*\(.*?\)\s*", " ", name).strip()


def default_template(name: str) -> str:
    return f"{_short_name(name)} subscription - {{month}}"


def describe(sub: Subscription, d: date | None) -> str:
    tpl = sub.description_template or default_template(sub.name)
    month = d.strftime("%b %Y") if d else ""
    return tpl.replace("{month}", month).replace("{name}", _short_name(sub.name)).strip(" -")


def _alts(pattern: str | None) -> list[str]:
    return [a.strip().lower() for a in (pattern or "").split("|") if a.strip()]


def matches_text(sub: Subscription, *texts: str | None) -> bool:
    hay = " ".join((t or "").lower() for t in texts)
    return any(a in hay for a in _alts(sub.vendor_pattern))


def account_for(db: Session, sub: Subscription) -> int | None:
    """Kontot Mikko valt tidigare för leverantören (vendor-mappning)."""
    from app.models import BezalaVendorMapping

    for m in db.query(BezalaVendorMapping).all():
        p = (m.vendor_pattern or "").lower().strip()
        if p and any(p in a or a in p for a in _alts(sub.vendor_pattern)):
            return m.bezala_account_id
    return None


def seed_subscriptions(db: Session) -> int:
    if db.query(MaintenanceTask).filter(MaintenanceTask.name == SEED_TASK).first():
        return 0
    existing = {s.name.lower() for s in db.query(Subscription).all()}
    added = 0
    for seed in SEEDS:
        if seed["name"].lower() in existing:
            continue
        sub = Subscription(
            name=seed["name"], vendor_pattern=seed["vendor_pattern"],
            sender=seed["sender"], cadence="monthly", active=False,
            origin="seed", review_status="suggested",
            description_template=default_template(seed["name"]),
        )
        db.add(sub)
        added += 1
    db.add(MaintenanceTask(name=SEED_TASK))
    db.commit()
    return added


def history(db: Session, sub: Subscription) -> list[tuple[date, float, str]]:
    """(datum, belopp, valuta) från kortrader + kvitton för leverantören."""
    out = []
    for c in db.query(CardLine).all():
        if c.txn_date and c.amount and c.amount > 0 and matches_text(sub, c.description, c.vendor):
            out.append((c.txn_date, float(c.amount), (c.currency or "EUR").upper()))
    from app.services.trip_completeness import _parse_day

    for m in (db.query(ProcessedMessage)
              .filter(ProcessedMessage.deleted_at.is_(None))
              .filter(ProcessedMessage.status == "saved").all()):
        d = _parse_day(m.receipt_date)
        if d and m.amount and matches_text(sub, m.vendor, m.sender):
            out.append((d, float(m.amount), (m.currency or "EUR").upper()))
    out.sort()
    return out


def serialize(db: Session, sub: Subscription) -> dict:
    flags = []
    if sub.account is None:
        flags.append("account_missing")
    if sub.expected_amount is None:
        flags.append("amount_unknown")
    hist = history(db, sub)[-6:]
    return {
        "id": sub.id, "name": sub.name, "vendor_pattern": sub.vendor_pattern,
        "sender": sub.sender, "expected_amount": sub.expected_amount,
        "currency": sub.currency, "cadence": sub.cadence,
        "description_template": sub.description_template,
        "example_description": describe(sub, date.today()),
        "account": sub.account, "active": sub.active, "origin": sub.origin,
        "review_status": sub.review_status, "flags": flags,
        "recent": [{"date": d.isoformat(), "amount": a, "currency": c} for d, a, c in hist],
    }


def list_subscriptions(db: Session) -> list[dict]:
    return [serialize(db, s) for s in db.query(Subscription).order_by(Subscription.name).all()]


def confirm(db: Session, sub: Subscription) -> dict:
    """Mikko sa ja: aktivera, fyll i belopp/valuta/konto från historiken."""
    before = {"active": sub.active, "review_status": sub.review_status,
              "expected_amount": sub.expected_amount, "currency": sub.currency,
              "account": sub.account}
    sub.active = True
    sub.review_status = "confirmed"
    if sub.expected_amount is None:
        hist = history(db, sub)
        if hist:
            _, amount, cur = hist[-1]
            sub.expected_amount, sub.currency = amount, cur
    if sub.account is None:
        sub.account = account_for(db, sub)
    db.commit()
    return before


def restore(db: Session, sub: Subscription, before: dict) -> None:
    for k, v in before.items():
        setattr(sub, k, v)
    db.commit()


# ---------- automatiska förslag ----------


def _vendor_key(text: str | None) -> str | None:
    from app.services.trip_completeness import _vendor_words

    words = _vendor_words(text)
    return words[0] if words else None


def detect_subscriptions(db: Session) -> list[Subscription]:
    """Föreslå nya: samma leverantör ≥2 olika månader med liknande belopp."""
    from app.services.trip_completeness import _parse_day

    known = db.query(Subscription).all()
    excluded: set[str] = set()
    entries: dict[str, list[tuple[date, float, str, str]]] = defaultdict(list)
    for m in (db.query(ProcessedMessage)
              .filter(ProcessedMessage.deleted_at.is_(None))
              .filter(ProcessedMessage.status == "saved").all()):
        key = _vendor_key(m.vendor)
        if not key:
            continue
        if (m.category or "").strip().lower() in NON_SUBSCRIPTION_CATEGORIES or m.flight_segments:
            excluded.add(key)
            continue
        d = _parse_day(m.receipt_date)
        if d and m.amount and m.amount > 0:
            entries[key].append((d, float(m.amount), (m.currency or "EUR").upper(), m.vendor))
    for c in db.query(CardLine).all():
        key = _vendor_key(c.vendor or c.description)
        if key and c.txn_date and c.amount and c.amount > 0:
            entries[key].append((c.txn_date, float(c.amount), (c.currency or "EUR").upper(),
                                 c.vendor or key))
    from app.services.bill_line_status import is_parking_line

    new: list[Subscription] = []
    for key, rows in entries.items():
        if key in excluded:
            continue
        if any(s.vendor_pattern and (key in s.vendor_pattern.lower()
               or matches_text(s, key)) for s in known + new):
            continue
        if any(is_parking_line(r[3]) for r in rows):
            continue
        by_month: dict[tuple[int, int, str], float] = {}
        for d, amount, cur, _ in sorted(rows):
            by_month[(d.year, d.month, cur)] = amount
        best = None
        for cur in {k[2] for k in by_month}:
            amounts = [a for (y, mo, c), a in sorted(by_month.items()) if c == cur]
            if len(amounts) < 2:
                continue
            ref = amounts[-1]
            similar = [a for a in amounts if abs(a - ref) <= AMOUNT_TOLERANCE * max(ref, 0.01)]
            if len(similar) >= 2:
                best = (ref, cur)
        if best is None:
            continue
        name = rows[-1][3] or key
        name = re.sub(r"\s+", " ", str(name)).strip()[:80].title()
        sub = Subscription(
            name=name, vendor_pattern=key, expected_amount=best[0], currency=best[1],
            cadence="monthly", active=False, origin="detected", review_status="suggested",
            description_template=default_template(name),
        )
        sub.account = account_for(db, sub)
        db.add(sub)
        new.append(sub)
    if new:
        db.commit()
        logger.info("[c47-subs] %d nya prenumerationsförslag: %s", len(new), [s.name for s in new])
    return new


# ---------- kortrader ----------


def active_for_line(subs: list[Subscription], c: CardLine) -> Subscription | None:
    return next((s for s in subs if s.active and matches_text(s, c.description, c.vendor)), None)


def deviates(sub: Subscription, c: CardLine) -> bool:
    if sub.expected_amount is None or c.amount is None:
        return False
    if sub.currency and c.currency and sub.currency.upper() != c.currency.upper():
        return False
    return abs(float(c.amount) - sub.expected_amount) > AMOUNT_TOLERANCE * sub.expected_amount


def line_flags(db: Session, today: date | None = None) -> list[dict]:
    """Kortrader på aktiva prenumerationer som behöver Mikko: avvikande
    belopp eller inget kvitto 5 dagar efter dragningen."""
    from app.services.card_line_coupling import suggest_receipts

    today = today or date.today()
    subs = [s for s in db.query(Subscription).all() if s.active]
    if not subs:
        return []
    out = []
    lines = db.query(CardLine).filter(CardLine.status == "missing").all()
    pending_receipt = []
    for c in lines:
        sub = active_for_line(subs, c)
        if sub is None:
            continue
        if deviates(sub, c):
            out.append({"kind": "subdev", "line": c, "sub": sub})
        elif c.txn_date and c.txn_date <= today - timedelta(days=NO_RECEIPT_DAYS):
            pending_receipt.append((c, sub))
    if pending_receipt:
        sugg = suggest_receipts(db, [c for c, _ in pending_receipt])
        for c, sub in pending_receipt:
            if sugg.get(c.bill_line_id) is None:
                out.append({"kind": "subnr", "line": c, "sub": sub})
    return out


def process_lines(db: Session, *, couple=None) -> dict:
    """Koppla kortrader på aktiva prenumerationer automatiskt när kvitto
    finns och beloppet stämmer (±10 %)."""
    from app.services.card_line_coupling import suggest_receipts

    if couple is None:
        from app.services.card_line_coupling import couple_receipt as couple
    subs = [s for s in db.query(Subscription).all() if s.active]
    if not subs:
        return {"coupled": []}
    todo = []
    for c in db.query(CardLine).filter(CardLine.status == "missing").all():
        sub = active_for_line(subs, c)
        if sub is not None and not deviates(sub, c) and (c.amount or 0) > 0:
            todo.append((c, sub))
    if not todo:
        return {"coupled": []}
    sugg = suggest_receipts(db, [c for c, _ in todo])
    coupled = []
    for c, sub in todo:
        s = sugg.get(c.bill_line_id)
        if not s:
            continue
        row = (db.query(ProcessedMessage)
               .filter(ProcessedMessage.message_id == s["message_id"]).first())
        if row is None or not matches_text(sub, row.vendor, row.sender):
            continue
        old = row.ai_description_en
        row.ai_description_en = describe(sub, c.txn_date)
        db.commit()
        try:
            couple(db, c.bill_line_id, row.message_id)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            row.ai_description_en = old
            db.commit()
            logger.warning("[c47-subs] koppling %s ← %s misslyckades: %s",
                           c.bill_line_id, row.message_id, exc)
            continue
        coupled.append({"bill_line_id": c.bill_line_id, "message_id": row.message_id,
                        "subscription": sub.name, "description": row.ai_description_en})
        logger.info("[c47-subs] auto-kopplat %s ← %s (%s)", c.bill_line_id,
                    row.message_id, row.ai_description_en)
    return {"coupled": coupled}
