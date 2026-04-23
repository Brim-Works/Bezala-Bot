"""Extrahera kvitto-länkar från mail-body.

Smart URL-ranking. När avsändaren finns i AppSettings.link_fetch_senders
sparar vi den högst rankade URL:en som pending_link och låter användaren
trigga hämtningen manuellt via POST /api/messages/{id}/fetch-pdf.

Ranking-logik (för att undvika tracking-pixlar och CDN-bilder):
  1. Exkludera helt: bilder, tracking-pixlar, korta URLs, dimensions-URLs
  2. Bonus: anchor-text innehåller 'kvitto'/'receipt'/'invoice'/'ladda ner'
  3. Bonus: URL-substring innehåller samma keywords
  4. URL-längd ≥ 40 tecken (kortare = troligen tracking)
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

# Fångar http(s)-URL:er. Tolerant — täcker click-trackers, signed URLs etc.
_URL_RE = re.compile(r"https?://[^\s<>\"'\\]+", re.IGNORECASE)

# <a href="...">visible text</a>-extraktion: vi behöver text-innehållet
# för att ranka kvitto-länkar med "Ladda ner kvitto"-anchors högst.
_A_HREF_RE = re.compile(
    r"""<a\b[^>]*?\bhref=(["'])(https?://[^"']+)\1[^>]*>(.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")

# URLs vi alltid skippar — bilder och tracking-mönster
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp")
_TRACKING_TOKENS = (
    "cdn.",
    "/cdn/",
    "pixel",
    "track",
    "/open",
    "open?",
    "beacon",
    "spacer",
    "1x1",
    "_pixel",
    "/pixel",
    "no-images",
)
# Dimension-mönster (NxN eller NN x NN) — tipsar om bild eller pixel.
_DIMENSION_RE = re.compile(r"\b\d{1,4}x\d{1,4}\b", re.IGNORECASE)

# Tröskel — under detta är URL:en troligen tracking-pixel eller redirect-stub
MIN_URL_LENGTH = 40

# Keywords för bonus-poäng (URL och anchor-text)
_RECEIPT_KEYWORDS = (
    "kvitto",
    "receipt",
    "invoice",
    "faktura",
    "ladda ner",
    "ladda-ner",
    "download",
)
_PDF_KEYWORDS = ("pdf", ".pdf")
_REDIRECT_KEYWORDS = (
    "click",
    "sendgrid",
    "ct.sendgrid",
    "/cl/",
    "/c/",
    "redirect",
)


def _unquote_html(raw: str) -> str:
    return html.unescape(raw or "")


def _strip_tags(raw: str) -> str:
    return _TAG_STRIP_RE.sub(" ", raw or "")


def is_excluded(url: str) -> bool:
    """True om URL:en är en tracking-pixel, en bild, eller för kort."""
    if not url:
        return True
    lower = url.lower()
    path = urlparse(url).path.lower()
    for ext in _IMAGE_EXTENSIONS:
        if path.endswith(ext):
            return True
    for token in _TRACKING_TOKENS:
        if token in lower:
            return True
    if _DIMENSION_RE.search(lower):
        return True
    if len(url) < MIN_URL_LENGTH:
        return True
    return False


def _score_url(url: str, anchor_text: str = "") -> int:
    """Högre = mer trolig kvitto-länk. Används bara om is_excluded är False."""
    score = 0
    lower_url = url.lower()
    lower_text = (anchor_text or "").lower()

    # Anchor-text-bonus väger mest — användaren har skrivit explicit text
    for kw in _RECEIPT_KEYWORDS:
        if kw in lower_text:
            score += 100
    for kw in _PDF_KEYWORDS:
        if kw in lower_text:
            score += 50

    # URL-substring-bonus
    for kw in _RECEIPT_KEYWORDS:
        if kw in lower_url:
            score += 30
    for kw in _PDF_KEYWORDS:
        if kw in lower_url:
            score += 15

    # Redirect-services (Sendgrid click etc.) — neutral, +5 för att de
    # vanligtvis pekar mot riktig destination.
    for kw in _REDIRECT_KEYWORDS:
        if kw in lower_url:
            score += 5

    # Längd-bonus: längre URL ≈ signerad / token-bärande → troligen "riktig"
    if len(url) > 80:
        score += 10
    if len(url) > 150:
        score += 10

    return score


def _extract_anchor_links(html_body: str) -> list[tuple[str, str]]:
    """Returnerar [(url, anchor_text)] från alla <a href> i HTML."""
    if not html_body:
        return []
    out: list[tuple[str, str]] = []
    for match in _A_HREF_RE.finditer(html_body):
        url = match.group(2).strip()
        text = _strip_tags(match.group(3))
        text = re.sub(r"\s+", " ", html.unescape(text)).strip()
        out.append((url, text))
    return out


def _extract_plain_urls(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _URL_RE.finditer(_unquote_html(text)):
        url = match.group(0).rstrip(".,;)>]")
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def extract_receipt_link(body_text: str | None, body_html: str | None) -> str | None:
    """Returnera den högst rankade kvitto-länken eller None.

    Algoritm:
      1. Plocka alla <a href>-länkar med synlig text från HTML
      2. Plocka alla URL:er från text-bodyn (ingen anchor-text tillgänglig)
      3. Filtrera bort tracking-pixlar/bilder/för korta URL:er
      4. Rangordna efter keyword-träffar; högsta vinner
      5. Om ingen URL får poäng > 0 men det finns någon URL kvar efter
         exkludering → returnera den första (hellre något än inget)
    """
    candidates: list[tuple[int, str]] = []
    seen_urls: set[str] = set()

    # 1. HTML <a href> med anchor-text
    for url, text in _extract_anchor_links(body_html or ""):
        if is_excluded(url) or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append((_score_url(url, text), url))

    # 2. Plain URLs från text-body (ingen anchor-text)
    for url in _extract_plain_urls(body_text or ""):
        if is_excluded(url) or url in seen_urls:
            continue
        seen_urls.add(url)
        candidates.append((_score_url(url, ""), url))

    if not candidates:
        return None

    # Sortera efter score desc, behåll insertion-ordning vid lika score
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]
