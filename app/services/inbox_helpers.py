"""C47 — små hjälpare för kvittofiler (bild/PDF)."""

from __future__ import annotations


def is_receipt_file(filename: str | None, mime: str | None, data: bytes | None) -> bool:
    """PDF eller JPEG/PNG (magic bytes avgör). Små bilder (<8 kB, t.ex.
    loggor och signaturer i mailet) räknas inte."""
    if not data:
        return False
    if data[:4] == b"%PDF":
        return True
    is_img = data[:3] == b"\xff\xd8\xff" or data[:8] == b"\x89PNG\r\n\x1a\n"
    return is_img and len(data) >= 8 * 1024
