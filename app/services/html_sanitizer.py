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
# Img src → 1x1 transparent PNG om källan är extern (http/https),
# cid: (mail-inline), tom, eller ogiltig data-URI. Tidigare sparades
# ERR_INVALID_URL-rops från iframe när Skånetrafiken-mail hade
# src="cid:logo" eller src="" som browsern inte kunde upplösa.
_IMG_SRC_ANY_RE = re.compile(
    r"""(<img\b[^>]*?\bsrc=)(["'])(.*?)\2""",
    re.IGNORECASE | re.DOTALL,
)
# Strippa externa stylesheets — weasyprint/browser försöker fetcha
# och kan hänga / misslyckas inuti iframe srcdoc. Inline <style>
# strippas separat av _STYLE_RE.
_LINK_STYLESHEET_RE = re.compile(
    r"""<link\b[^>]*?>""", re.IGNORECASE,
)
_TRANSPARENT_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
# Extrahera alla <a href="..."> ur (helst saniterade) HTML
_A_HREF_RE = re.compile(r"""<a\b[^>]*?\bhref=(["'])([^"']+)\1[^>]*>(.*?)</a>""", re.IGNORECASE | re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _replace_img_src(match: re.Match) -> str:
    prefix, quote, src = match.group(1), match.group(2), match.group(3).strip()
    # Bevara välformade inline data-URIs med mime-typ
    if src.startswith("data:") and ";base64," in src and len(src) > 32:
        return match.group(0)
    return f"{prefix}{quote}{_TRANSPARENT_PNG}{quote}"


def sanitize_html(raw: str) -> str:
    """Ta bort scripts/styles/event-handlers + stylesheets. Ersätt
    alla img-källor som inte är välformade data-URIs med placeholder
    (skyddar mot tracking-pixlar OCH ERR_INVALID_URL från cid:/tomma
    src i iframe srcdoc). Neutralisera javascript:-URL:er."""
    if not raw:
        return ""
    clean = raw
    clean = _SCRIPT_RE.sub("", clean)
    clean = _STYLE_RE.sub("", clean)
    clean = _LINK_STYLESHEET_RE.sub("", clean)
    clean = _EVENT_ATTR_RE.sub("", clean)
    clean = _JS_URL_RE.sub(r'\1="#blocked"', clean)
    clean = _IMG_SRC_ANY_RE.sub(_replace_img_src, clean)
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
