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

C48:
* Detektering räknar BARA kortrader på företagskortet (card_lines +
  Bezala-snapshot på kopplade kvitton), aldrig enbart kvitton. Privata
  Apple-kvitton (Skyshowtime, Crunchyroll …) blir alltså inga förslag.
* Förslag utan kortrader avfärdas tyst (inga kortrader markeras privata).
* `SUBSCRIPTIONS_LIKELY_PRIVATE`: strömning/spel med kortrader på
  företagskortet → kort 'subpriv' ("Private - mark as private expense").
  Ja = kortraderna (även framtida) blir private_expense. Kan ångras.
* Dubbletter (OpenAI/ChatGPT) slås ihop. `recent` = bara kortrader.
* Belopp fylls från kortradernas typvärde. Konto sätts BARA av Mikko
  (set_subscription_account); utan konto kan prenumerationen inte aktiveras.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter, defaultdict
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

DEFAULT_LIKELY_PRIVATE = (
    "skyshowtime", "crunchyroll", "netflix", "spotify", "disney", "hbo", "max",
    "viaplay", "youtube premium", "apple music", "playstation", "xbox", "nintendo",
    "audible", "storytel",
)
# Samma tjänst under olika namn (Apple-kvitton säger ibland OpenAI, ibland ChatGPT).
ALIASES: tuple[frozenset[str], ...] = (frozenset({"openai", "chatgpt"}),)
PRIVATE_NOTE = "Private subscription: {name}"
ACCOUNT_MISSING = "Account missing - set it first"

from app.services.calendar_trip_edit import TripEditError  # noqa: E402


class SubscriptionError(TripEditError):
    """Fel som visas för Mikko (engelska)."""


def likely_private_words() -> list[str]:
    raw = os.environ.get("SUBSCRIPTIONS_LIKELY_PRIVATE")
    words = raw.split(",") if raw is not None else DEFAULT_LIKELY_PRIVATE
    return [w.strip().lower() for w in words if w.strip()]


def is_likely_private(sub: "Subscription") -> bool:
    hay = f" {(sub.name or '').lower()} {(sub.vendor_pattern or '').lower()} "
    hay = re.sub(r"[^a-z0-9|]+", " ", hay).replace("|", " ")
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", hay)
               for w in likely_private_words())


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


def card_history(db: Session, sub: Subscription) -> list[dict]:
    """Dragningar på FÖRETAGSKORTET: card_lines (alla statusar) + Bezala-
    snapshot på kopplade kvitton (en kortrad som kopplats före C42)."""
    from app.services.trip_completeness import _parse_day

    out: list[dict] = []
    seen: set[tuple] = set()
    for c in db.query(CardLine).all():
        if c.txn_date and c.amount and c.amount > 0 and matches_text(sub, c.description, c.vendor):
            cur = (c.currency or "EUR").upper()
            out.append({"date": c.txn_date, "amount": float(c.amount), "currency": cur,
                        "source": "card_line", "bill_line_id": c.bill_line_id,
                        "status": c.status})
            seen.add((c.txn_date, round(float(c.amount), 2)))
    for m in (db.query(ProcessedMessage)
              .filter(ProcessedMessage.deleted_at.is_(None))
              .filter(ProcessedMessage.bezala_payment_amount.isnot(None)).all()):
        d = _parse_day(m.bezala_payment_date)
        if not d or not m.bezala_payment_amount or m.bezala_payment_amount <= 0:
            continue
        if not matches_text(sub, m.bezala_payment_merchant, m.vendor):
            continue
        key = (d, round(float(m.bezala_payment_amount), 2))
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": d, "amount": float(m.bezala_payment_amount),
                    "currency": (m.bezala_payment_currency or "EUR").upper(),
                    "source": "card_line", "bill_line_id": None, "status": "coupled",
                    "message_id": m.message_id})
    out.sort(key=lambda r: r["date"])
    return out


def receipt_history(db: Session, sub: Subscription) -> list[ProcessedMessage]:
    return [m for m in (db.query(ProcessedMessage)
                        .filter(ProcessedMessage.deleted_at.is_(None))
                        .filter(ProcessedMessage.status == "saved")
                        .filter(ProcessedMessage.duplicate_of.is_(None)).all())
            if matches_text(sub, m.vendor, m.file_name)]


def history(db: Session, sub: Subscription) -> list[tuple[date, float, str]]:
    """(datum, belopp, valuta) — C48: bara kortrader på företagskortet."""
    return [(r["date"], r["amount"], r["currency"]) for r in card_history(db, sub)]


def typical_amount(db: Session, sub: Subscription) -> tuple[float, str] | None:
    """Typvärdet av de senaste (≤6) kortraderna."""
    rows = card_history(db, sub)[-6:]
    if not rows:
        return None
    counts = Counter((r["amount"], r["currency"]) for r in rows)
    best = max(counts.values())
    # vid lika: det senaste
    for r in reversed(rows):
        if counts[(r["amount"], r["currency"])] == best:
            return r["amount"], r["currency"]
    return None


def serialize(db: Session, sub: Subscription) -> dict:
    flags = []
    if sub.account is None:
        flags.append("account_missing")
    if sub.expected_amount is None:
        flags.append("amount_unknown")
    cards = card_history(db, sub)
    if not cards:
        flags.append("no_card_lines")
    receipts = receipt_history(db, sub)
    return {
        "id": sub.id, "name": sub.name, "vendor_pattern": sub.vendor_pattern,
        "sender": sub.sender, "expected_amount": sub.expected_amount,
        "currency": sub.currency, "cadence": sub.cadence,
        "description_template": sub.description_template,
        "example_description": describe(sub, date.today()),
        "account": sub.account, "suggested_account": account_for(db, sub),
        "active": sub.active, "origin": sub.origin,
        "review_status": sub.review_status, "kind": sub.kind, "note": sub.note,
        "flags": flags,
        "blocked": ACCOUNT_MISSING if sub.account is None else None,
        "recent": [{"date": r["date"].isoformat(), "amount": r["amount"],
                    "currency": r["currency"], "source": "card_line",
                    "bill_line_id": r.get("bill_line_id"), "status": r.get("status")}
                   for r in cards[-6:]],
        "card_lines": len(cards),
        "receipts": len(receipts),
    }


def list_subscriptions(db: Session) -> list[dict]:
    return [serialize(db, s) for s in db.query(Subscription).order_by(Subscription.name).all()]


def confirm(db: Session, sub: Subscription) -> dict:
    """Mikko sa ja: aktivera. C48: kräver konto (sätts bara av Mikko);
    beloppet fylls från kortradernas typvärde om det saknas."""
    if sub.account is None:
        raise SubscriptionError(f"{ACCOUNT_MISSING} (set_subscription_account({sub.id}, account)).")
    before = {"active": sub.active, "review_status": sub.review_status,
              "expected_amount": sub.expected_amount, "currency": sub.currency,
              "account": sub.account}
    sub.active = True
    sub.review_status = "confirmed"
    if sub.expected_amount is None:
        typ = typical_amount(db, sub)
        if typ:
            sub.expected_amount, sub.currency = typ
    db.commit()
    return before


def set_account(db: Session, subscription_id: int, account, *, accounts=None) -> dict:
    """Mikkos val av Bezala-konto. accounts = Bezalas kontolista (för
    kontroll); None = kontrolleras inte (Bezala ej nåbart)."""
    sub = db.query(Subscription).filter(Subscription.id == int(subscription_id)).first()
    if sub is None:
        raise SubscriptionError(f"Subscription {subscription_id} not found.")
    if account is None or str(account).strip() == "":
        sub.account = None
        db.commit()
        return serialize(db, sub)
    try:
        acc = int(str(account).strip())
    except ValueError as exc:
        raise SubscriptionError("account must be the numeric Bezala account id "
                                "(see list_bezala_accounts).") from exc
    if accounts:
        ids = {int(a["id"]) for a in accounts if str(a.get("id", "")).isdigit()}
        if ids and acc not in ids:
            raise SubscriptionError(f"Account {acc} is not in Bezala's account list "
                                    "(see list_bezala_accounts).")
    sub.account = acc
    db.commit()
    logger.info("[c48-subs] konto för %s satt till %s av Mikko", sub.name, acc)
    return serialize(db, sub)


def bezala_accounts(db: Session, *, client=None) -> dict:
    """Read-only: Bezalas kontolista + konton Mikko valt per leverantör."""
    from app.models import BezalaVendorMapping

    accounts: list[dict] = []
    error = None
    close = False
    try:
        if client is None:
            from app.services.bezala_client import BezalaClient

            client = BezalaClient()
            close = True
        for a in client.list_accounts() or []:
            accounts.append({k: a.get(k) for k in ("id", "name", "code", "number")
                             if a.get(k) is not None})
    except Exception as exc:  # noqa: BLE001
        error = f"Bezala account list unavailable: {exc}"[:300]
    finally:
        if close:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
    names = {a.get("id"): a.get("name") for a in accounts}
    earlier = [{"vendor_pattern": m.vendor_pattern, "account": m.bezala_account_id,
                "name": names.get(m.bezala_account_id)}
               for m in db.query(BezalaVendorMapping).all()]
    out = {"accounts": accounts, "chosen_per_vendor": earlier}
    if error:
        out["error"] = error
    return out


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
    """Föreslå nya: samma leverantör ≥2 olika månader med liknande belopp.
    C48: bara dragningar på FÖRETAGSKORTET räknas (card_lines + Bezala-
    snapshot), aldrig enbart kvitton."""
    from app.services.trip_completeness import _parse_day

    known = db.query(Subscription).all()
    excluded: set[str] = set()
    entries: dict[str, list[tuple[date, float, str, str]]] = defaultdict(list)
    for m in (db.query(ProcessedMessage)
              .filter(ProcessedMessage.deleted_at.is_(None))
              .filter(ProcessedMessage.status == "saved").all()):
        key = _vendor_key(m.vendor)
        if key and ((m.category or "").strip().lower() in NON_SUBSCRIPTION_CATEGORIES
                    or m.flight_segments):
            excluded.add(key)
        # Kopplat kvitto: Bezala-snapshoten är kortraden.
        d = _parse_day(m.bezala_payment_date)
        if d and m.bezala_payment_amount and m.bezala_payment_amount > 0:
            k = _vendor_key(m.bezala_payment_merchant) or key
            if k:
                entries[k].append((d, float(m.bezala_payment_amount),
                                   (m.bezala_payment_currency or "EUR").upper(),
                                   m.vendor or k))
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
        if is_likely_private(sub):
            sub.kind = "likely_private"
        db.add(sub)
        new.append(sub)
    if new:
        db.commit()
        logger.info("[c47-subs] %d nya prenumerationsförslag: %s", len(new), [s.name for s in new])
    return new


# ---------- C48: städning av förslag + privata ----------


def _alias_group(sub: Subscription) -> frozenset[str] | None:
    alts = set(_alts(sub.vendor_pattern)) | {w.lower() for w in re.findall(r"[a-z0-9]+", (sub.name or "").lower())}
    for g in ALIASES:
        if alts & g:
            return g
    return None


def _same_service(db: Session, a: Subscription, b: Subscription) -> bool:
    ga, gb = _alias_group(a), _alias_group(b)
    if ga is not None and ga == gb:
        return True
    if a.expected_amount is None or a.expected_amount != b.expected_amount:
        return False
    if (a.currency or "") != (b.currency or ""):
        return False
    from app.services.trip_completeness import _parse_day

    def days(sub):
        out = {r["date"] for r in card_history(db, sub)}
        out |= {d for d in (_parse_day(m.receipt_date) for m in receipt_history(db, sub)) if d}
        return out
    return len(days(a) & days(b)) >= 2


def merge_duplicates(db: Session, run: str = "subscriptions") -> list[dict]:
    """Slå ihop förslag som är samma tjänst (OpenAI/ChatGPT)."""
    from app.services.cleanup_log import log

    subs = [s for s in db.query(Subscription).order_by(Subscription.id).all()
            if s.review_status == "suggested"]
    merged: list[dict] = []
    gone: set[int] = set()
    for i, a in enumerate(subs):
        if a.id in gone:
            continue
        for b in subs[i + 1:]:
            if b.id in gone or not _same_service(db, a, b):
                continue
            alts = _alts(a.vendor_pattern)
            for alt in _alts(b.vendor_pattern):
                if alt not in alts:
                    alts.append(alt)
            a.vendor_pattern = "|".join(alts)[:200]
            if a.expected_amount is None:
                a.expected_amount, a.currency = b.expected_amount, b.currency
            b.review_status = "merged"
            b.active = False
            b.note = f"merged into subscription {a.id}"
            gone.add(b.id)
            merged.append({"kept": a.id, "merged": b.id, "pattern": a.vendor_pattern})
            log(db, run, "subscription_merged", None,
                before={"kept": a.name, "merged": b.name},
                after={"vendor_pattern": a.vendor_pattern}, reason="same service/charges")
    if merged:
        db.commit()
        logger.info("[c48-subs] ihopslagna: %s", merged)
    return merged


def tidy_suggestions(db: Session, run: str = "subscriptions") -> dict:
    """Körs efter varje synk: slå ihop dubbletter, avfärda förslag utan
    kortrader tyst, flagga troligen privata, fyll belopp."""
    from app.services.cleanup_log import log

    merged = merge_duplicates(db, run)
    dismissed, private, filled = [], [], []
    for sub in db.query(Subscription).all():
        if sub.review_status not in ("suggested", "confirmed"):
            continue
        cards = card_history(db, sub)
        if sub.review_status == "suggested" and not cards:
            sub.review_status = "dismissed"
            sub.active = False
            sub.note = "no_card_lines"
            dismissed.append(sub.name)
            log(db, run, "subscription_dismissed", None,
                before={"subscription": sub.name, "id": sub.id},
                after={"review_status": "dismissed"},
                reason="no charges on the company card - only receipts")
            continue
        if (sub.review_status == "suggested" and sub.kind is None and cards
                and is_likely_private(sub)):
            sub.kind = "likely_private"
            private.append(sub.name)
        if sub.expected_amount is None and cards:
            typ = typical_amount(db, sub)
            if typ:
                sub.expected_amount, sub.currency = typ
                filled.append(sub.name)
    db.commit()
    out = {"merged": merged, "dismissed": dismissed, "likely_private": private,
           "amount_filled": filled}
    if any(out.values()):
        logger.info("[c48-subs] %s", out)
    return out


def _private_note(sub: Subscription) -> str:
    return PRIVATE_NOTE.format(name=sub.name)


def mark_private(db: Session, sub: Subscription) -> dict:
    """Mikko: privat. Kortrader från leverantören → private_expense (även
    framtida, via apply_private). Inget skickas för attest."""
    if not card_history(db, sub):
        raise SubscriptionError(f"{sub.name} has no charges on the company card - "
                                "nothing to mark as private.")
    before = {"active": sub.active, "review_status": sub.review_status, "kind": sub.kind}
    sub.active = False
    sub.review_status = "private"
    db.commit()
    lines = apply_private(db)
    return {"before": before, "lines": lines}


def undo_private(db: Session, sub: Subscription, before: dict) -> list[str]:
    note = _private_note(sub)
    back = []
    for c in db.query(CardLine).filter(CardLine.note == note).all():
        if c.status == "private_expense":
            c.status = "missing"
            c.set_by = None
            c.note = None
            back.append(c.bill_line_id)
    for k, v in (before or {}).items():
        setattr(sub, k, v)
    db.commit()
    return back


def apply_private(db: Session) -> list[str]:
    """Nya/befintliga kortrader (utan kvitto) från privata prenumerationer
    → private_expense. Bezala-kommentaren 'Private expense - to be deducted
    from salary.' används när ett utkast skapas (aldrig attest)."""
    subs = [s for s in db.query(Subscription).all() if s.review_status == "private"]
    if not subs:
        return []
    marked = []
    for c in db.query(CardLine).filter(CardLine.status == "missing").all():
        if (c.amount or 0) <= 0:
            continue
        sub = next((s for s in subs if matches_text(s, c.description, c.vendor)), None)
        if sub is None:
            continue
        c.status = "private_expense"
        c.set_by = "auto"
        c.note = _private_note(sub)
        marked.append(c.bill_line_id)
    if marked:
        db.commit()
        logger.info("[c48-subs] privata kortrader: %s", marked)
    return marked


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
        from app.services.bezala_write_guard import auto_couple_enabled

        # C50 — automatisk koppling bara med AUTO_COUPLE_ENABLED.
        if not auto_couple_enabled(db):
            return {"coupled": [], "skipped": "AUTO_COUPLE_ENABLED is off"}
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
        if row.amount is None:  # C48: kvitto utan belopp kopplas aldrig automatiskt
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
