"""C45 — Apple-kvitton (App Store / iCloud+ / Apple One / Apple Music …).

Apple skickar kvittot som HTML i mailet ("Your receipt from Apple." /
"Kuittisi Applelta" / "Ditt kvitto från Apple") från
`no_reply@email.apple.com` — ingen PDF-bilaga. Pipelinen gör HTML→PDF som
för andra html-kvitton; den här modulen tolkar texten deterministiskt så
Bezala-beskrivningen blir engelsk och förutsägbar:

    "Apple iCloud+ subscription - Nov 2026"

Hårdvara/AppleCare/okänt köp flaggas (`needs_review=True`) — vi gissar
aldrig bokföringskonto.
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from datetime import datetime

APPLE_SENDER_DOMAINS = ("email.apple.com", "apple.com")

# (nyckelord i gemener, engelskt tjänstenamn, är prenumeration)
_SERVICES: tuple[tuple[str, str, bool], ...] = (
    ("apple one", "Apple One", True),
    ("icloud+", "iCloud+", True),
    ("icloud", "iCloud+", True),
    ("apple music", "Apple Music", True),
    ("apple tv", "Apple TV+", True),
    ("apple arcade", "Apple Arcade", True),
    ("apple news", "Apple News+", True),
    ("fitness+", "Apple Fitness+", True),
)

_HARDWARE_WORDS = (
    "macbook", "imac", "mac mini", "mac studio", "iphone", "ipad",
    "airpods", "apple watch", "apple pencil", "magic keyboard",
    "magic mouse", "studio display", "applecare",
)

_SUBSCRIPTION_HINTS = (
    "renews", "monthly", "yearly", "annual", "subscription",
    "uusiutuu", "kuukausi", "tilaus", "förnyas", "månad", "prenumeration",
)

_CURRENCY_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP", "kr": "SEK"}

_TOTAL_RE = re.compile(
    r"(?:^|\n|\s)(?:total|yhteensä|totalt|summa|grand total)\s*:?\s*"
    r"(?P<pre>€|\$|£|eur|usd|sek|kr)?\s*"
    r"(?P<num>\d{1,3}(?:[  .,]\d{3})*(?:[.,]\d{2})|\d+(?:[.,]\d{2})?)"
    r"\s*(?P<post>€|\$|£|eur|usd|sek|kr)?",
    re.IGNORECASE,
)

_ORDER_RE = re.compile(
    r"(?:order id|tilaustunnus|order-id|ordernummer)\s*:?\s*(?P<id>[A-Z0-9]{8,14})",
    re.IGNORECASE,
)


@dataclass
class AppleReceipt:
    service: str | None
    is_subscription: bool
    is_hardware: bool
    amount: float | None
    currency: str | None
    order_id: str | None
    period: str  # "Nov 2026"

    @property
    def needs_review(self) -> bool:
        """Hårdvara/AppleCare eller okänt köp → flagga, gissa inte konto."""
        return self.is_hardware or self.service is None

    def description_en(self) -> str:
        if self.is_hardware:
            return f"Apple hardware/AppleCare purchase - {self.period}"
        if self.service and self.is_subscription:
            name = self.service if self.service.startswith("Apple") else f"Apple {self.service}"
            return f"{name} subscription - {self.period}"
        if self.service:
            return f"Apple {self.service} - {self.period}"
        return f"Apple App Store purchase - {self.period}"


def is_apple_sender(sender: str | None) -> bool:
    s = (sender or "").lower()
    return any(f"@{d}" in s for d in APPLE_SENDER_DOMAINS)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|td|li|h\d)>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_lib.unescape(t)
    t = re.sub(r"[ \t ]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()


def _parse_amount(num: str) -> float | None:
    s = num.replace(" ", "").replace(" ", "")
    if "," in s and "." in s:
        # 1.234,56 eller 1,234.56 — sista separatorn är decimal
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _currency(pre: str | None, post: str | None) -> str | None:
    for tok in (pre, post):
        if not tok:
            continue
        t = tok.strip().lower()
        if t in ("eur", "usd", "sek", "gbp"):
            return t.upper()
        sym = _CURRENCY_SYMBOLS.get(tok.strip()) or _CURRENCY_SYMBOLS.get(t)
        if sym:
            return sym
    return None


def parse_apple_receipt(
    *, text: str | None, html: str | None, received_at: datetime | None,
) -> AppleReceipt:
    body = (text or "").strip() or html_to_text(html)
    low = body.lower()

    service: str | None = None
    for key, name, _sub in _SERVICES:
        if key in low:
            service = name
            break
    is_hardware = any(w in low for w in _HARDWARE_WORDS) and service is None
    is_subscription = service is not None or any(h in low for h in _SUBSCRIPTION_HINTS)

    amount = currency = None
    matches = list(_TOTAL_RE.finditer(body))
    if matches:
        m = matches[-1]  # sista "Total" = slutsumman (efter ev. delsummor)
        amount = _parse_amount(m.group("num"))
        currency = _currency(m.group("pre"), m.group("post"))

    om = _ORDER_RE.search(body)
    when = received_at or datetime.utcnow()
    return AppleReceipt(
        service=service,
        is_subscription=is_subscription,
        is_hardware=is_hardware,
        amount=amount,
        currency=currency,
        order_id=om.group("id").upper() if om else None,
        period=when.strftime("%b %Y"),
    )
