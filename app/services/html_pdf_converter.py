"""HTML → PDF konvertering för mail där själva mailet är kvittot.

Används av pipeline när ett mail saknar PDF-bilaga men avsändaren INTE
ligger i link_fetch_senders — t.ex. Moovy och Skånetrafiken som lägger
hela kvittot i mail-bodyn. Konverteras med weasyprint.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class HtmlToPdfError(RuntimeError):
    """Konverteringen misslyckades — pipeline loggar 'html_pdf_failed'."""


_BASE_CSS = """
@page { size: A4; margin: 18mm; }
body { font-family: 'Helvetica', 'Arial', sans-serif; font-size: 11pt;
       line-height: 1.4; color: #111; }
img { max-width: 100%; }
table { border-collapse: collapse; }
pre, code { font-family: 'Courier New', monospace; white-space: pre-wrap; }
"""


def _wrap_plain_text(text: str) -> str:
    safe = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    return f"<html><body><pre>{safe}</pre></body></html>"


def _html_diagnostics(source: str) -> dict:
    """Snabb struktur-diagnostik för Railway-loggen: ger dev en känsla
    för vad weasyprint fick in utan att klistra in hela HTML:en."""
    low = source.lower()
    return {
        "len": len(source),
        "head": source[:400],
        "link_rel_stylesheet": low.count("<link"),
        "style_tags": low.count("<style"),
        "img_tags": low.count("<img"),
        "external_img_https": low.count('src="https://'),
        "external_img_http": low.count('src="http://'),
        "script_tags": low.count("<script"),
        "svg_tags": low.count("<svg"),
        "has_doctype": "<!doctype" in low,
    }


def html_to_pdf(html: str | None, *, plain_text_fallback: str | None = None) -> bytes:
    """Returnerar PDF-bytes. Föredrar HTML; faller tillbaka på plain text.

    Höjer HtmlToPdfError om weasyprint saknas eller konverteringen kraschar.
    """
    source = (html or "").strip()
    used_fallback = False
    if not source and plain_text_fallback:
        source = _wrap_plain_text(plain_text_fallback)
        used_fallback = True
    if not source:
        raise HtmlToPdfError("Tomt mail — varken HTML eller text att konvertera.")

    try:
        from weasyprint import CSS, HTML  # importera lazily så testerna kan mocka
    except ImportError as exc:
        raise HtmlToPdfError(
            "weasyprint är inte installerat — kan inte konvertera HTML till PDF."
        ) from exc

    try:
        pdf = HTML(string=source).write_pdf(stylesheets=[CSS(string=_BASE_CSS)])
    except Exception as exc:  # noqa: BLE001 — weasyprint kastar olika typer
        diag = _html_diagnostics(source)
        logger.exception(
            "HTML→PDF-konvertering misslyckades — fallback=%s diag=%s exc_type=%s",
            used_fallback, diag, type(exc).__name__,
        )
        raise HtmlToPdfError(
            f"HTML→PDF kraschade ({type(exc).__name__}): {exc}"
        ) from exc

    if not pdf or not pdf.startswith(b"%PDF"):
        raise HtmlToPdfError("HTML→PDF returnerade inte giltig PDF.")
    return pdf
