"""Claude-baserad analys av kvitton/resedokument.

Skickar hela bilagan (PDF eller HTML) till Claude Sonnet 4 som dokument-input
och ber om strukturerad JSON om innehållet. Analyzern används i pipeline för
att avgöra om ett mail ska sparas eller hoppas över, samt för att sätta ett
beskrivande filnamn och fylla i ProcessedMessage-raden med AI-data.

FAS 8: tar nu emot en valfri lista `examples` med tidigare användar-
rättelser. När den ges bifogas exemplen som few-shot-block efter
SYSTEM_PROMPT så Claude kan dra lärdom av dem.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from anthropic import Anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "Du analyserar bifogade dokument och avgör om de är kvitton, "
    "resedokument, fakturor eller liknande utläggsunderlag. "
    "Du svarar ENDAST med giltig JSON — ingen förklarande text, "
    "inga markdown-codefences, inga kommentarer.\n\n"
    "Schema:\n"
    "{\n"
    '  "is_receipt": bool,\n'
    '  "confidence": 0-100,\n'
    '  "filename": "YYYYMMDD Leverantör Beskrivning.pdf",\n'
    '  "vendor": "Finnair",\n'
    '  "amount": 123.45 eller null,\n'
    '  "currency": "EUR",\n'
    '  "date": "YYYY-MM-DD" eller null,\n'
    '  "category": "Flyg | Hotell | Parkering | Mat | Taxi | Annat",\n'
    '  "summary": "kort beskrivning på svenska"\n'
    "}\n\n"
    "Regler:\n"
    "- Svenska filnamn och sammanfattningar.\n"
    "- Datum i filnamnet: YYYYMMDD. Datum i fältet 'date': YYYY-MM-DD.\n"
    "- Leverantör = företagets/varumärkets namn (t.ex. Finnair, SL, Scandic).\n"
    "- Beskrivning i filnamnet: max 3–5 ord, tydlig och kort.\n"
    "- Inga specialtecken i filnamnet förutom bindestreck, mellanslag och punkt.\n"
    "- Filnamnet slutar alltid på .pdf.\n"
    "- Om belopp, valuta eller datum INTE kan utläsas säkert: sätt null.\n"
    "- Kategorier: 'Flyg', 'Hotell', 'Parkering', 'Mat', 'Taxi', 'Annat'.\n"
    "- confidence speglar hur säker du är på att detta är ett kvitto/utläggsunderlag."
)

_FILENAME_RE = re.compile(r"^[A-Za-z0-9ÅÄÖåäö \-_.]+\.pdf$")
_ALLOWED_CATEGORIES = {"Flyg", "Hotell", "Parkering", "Mat", "Taxi", "Annat"}


@dataclass
class ReceiptAnalysis:
    is_receipt: bool
    confidence: int
    filename: str
    vendor: str | None
    amount: float | None
    currency: str | None
    date: str | None  # YYYY-MM-DD
    category: str | None
    summary: str | None


def _sanitize_filename(name: str) -> str:
    name = (name or "").strip().strip('"').strip("'")
    if name:
        name = name.splitlines()[0]
    name = re.sub(r"[^A-Za-z0-9ÅÄÖåäö \-_.]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    if not name.lower().endswith(".pdf"):
        name = f"{name}.pdf" if name else ""
    return name


def _extract_json(raw: str) -> str:
    """Strippa markdown-codefences som Claude ibland packar JSON i
    trots schema-instruktionen. Returnerar en ren sträng; callern
    kör json.loads. Hanterar alla varianter:

      - ``` {"a": 1} ```
      - ```json {"a": 1} ```  (språktagg)
      - {"a": 1}              (rå JSON, endast whitespace runt)
      - ```json {"a": 1}       (saknad stängande fence)
      - {"a": 1} ```           (saknad öppnande fence)
    """
    s = (raw or "").strip()
    if s.startswith("```"):
        # Ta bort öppnande fence + valfri språktagg (```json, ```JSON, ```)
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
    if s.endswith("```"):
        s = s.rsplit("```", 1)[0]
    return s.strip()


def _fallback_filename(
    received_at: datetime | None,
    sender: str,
    subject: str,
) -> str:
    date_str = (received_at or datetime.utcnow()).strftime("%Y%m%d")
    vendor = (sender or "").split("<")[0].strip().split("@")[0] or "Okand"
    vendor = re.sub(r"[^A-Za-z0-9ÅÄÖåäö \-]", " ", vendor).strip() or "Okand"
    desc = re.sub(r"[^A-Za-z0-9ÅÄÖåäö \-]", " ", subject or "").strip() or "Dokument"
    desc = " ".join(desc.split()[:5]) or "Dokument"
    return _sanitize_filename(f"{date_str} {vendor} {desc}.pdf")


def _build_system_prompt(
    examples: list[dict] | None,
    negative_examples: list[dict] | None = None,
) -> str:
    """Lägg till few-shot-exempel + negativa exempel (FAS 8.1) efter
    base-prompten. Defensivt — om formateringen kraschar returnerar vi
    base-prompten ensamt."""
    if not examples and not negative_examples:
        return SYSTEM_PROMPT
    try:
        # Lazy import för att undvika cirkulär import (feedback ↔ analyzer)
        from app.services.feedback import (
            format_examples_for_prompt,
            format_not_receipt_examples_for_prompt,
        )
        out = SYSTEM_PROMPT
        positive = format_examples_for_prompt(examples or [])
        if positive:
            out = out + "\n" + positive
        negative = format_not_receipt_examples_for_prompt(
            negative_examples or []
        )
        if negative:
            out = out + "\n" + negative
        return out
    except Exception:  # noqa: BLE001
        logger.exception("Few-shot prompt-bygge misslyckades")
    return SYSTEM_PROMPT


class ReceiptAnalyzer:
    """Analyserar bilagor och returnerar ReceiptAnalysis.

    Om ANTHROPIC_API_KEY saknas rapporteras AnalyzerError (kallaren ska falla
    tillbaka på icke-AI-flödet).
    """

    def __init__(self) -> None:
        settings = get_settings()
        self._model = settings.claude_model
        if not settings.anthropic_api_key:
            self._client: Anthropic | None = None
        else:
            self._client = Anthropic(api_key=settings.anthropic_api_key)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def analyze(
        self,
        *,
        attachment_bytes: bytes,
        mime_type: str,
        original_filename: str,
        sender: str,
        subject: str,
        snippet: str,
        received_at: datetime | None,
        examples: list[dict] | None = None,
        negative_examples: list[dict] | None = None,
    ) -> ReceiptAnalysis:
        if not self._client:
            raise AnalyzerError("ANTHROPIC_API_KEY saknas")

        media_type = "application/pdf" if "pdf" in (mime_type or "").lower() else "application/pdf"
        data_b64 = base64.b64encode(attachment_bytes).decode("ascii")
        context_date = (received_at or datetime.utcnow()).strftime("%Y-%m-%d")
        user_text = (
            f"Mailets mottagningsdatum: {context_date}\n"
            f"Avsändare: {sender}\n"
            f"Ämne: {subject}\n"
            f"Snippet: {(snippet or '')[:500]}\n"
            f"Bifogad fil (original): {original_filename}\n\n"
            "Analysera dokumentet och svara med JSON enligt schemat."
        )

        system_prompt = _build_system_prompt(examples, negative_examples)
        if examples:
            logger.info(
                "AI-analys: bifogar %d few-shot-exempel (sender=%r)",
                len(examples), sender,
            )
        if negative_examples:
            logger.info(
                "AI-analys: bifogar %d not_a_receipt-exempel (sender=%r)",
                len(negative_examples), sender,
            )

        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=600,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "document",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": data_b64,
                                },
                            },
                            {"type": "text", "text": user_text},
                        ],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise AnalyzerError(f"Claude API-fel: {exc}") from exc

        raw_text = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )
        try:
            payload = json.loads(_extract_json(raw_text))
        except json.JSONDecodeError as exc:
            raise AnalyzerError(
                f"Kunde inte parsa Claude-JSON: {exc} (raw: {raw_text[:500]!r})"
            ) from exc

        analysis = _normalize(
            payload, received_at=received_at, sender=sender, subject=subject,
        )

        # Fix 4: tydlig debug-logg när AI missar amount/date — ofta ett tecken
        # på en bild-PDF (OCR behövs) eller en kvitto-PDF där belopp ligger
        # längst ner och dokumentet trunkerades. Loggar PDF-storlek + första
        # raden av Claude:s payload så vi kan diagnosa.
        if analysis.is_receipt and analysis.amount is None:
            pdf_size = len(attachment_bytes)
            looks_text_pdf = b"/Font" in attachment_bytes[:50_000]
            logger.warning(
                "AI missade amount för %r (sender=%r confidence=%d%% "
                "pdf_size=%d text_pdf_indikator=%s) — claude_payload=%s",
                original_filename, sender, analysis.confidence, pdf_size,
                looks_text_pdf, json.dumps(payload, ensure_ascii=False)[:600],
            )

        return analysis


class AnalyzerError(RuntimeError):
    pass


def _normalize(
    payload: dict,
    *,
    received_at: datetime | None,
    sender: str,
    subject: str,
) -> ReceiptAnalysis:
    is_receipt = bool(payload.get("is_receipt"))
    confidence_raw = payload.get("confidence")
    try:
        confidence = int(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    filename = _sanitize_filename(str(payload.get("filename") or ""))
    if not _FILENAME_RE.match(filename):
        filename = _fallback_filename(received_at, sender, subject)

    vendor = payload.get("vendor")
    vendor = str(vendor).strip() if vendor else None

    amount_raw = payload.get("amount")
    try:
        amount = float(amount_raw) if amount_raw is not None else None
    except (TypeError, ValueError):
        amount = None

    currency = payload.get("currency")
    currency = str(currency).strip()[:16] if currency else None

    date = payload.get("date")
    if date and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(date)):
        date = None
    else:
        date = str(date) if date else None

    category = payload.get("category")
    if category and category not in _ALLOWED_CATEGORIES:
        category = "Annat"
    category = str(category) if category else None

    summary = payload.get("summary")
    summary = str(summary).strip() if summary else None
    if summary and len(summary) > 1000:
        summary = summary[:1000]

    return ReceiptAnalysis(
        is_receipt=is_receipt,
        confidence=confidence,
        filename=filename,
        vendor=vendor,
        amount=amount,
        currency=currency,
        date=date,
        category=category,
        summary=summary,
    )


def build_fallback_filename(
    received_at: datetime | None, sender: str, subject: str
) -> str:
    return _fallback_filename(received_at, sender, subject)
