"""C61 — läs kvittots totalbelopp ur mejlets text (Skånetrafiken m.fl.).

Skånetrafiken skriver "Summa betalt: 150 SEK" (utan decimaler) och
"Varav moms 0,0%: 0 SEK". Bezala läste nollan på momsraden som totalen.
Här tas totalen bara från en rad märkt som total ("Summa betalt", "Totalt",
"Total", "Att betala", "Summa") - aldrig från en momsrad. Gissar aldrig:
hittas ingen sådan rad blir svaret None.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass

# Etiketter i prioritetsordning (första träffen i listan vinner).
_LABELS = (
    r"summa\s+betalt",
    r"totalt\s+betalt",
    r"betalt\s+totalt",
    r"att\s+betala",
    r"totalt",
    r"total\s+amount",
    r"grand\s+total",
    r"total",
    r"summa",
)
_VAT_WORDS = re.compile(r"\b(varav|moms|vat|mva|alv|tax|exkl|exkl\.|excl)\b", re.I)
_CUR = r"(SEK|EUR|DKK|NOK|kr|€)"
# 150 / 22,80 / 22.80 / 1 250 / 1 250,00 (tusental bara med mellanslag).
_NUM = r"(\d{1,3}(?:[   ]\d{3})+|\d+)(?:[,.](\d{1,2}))?"
_AMOUNT_RE = re.compile(rf"(?:{_CUR}\s*)?{_NUM}\s*{_CUR}?", re.I)


@dataclass(frozen=True)
class ReceiptTotal:
    amount: float
    currency: str
    label: str
    line: str

    def text(self) -> str:
        """Totalraden som boten lägger överst i PDF:en: "150.00 SEK"."""
        return f"{self.amount:.2f} {self.currency}"


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d|table)>", "\n", s)
    s = re.sub(r"(?i)</t[dh]>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    return _html.unescape(s)


def _norm_cur(c: str | None) -> str | None:
    if not c:
        return None
    c = c.strip().upper()
    return {"KR": "SEK", "€": "EUR"}.get(c, c)


def _to_float(whole: str, dec: str | None) -> float:
    whole = re.sub(r"[   ]", "", whole)
    return round(float(f"{whole}.{dec or '0'}"), 2)


def parse_total(text: str | None = None, html: str | None = None,
                default_currency: str | None = None) -> ReceiptTotal | None:
    """Kvittots totalbelopp, eller None om ingen tydlig totalrad finns."""
    body = "\n".join(x for x in (text or "", html_to_text(html)) if x)
    if not body.strip():
        return None
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln]
    for label in _LABELS:
        label_re = re.compile(rf"(?<![a-zåäö]){label}(?![a-zåäö])", re.I)
        for i, ln in enumerate(lines):
            m = label_re.search(ln)
            if not m or _VAT_WORDS.search(ln):
                continue
            # Beloppet står efter etiketten på samma rad, annars på nästa rad.
            rest = ln[m.end():]
            candidates = [rest]
            if i + 1 < len(lines) and not _VAT_WORDS.search(lines[i + 1]):
                candidates.append(lines[i + 1])
            for cand in candidates:
                for am in _AMOUNT_RE.finditer(cand):
                    cur = _norm_cur(am.group(1) or am.group(4)) or _norm_cur(default_currency)
                    if not cur:
                        continue
                    if am.group(1) is None and am.group(4) is None and cand is not rest:
                        continue
                    value = _to_float(am.group(2), am.group(3))
                    return ReceiptTotal(amount=value, currency=cur,
                                        label=m.group(0), line=ln if cand is rest
                                        else f"{ln} {cand}")
    return None


def is_skanetrafiken(*values: str | None) -> bool:
    joined = " ".join(v or "" for v in values).casefold()
    return "skanetrafiken" in joined or "skånetrafiken" in joined


SUMMARY_TITLE = "Sammanfattning av Bezala Bot"


def summary_header_html(total: ReceiptTotal) -> str:
    """En sidhuvudsrad överst i PDF:en. Originalinnehållet ändras inte."""
    return (
        '<div class="bb-summary" style="border-bottom:1px solid #999;'
        'margin:0 0 8px 0;padding:0 0 4px 0;font-size:10pt;">'
        f"{SUMMARY_TITLE}: Total: {_html.escape(total.text())}</div>"
    )


def add_summary_header(html: str, total: ReceiptTotal) -> str:
    """Lägg sidhuvudsraden direkt efter <body> (eller först)."""
    header = summary_header_html(total)
    m = re.search(r"(?is)<body[^>]*>", html)
    if m:
        return html[:m.end()] + header + html[m.end():]
    return header + html


def pdf_text(pdf_bytes: bytes) -> str:
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # noqa: BLE001
        return ""


def stamp_pdf_total(pdf_bytes: bytes) -> tuple[bytes, ReceiptTotal | None]:
    """Lägg sidhuvudsraden "Sammanfattning av Bezala Bot: Total: 150.00 SEK"
    i övre marginalen på sida 1 i en PDF som boten gjort av mejlet.
    Originalinnehållet ändras inte (raden läggs ovanpå den tomma marginalen).
    Ingen tydlig totalrad i PDF:en → PDF:en lämnas orörd."""
    import io
    import logging

    log = logging.getLogger(__name__)
    text = pdf_text(pdf_bytes)
    if SUMMARY_TITLE in text:
        return pdf_bytes, parse_total(text)
    total = parse_total(text)
    if total is None:
        return pdf_bytes, None
    try:
        from pypdf import PdfReader, PdfWriter
        from weasyprint import CSS, HTML

        reader = PdfReader(io.BytesIO(pdf_bytes))
        first = reader.pages[0]
        w, h = float(first.mediabox.width), float(first.mediabox.height)
        css = (f"@page {{ size: {w}pt {h}pt; margin: 0; }} body {{ margin: 0; }} "
               ".bb-summary { position: absolute; top: 6mm; left: 18mm; right: 18mm; "
               "font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #111; }")
        head = HTML(string=f"<html><body>{summary_header_html(total)}</body></html>") \
            .write_pdf(stylesheets=[CSS(string=css)])
        overlay = PdfReader(io.BytesIO(head)).pages[0]
        writer = PdfWriter()
        for i, page in enumerate(reader.pages):
            if i == 0:
                page.merge_page(overlay)
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue(), total
    except Exception:  # noqa: BLE001 - kvittot laddas upp ändå, utan raden
        log.exception("C61: totalraden kunde inte läggas i PDF:en")
        return pdf_bytes, total
