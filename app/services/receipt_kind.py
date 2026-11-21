"""C43 — känn igen kvitton som INTE är vanliga köp.

Just nu: Finnair/Amadeus-dokument för ombokning ("change fee").

Bakgrund (attesten 2026-10): Finnair skickar efter en ombokning ett nytt
"YOUR ETICKET RECEIPT" från eticket@amadeus.com som ser ut exakt som en
flygbiljett — "Fare: EUR 595.00" — men där det faktiskt debiterade
beloppet är "Grand Total: EUR 0.02" och skatterna är märkta "PD"
(previously paid). Samtidigt kommer "Bekräftelse på din bokningsändring"
från noreply@finnair.com med "Prisskillnad 0.02 EUR / Ändringsavgift
0 EUR / Totalpris 0.02 EUR". Boten tolkade det som resans flygkvitto och
attesten (Riikka) fick fråga vad det var.

Regel: sådana dokument får `kind = "change_fee"` och en engelsk
Bezala-beskrivning "Finnair change fee – flight HEL–GOT 13 Oct 2026".
De räknas inte som resans flygbiljett men får gärna kopplas till resan.

Pure functions — inga sidoeffekter, lätt att testa.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

KIND_CHANGE_FEE = "change_fee"

_AIRLINE_SENDERS = ("finnair", "amadeus")

# Ord som bara förekommer i ändrings-/avgiftsdokument. Svenska, finska,
# engelska (Finnair skickar på bokningens språk).
_CHANGE_KEYWORDS = (
    "change fee",
    "changefee",
    "booking change",
    "change confirmation",
    "price difference",
    "fare difference",
    "ändringsavgift",
    "bokningsändring",
    "prisskillnad",
    "muutosmaksu",
    "varauksen muutos",
    "varausmuutos",
    "hinnanero",
)

# Under detta belopp är ett "flygkvitto" i praktiken alltid en avgift eller
# prisskillnad, aldrig en biljett.
TINY_AMOUNT = 5.0

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH_NAMES = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

_AMOUNT = r"(\d+(?:[.,]\d{1,2})?)"
_GRAND_TOTAL_RE = re.compile(
    r"grand\s+total\s*[:|]?\s*\|?\s*:?\s*([A-Z]{3})\s*" + _AMOUNT, re.I,
)
_FARE_RE = re.compile(r"\bfare\s*[:|]?\s*\|?\s*:?\s*([A-Z]{3})\s*" + _AMOUNT, re.I)
_PD_TAX_RE = re.compile(r"\b[A-Z]{3}\s+PD\s+\d", re.I)
_FARE_CALC_RE = re.compile(
    r"fare\s+calculation\s*[:|]?\s*\|?\s*:?\s*([A-Z]{3})\s+[A-Z0-9]{2}\s+([A-Z]{3})",
    re.I,
)
# "07:15 HEL | | 07:45 GOT" (Finnair ändringsbekräftelse)
_TIME_PAIR_RE = re.compile(r"\d\d:\d\d\s+([A-Z]{3})\s*[|\s]*\d\d:\d\d\s+([A-Z]{3})")
# "HEL-GOT", "HEL–GOT"
_DASH_PAIR_RE = re.compile(r"\b([A-Z]{3})\s*[-–]\s*([A-Z]{3})\b")
_DEP_DATE_RE = re.compile(r"DEP:\s*(\d{1,2})([A-Z]{3})(\d{4})", re.I)
_ISO_DATE_RE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")

# Tre-bokstavsord som INTE är flygplatser (valutor m.m.) — skydd för
# _DASH_PAIR_RE / _TIME_PAIR_RE.
_NOT_AIRPORTS = {"EUR", "SEK", "NOK", "DKK", "USD", "GBP", "PDF", "ADT", "VAT"}


@dataclass(frozen=True)
class ChangeFeeInfo:
    origin: str | None
    destination: str | None
    flight_date: str | None  # YYYY-MM-DD
    amount: float | None
    currency: str | None
    reason: str

    def description_en(self) -> str:
        """'Finnair change fee - flight HEL-GOT 13 Oct 2026'."""
        parts = ["Finnair change fee"]
        flight = "flight"
        if self.origin and self.destination:
            flight += f" {self.origin}-{self.destination}"
        if self.flight_date:
            try:
                d = datetime.strptime(self.flight_date, "%Y-%m-%d")
                flight += f" {d.day} {_MONTH_NAMES[d.month - 1]} {d.year}"
            except ValueError:
                pass
        return f"{parts[0]} - {flight}"


def _to_float(raw: str) -> float | None:
    try:
        return float(raw.replace(",", "."))
    except (TypeError, ValueError):
        return None


def is_airline_sender(sender: str | None) -> bool:
    s = (sender or "").lower()
    return any(a in s for a in _AIRLINE_SENDERS)


def _route(text: str) -> tuple[str | None, str | None]:
    for rx in (_FARE_CALC_RE, _TIME_PAIR_RE, _DASH_PAIR_RE):
        for m in rx.finditer(text):
            a, b = m.group(1).upper(), m.group(2).upper()
            if a in _NOT_AIRPORTS or b in _NOT_AIRPORTS or a == b:
                continue
            return a, b
    return None, None


def _flight_date(subject: str, text: str) -> str | None:
    for src in (subject or "", text):
        m = _DEP_DATE_RE.search(src)
        if m:
            month = _MONTHS.get(m.group(2).upper())
            if month:
                return f"{int(m.group(3)):04d}-{month:02d}-{int(m.group(1)):02d}"
    # "Utresa 2026-10-13 …" i Finnairs ändringsbekräftelse
    m = re.search(r"(?:utresa|outbound|meno|departure)\D{0,20}(20\d\d-\d\d-\d\d)", text, re.I)
    if m:
        return m.group(1)
    return None


def detect_change_fee(
    *,
    sender: str | None,
    subject: str | None,
    text: str | None,
    amount: float | None = None,
    currency: str | None = None,
) -> ChangeFeeInfo | None:
    """Returnera ChangeFeeInfo om dokumentet är en Finnair/Amadeus-
    ombokning/avgift, annars None.

    text = mailets text + ev. PDF-text (det som finns). amount/currency =
    AI:ns tolkning (kan vara Fare istället för Grand Total — därför läser
    vi Grand Total själva när den finns).
    """
    if not is_airline_sender(sender):
        return None
    body = text or ""
    haystack = f"{subject or ''}\n{body}"
    low = haystack.lower()

    reason: str | None = None
    paid_amount = amount
    paid_currency = currency

    gt = _GRAND_TOTAL_RE.search(body)
    fare = _FARE_RE.search(body)
    if gt:
        paid_currency = gt.group(1).upper()
        paid_amount = _to_float(gt.group(2))
    # Amadeus-reissue: Grand Total långt under Fare, eller skatter "PD"
    # (redan betalda på originalbiljetten) och Grand Total < Fare.
    if gt and fare:
        gt_v = _to_float(gt.group(2)) or 0.0
        fare_v = _to_float(fare.group(2)) or 0.0
        if fare_v > 0 and gt_v < fare_v * 0.5:
            reason = "grand_total_below_fare"
        elif fare_v > 0 and gt_v < fare_v and _PD_TAX_RE.search(body):
            reason = "reissue_previously_paid_taxes"

    if reason is None:
        for kw in _CHANGE_KEYWORDS:
            if kw in low:
                reason = f"keyword:{kw}"
                break

    if reason is None and paid_amount is not None and 0 <= abs(paid_amount) < TINY_AMOUNT:
        # Ett flygdokument från Finnair/Amadeus med ett par cent är aldrig
        # en biljett. Kräv ändå att det ser ut som ett flygdokument.
        if "eticket" in low or "e-ticket" in low or "booking" in low or "bokning" in low:
            reason = "tiny_amount"

    if reason is None:
        return None

    origin, destination = _route(haystack)
    return ChangeFeeInfo(
        origin=origin,
        destination=destination,
        flight_date=_flight_date(subject or "", body),
        amount=paid_amount,
        currency=paid_currency,
        reason=reason,
    )


def pdf_text(data: bytes | None, *, max_pages: int = 4) -> str:
    """Bästa-försök-text ur en PDF (pypdf). Tom sträng vid fel."""
    if not data or not data.startswith(b"%PDF"):
        return ""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(
            (page.extract_text() or "") for page in reader.pages[:max_pages]
        )
    except Exception:  # noqa: BLE001
        logger.info("pdf_text: kunde inte läsa PDF-text", exc_info=True)
        return ""


def classify_existing_row(
    *,
    sender: str | None,
    subject: str | None,
    summary: str | None,
    amount: float | None,
) -> str | None:
    """Backfill för redan sparade rader (där mailtexten inte finns kvar):
    Finnair/Amadeus + (ändringsord i ämne/sammanfattning ELLER belopp
    under TINY_AMOUNT) → change_fee."""
    if not is_airline_sender(sender):
        return None
    info = detect_change_fee(
        sender=sender,
        subject=subject,
        text=f"{summary or ''}\neticket booking",
        amount=amount,
    )
    return KIND_CHANGE_FEE if info else None
