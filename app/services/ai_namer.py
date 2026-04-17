"""Claude-baserad filnamngivning.

Claude får e-postens avsändare, ämne, snippet och filnamn — och returnerar ett
kort, beskrivande filnamn på formatet "YYYYMMDD Leverantör Beskrivning.pdf".
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from anthropic import Anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = (
    "Du namnger kvitton och resedokument. Du får ett e-mail (avsändare, ämne, "
    "snippet) och ett bifogat filnamn. Svara med EN rad — ENDAST filnamnet, "
    "inga förklaringar, inga citattecken. Format: 'YYYYMMDD Leverantör "
    "Beskrivning.pdf'. Exempel:\n"
    "  20260401 Finnair HEL-CPH.pdf\n"
    "  20260315 SL Månadskort.pdf\n"
    "  20260228 Scandic Hotell Stockholm.pdf\n"
    "Regler:\n"
    "- Använd mailets datum för YYYYMMDD.\n"
    "- Leverantör = företag/varumärke (ex 'Finnair', 'SL', 'Scandic').\n"
    "- Beskrivning: max 3-5 ord, tydlig och kort.\n"
    "- Inga specialtecken förutom bindestreck, mellanslag och punkt.\n"
    "- Sluta alltid på .pdf"
)

_FILENAME_RE = re.compile(r"^[A-Za-z0-9ÅÄÖåäö \-_.]+\.pdf$")


def _sanitize(name: str) -> str:
    name = name.strip().strip('"').strip("'")
    # ta bort eventuella radbrytningar och extra text
    name = name.splitlines()[0] if name else name
    # ersätt otillåtna tecken
    name = re.sub(r"[^A-Za-z0-9ÅÄÖåäö \-_.]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf"
    return name


def _fallback_name(
    received_at: datetime | None, sender: str, subject: str, original_filename: str
) -> str:
    date_str = (received_at or datetime.utcnow()).strftime("%Y%m%d")
    vendor = (sender.split("<")[0].strip() or "Okand").split("@")[0]
    vendor = re.sub(r"[^A-Za-z0-9ÅÄÖåäö \-]", " ", vendor).strip() or "Okand"
    desc = re.sub(r"[^A-Za-z0-9ÅÄÖåäö \-]", " ", subject).strip() or "Dokument"
    desc = " ".join(desc.split()[:5]) or "Dokument"
    base = f"{date_str} {vendor} {desc}.pdf"
    return _sanitize(base)


class FileNamer:
    def __init__(self) -> None:
        settings = get_settings()
        self._enabled = bool(settings.anthropic_api_key)
        self._client: Anthropic | None = (
            Anthropic(api_key=settings.anthropic_api_key) if self._enabled else None
        )

    def name_for(
        self,
        *,
        sender: str,
        subject: str,
        snippet: str,
        received_at: datetime | None,
        original_filename: str,
    ) -> str:
        fallback = _fallback_name(received_at, sender, subject, original_filename)
        if not self._enabled or not self._client:
            return fallback

        date_str = (received_at or datetime.utcnow()).strftime("%Y-%m-%d")
        user_prompt = (
            f"Mailets datum: {date_str}\n"
            f"Avsändare: {sender}\n"
            f"Ämne: {subject}\n"
            f"Snippet: {snippet[:500]}\n"
            f"Bifogad fil: {original_filename}\n\n"
            "Ge mig filnamnet."
        )
        try:
            resp = self._client.messages.create(
                model=MODEL,
                max_tokens=120,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = "".join(
                block.text for block in resp.content if getattr(block, "type", "") == "text"
            )
            candidate = _sanitize(text)
            if _FILENAME_RE.match(candidate):
                return candidate
            logger.warning("Claude returnerade ogiltigt filnamn %r, använder fallback", text)
            return fallback
        except Exception as exc:  # noqa: BLE001
            logger.exception("Claude-namngivning misslyckades: %s", exc)
            return fallback
