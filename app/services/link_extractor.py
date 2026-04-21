"""Extrahera kvitto-länkar från mail-body.

Enkel URL-regex + keyword-filter. När avsändaren finns i
AppSettings.link_fetch_senders sparar vi första matchande URL som
pending_link och låter användaren trigga hämtningen manuellt via
POST /api/messages/{id}/fetch-pdf.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

# Fångar http(s)-URL:er. Avsiktligt tolerant — täcker
# Sendgrid-click-länkar, signed-URLs, query-strängar, etc.
_URL_RE = re.compile(r"https?://[^\s<>\"'\\]+", re.IGNORECASE)

# URL som innehåller (path, host eller nearby text) något av dessa
# nyckelord räknas som potentiell kvitto-länk. Lowercase-jämförelse.
KEYWORDS: tuple[str, ...] = (
    "receipt",
    "kvitto",
    "invoice",
    "faktura",
    "download",
    "pdf",
    "click",
    "sendgrid",
    "ct.sendgrid",
)


def _unquote_html(raw: str) -> str:
    return html.unescape(raw or "")


def _extract_urls(source: str) -> list[str]:
    if not source:
        return []
    text = _unquote_html(source)
    seen: set[str] = set()
    results: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;)>]")
        if url in seen:
            continue
        seen.add(url)
        results.append(url)
    return results


def _url_matches_keyword(url: str) -> bool:
    lower = url.lower()
    return any(kw in lower for kw in KEYWORDS)


def extract_receipt_link(body_text: str | None, body_html: str | None) -> str | None:
    """Returnera första URL som matchar KEYWORDS. Prioriterar text-kroppen
    eftersom HTML ofta är full av tracking-pixlar och layout-URL:er."""
    for source in (body_text, body_html):
        for url in _extract_urls(source or ""):
            if _url_matches_keyword(url):
                return url
    # Fallback: om ingen keyword-träff men det finns URL:er, ta första
    # URL som har en path längre än "/". Det är bättre att ge användaren
    # NÅGOT än inget när vi vet att leverantören är link-fetch.
    for source in (body_text, body_html):
        for url in _extract_urls(source or ""):
            try:
                path = urlparse(url).path or ""
            except Exception:  # noqa: BLE001
                path = ""
            if path and path != "/":
                return url
    return None
