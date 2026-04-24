"""Minimal HTML-sanitering för preview i Drawer.

Renderas i iframe med sandbox=""-attribut på frontend — all JS neutraliseras
där. Denna sanitizer strippar ändå scripts/styles/event-handlers så att
även om sandboxingen kringgås någonstans uppströms, så finns inget
exekverbart kvar. Dessutom extraheras alla <a href>-länkar till en
separat lista så användaren kan välja vilken att följa.

Enkel regex-baserad — vi bygger INTE en full HTML-parser (tung beroende).
Mail-klienter genererar normaliserad HTML så regex-approach räcker.
"""

from __future__ import annotations

import logging
import re
from html import unescape

logger = logging.getLogger(__name__)


_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
# Skadliga event-handlers (onclick, onerror, onload, onmouseover, ...)
_EVENT_ATTR_RE = re.compile(r"""\s+on\w+\s*=\s*(?:"[^"]*"|'[^']*'|\S+)""", re.IGNORECASE)
# javascript:-URL:er i href/src
_JS_URL_RE = re.compile(r"""(href|src)\s*=\s*(?:"\s*javascript:[^"]*"|'\s*javascript:[^']*')""", re.IGNORECASE)
# Externa img src → 1x1 transparent PNG (så vi inte exponerar user:s
# IP-adress till tracking-pixlar när mailet öppnas). En valid data-URI
# krävs — tom "data:" ger ERR_INVALID_URL i browser-konsolen.
_IMG_SRC_EXTERNAL_RE = re.compile(r"""(<img\b[^>]*?\bsrc=)(["'])(?:https?://[^"']*)\2""", re.IGNORECASE)
_TRANSPARENT_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
# Extrahera alla <a href="..."> ur (helst saniterade) HTML
_A_HREF_RE = re.compile(r"""<a\b[^>]*?\bhref=(["'])([^"']+)\1[^>]*>(.*?)</a>""", re.IGNORECASE | re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def sanitize_html(raw: str) -> str:
    """Ta bort scripts/styles/event-handlers. Ersätt externa bilder med
    placeholder. Neutralisera javascript:-URL:er."""
    if not raw:
        return ""
    clean = raw
    clean = _SCRIPT_RE.sub("", clean)
    clean = _STYLE_RE.sub("", clean)
    clean = _EVENT_ATTR_RE.sub("", clean)
    clean = _JS_URL_RE.sub(r'\1="#blocked"', clean)
    clean = _IMG_SRC_EXTERNAL_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_TRANSPARENT_PNG}{m.group(2)}",
        clean,
    )
    return clean


def extract_links(html: str) -> list[dict]:
    """Returnerar [{href, text}] — alla <a>-element i HTML:en.
    Duplikater (samma href) behålls inte. Text trimmas till 120 tecken."""
    if not html:
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for match in _A_HREF_RE.finditer(html):
        href = unescape(match.group(2)).strip()
        if not href or href.startswith("#") or href.startswith("mailto:"):
            continue
        if href.startswith("javascript:"):
            continue
        if not (href.startswith("http://") or href.startswith("https://")):
            continue
        if href in seen:
            continue
        seen.add(href)
        inner = match.group(3)
        text = _TAG_STRIP_RE.sub(" ", inner)
        text = unescape(text).strip()
        text = re.sub(r"\s+", " ", text)[:120]
        out.append({"href": href, "text": text or href})
    return out
