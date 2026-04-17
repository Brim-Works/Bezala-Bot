"""PDF-validering via magic bytes."""

from __future__ import annotations

PDF_MAGIC = b"%PDF"


def is_valid_pdf(data: bytes) -> bool:
    return bool(data) and data[:4] == PDF_MAGIC


def looks_like_pdf(filename: str, mime_type: str, data: bytes) -> bool:
    """Är detta en PDF som vi ska spara?

    Accepterar om antingen mime-type, filändelse ELLER magic bytes matchar —
    men kräver alltid att magic bytes är korrekta så vi aldrig sparar skräp.
    """
    if not is_valid_pdf(data):
        return False
    if mime_type == "application/pdf":
        return True
    if filename.lower().endswith(".pdf"):
        return True
    # Magic bytes matchar — acceptera även om mime/filnamn är fel
    return True
