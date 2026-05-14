"""Backfill `ai_description_en` för ProcessedMessage-rader som processades
innan kolumnen fanns (pre-PR30) eller där AI:n inte producerade en engelsk
beskrivning.

Tar metadata från raden (vendor, summary, receipt_date, category, amount,
currency) och ber Claude bygga en kort engelsk Bezala-beskrivning på
formatet "vendor + place/purpose + date".

Designnoteringar:
- Endast text-prompt — vi har inte längre originalbilagan tillgänglig
  för dessa rader, men summary + vendor räcker för att producera en
  meningsfull beskrivning.
- Skippar rader som redan har ai_description_en eller är soft-deleted.
- Rate-limit: 200ms sleep mellan Claude-anrop (sequentiellt).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Iterable

from anthropic import Anthropic

from app.config import get_settings
from app.models import ProcessedMessage

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = (
    "You translate Swedish receipt summaries into concise English "
    "descriptions for Finnish accounting (Bezala). Output ONLY the "
    "description text — no quotes, no preamble, no trailing punctuation."
)


PROMPT_TEMPLATE = """Translate this receipt to a concise English description for Finnish accounting (Bezala).

Vendor: {vendor}
Date: {receipt_date}
Category: {category}
Amount: {amount} {currency}
Swedish summary: {summary}

Required format: "<vendor + location/purpose>, <date>"

Examples:
- "Parking at Helsinki-Vantaa Airport P2, 22-24 April 2026"
- "Flight Helsinki-Stockholm round trip, 30 April 2026"
- "Anthropic API credits, 14 April 2026"
- "Airport Express train Stockholm-Arlanda, 16 April 2026"

Keep under 80 characters. Output the description text only, no quotes, no preamble."""


# Bezala description-fältet sparas som VARCHAR(500); promptens 80 är ett
# stilkrav men vi tar inte i från (truncating at 500 är hård gräns).
_MAX_DESC_LEN = 500


@dataclass
class BackfillResultItem:
    id: int
    old: str | None
    new: str | None
    status: str  # ok | skipped | failed
    error: str | None = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "old": self.old, "new": self.new, "status": self.status}
        if self.error is not None:
            d["error"] = self.error
        return d


def _sanitize(text: str) -> str | None:
    if not text:
        return None
    cleaned = text.strip().strip('"').strip("'")
    if not cleaned:
        return None
    # Endast första raden — Claude returnerar normalt en rad, men skydda mot
    # förklaringar.
    cleaned = cleaned.splitlines()[0].strip()
    cleaned = cleaned.rstrip(".").strip()
    if not cleaned:
        return None
    if len(cleaned) > _MAX_DESC_LEN:
        cleaned = cleaned[:_MAX_DESC_LEN].rstrip()
    return cleaned or None


def build_prompt(row: ProcessedMessage) -> str:
    return PROMPT_TEMPLATE.format(
        vendor=row.vendor or "Unknown",
        receipt_date=row.receipt_date or "Unknown",
        category=row.category or "Annat",
        amount=row.amount if row.amount is not None else "?",
        currency=row.currency or "",
        summary=row.summary or "",
    )


class DescriptionBackfiller:
    """Anropar Claude för att producera ai_description_en utifrån rad-metadata.

    Är inte enabled (no-op) om ANTHROPIC_API_KEY saknas.
    """

    def __init__(self, client: Anthropic | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self._model = model or settings.claude_model
        if client is not None:
            self._client = client
        elif settings.anthropic_api_key:
            self._client = Anthropic(api_key=settings.anthropic_api_key)
        else:
            self._client = None

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def describe(self, row: ProcessedMessage) -> str:
        if self._client is None:
            raise RuntimeError("ANTHROPIC_API_KEY saknas")
        prompt = build_prompt(row)
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=200,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        cleaned = _sanitize(text)
        if not cleaned:
            raise RuntimeError(f"Claude returnerade tomt svar (raw={text!r})")
        return cleaned


def backfill_rows(
    rows: Iterable[ProcessedMessage],
    backfiller: DescriptionBackfiller,
    *,
    sleep_seconds: float = 0.2,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[BackfillResultItem]:
    """Kör backfill sekveniellt över raderna.

    - Skippar rader som redan har ai_description_en.
    - Anropar Claude för övriga, uppdaterar row.ai_description_en in-place.
    - Sover `sleep_seconds` mellan rader (skydd mot rate-limits).
    - Returnerar en lista BackfillResultItem för rapportering.
    - Kallaren ansvarar för db.commit().
    """
    results: list[BackfillResultItem] = []
    rows = list(rows)
    for idx, row in enumerate(rows):
        if row.ai_description_en:
            results.append(
                BackfillResultItem(
                    id=row.id,
                    old=row.ai_description_en,
                    new=row.ai_description_en,
                    status="skipped",
                )
            )
            continue
        old = row.ai_description_en
        try:
            new_desc = backfiller.describe(row)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Backfill misslyckades för id=%s vendor=%r: %s",
                row.id, row.vendor, exc,
            )
            results.append(
                BackfillResultItem(
                    id=row.id, old=old, new=None,
                    status="failed", error=str(exc),
                )
            )
        else:
            row.ai_description_en = new_desc
            results.append(
                BackfillResultItem(
                    id=row.id, old=old, new=new_desc, status="ok",
                )
            )
        # sleep endast mellan rader vi faktiskt anropade Claude för
        if idx < len(rows) - 1 and sleep_seconds > 0:
            sleep_fn(sleep_seconds)
    return results
