"""C45 — kvitton som kommer som SVAR i en tråd.

Exempel (C43-luckan): Airport Recovery Services (Airport LRS, 69 USD
14 apr 2026). Tråden började med en anmälan om borttappad sak; kvittot kom
som tredje meddelande 4 jun 2026 ("This is your request for a receipt of
payment … Order Number 171791, Amount Paid $ 69"). AI:n såg ämnet
"Re: New form submission" + tidigare icke-kvitto från samma avsändare och
avfärdade det. Här är regeln deterministisk: avsändare + nyckelfras ⇒
kvitto, oavsett AI-bedömning.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ReplyReceiptRule:
    sender: str           # exakt adress (gemener)
    phrases: tuple[str, ...]  # minst en måste finnas i texten (gemener)
    vendor: str
    description: str      # engelsk Bezala-beskrivning, {period} ersätts


REPLY_RECEIPT_RULES: tuple[ReplyReceiptRule, ...] = (
    ReplyReceiptRule(
        sender="support@airportrecovery.com",
        phrases=("receipt of payment", "amount paid"),
        vendor="Airport Recovery Services",
        description="Airport Recovery Services - lost item recovery fee{order}",
    ),
)

# Avsändare som ska finnas som html-only-avsändare (kvittot i mailtexten).
REPLY_RECEIPT_SENDERS: tuple[str, ...] = tuple(r.sender for r in REPLY_RECEIPT_RULES)

_AMOUNT_RE = re.compile(
    r"amount paid\s*:?\s*(?P<cur>\$|€|usd|eur)?\s*(?P<num>\d+(?:[.,]\d{1,2})?)",
    re.IGNORECASE,
)
_ORDER_RE = re.compile(r"order number\s*:?\s*(?P<id>[A-Z0-9-]{3,20})", re.IGNORECASE)


@dataclass
class ReplyReceiptMatch:
    rule: ReplyReceiptRule
    amount: float | None
    currency: str | None
    order_id: str | None

    def description_en(self) -> str:
        order = f", order {self.order_id}" if self.order_id else ""
        return self.rule.description.format(order=order)


def match_reply_receipt(
    sender: str | None, text: str | None, html: str | None = None,
) -> ReplyReceiptMatch | None:
    s = (sender or "").lower()
    body = f"{text or ''}\n{_strip_tags(html)}"
    low = body.lower()
    for rule in REPLY_RECEIPT_RULES:
        if rule.sender not in s:
            continue
        if not any(p in low for p in rule.phrases):
            continue
        amount = currency = None
        m = _AMOUNT_RE.search(body)
        if m:
            try:
                amount = float(m.group("num").replace(",", "."))
            except ValueError:
                amount = None
            cur = (m.group("cur") or "").lower()
            currency = {"$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR"}.get(cur)
        om = _ORDER_RE.search(body)
        return ReplyReceiptMatch(
            rule=rule, amount=amount, currency=currency,
            order_id=om.group("id") if om else None,
        )
    return None


def _strip_tags(html: str | None) -> str:
    if not html:
        return ""
    return re.sub(r"<[^>]+>", " ", html)
