"""C47 Del 6 — två extra dubblettregler (utöver C44:s biljett/PNR-regel).

1. Finnair "Bokningsbekräftelse/Booking confirmation" med samma bokning
   och belopp som ett e-ticket-kvitto → bekräftelsen är dubblett av
   e-ticket-kvittot (datumen skiljer ofta: bokningsdag vs resdag).
2. Hotell: faktura + betalningsbekräftelse från samma hotell (samma
   avsändardomän), samma belopp, valuta och datum → den ena är dubblett,
   fakturan behålls. Gäller även utan bokningsnummer.

Ett kvitto som redan är kopplat i Bezala görs aldrig till dubblett av ett
okopplat — då behålls det kopplade.
"""

from __future__ import annotations

import re
from email.utils import parseaddr

CONFIRMATION_RE = re.compile(
    r"bokningsbekr[äa]ftelse|booking confirmation|varausvahvistus|reservation confirmation",
    re.IGNORECASE,
)
ETICKET_RE = re.compile(
    r"e-?ticket|electronic ticket|e-biljett|s[äa]hk[öo]inen lippu|ticket receipt|kvitto",
    re.IGNORECASE,
)
INVOICE_RE = re.compile(r"faktura|invoice|lasku|rechnung|kvitto|receipt", re.IGNORECASE)
PAYMENT_RE = re.compile(
    r"betalningsbekr[äa]ftelse|payment confirmation|maksuvahvistus|payment received|"
    r"betalning mottagen|bekr[äa]ftelse",
    re.IGNORECASE,
)


def _text(m) -> str:
    return f"{m.subject or ''} {m.file_name or ''}"


def _amount_key(m):
    if m.amount is None:
        return None
    return (round(float(m.amount), 2), (m.currency or "").upper())


def _domain(m) -> str:
    addr = parseaddr(m.sender or "")[1].lower()
    return addr.rpartition("@")[2]


def _keep_coupled(keep, dupe):
    """Byt plats om bara 'dupe' är kopplad i Bezala."""
    if dupe.bezala_transaction_id and not keep.bezala_transaction_id:
        return dupe, keep
    return keep, dupe


def finnair_pairs(rows) -> list[tuple]:
    fin = [m for m in rows if "finnair" in (m.vendor or "").lower()
           or "finnair" in (m.sender or "").lower()]
    confs = [m for m in fin if CONFIRMATION_RE.search(_text(m))]
    tickets = [m for m in fin if m not in confs and ETICKET_RE.search(_text(m))]
    out = []
    for c in confs:
        for t in tickets:
            if _amount_key(c) is None or _amount_key(c) != _amount_key(t):
                continue
            same_booking = (c.booking_ref and t.booking_ref and c.booking_ref == t.booking_ref)
            no_refs_same_day = (not c.booking_ref or not t.booking_ref) and (
                (c.receipt_date or "")[:10] == (t.receipt_date or "")[:10] != "")
            if same_booking or no_refs_same_day:
                out.append(_keep_coupled(t, c))
                break
    return out


def _is_hotel(m) -> bool:
    return (m.category or "").lower() in ("hotell", "hotel") or "hotel" in (m.vendor or "").lower()


def hotel_pairs(rows) -> list[tuple]:
    groups: dict[tuple, list] = {}
    for m in rows:
        if not _is_hotel(m) or _amount_key(m) is None or not m.receipt_date or not _domain(m):
            continue
        groups.setdefault((_domain(m), _amount_key(m), m.receipt_date[:10]), []).append(m)
    out = []
    for members in groups.values():
        if len(members) != 2:
            continue  # tre+ likadana = troligen flera nätter/rum, rör inte
        a, b = sorted(members, key=lambda m: m.id or 0)
        if a.message_id == b.message_id:
            continue
        a_inv = bool(INVOICE_RE.search(_text(a))) and not PAYMENT_RE.search(_text(a))
        b_inv = bool(INVOICE_RE.search(_text(b))) and not PAYMENT_RE.search(_text(b))
        keep, dupe = (b, a) if b_inv and not a_inv else (a, b)
        out.append(_keep_coupled(keep, dupe))
    return out


def extra_duplicates(rows) -> dict[int, str]:
    """{dubblettens id: behållet kvittos message_id}."""
    out: dict[int, str] = {}
    for keep, dupe in finnair_pairs(rows) + hotel_pairs(rows):
        if keep.duplicate_of:
            continue
        out.setdefault(dupe.id, keep.message_id)
    return out
