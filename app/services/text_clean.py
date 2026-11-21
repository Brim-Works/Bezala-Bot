"""C46 — texter som går till Bezala/Netvisor: vanligt bindestreck '-'.

En- och em-streck ("–", "—") och andra streck-varianter blir '-'.
"""

from __future__ import annotations

import re

_DASHES_RE = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")


def plain_dashes(text: str | None) -> str | None:
    if text is None:
        return None
    return _DASHES_RE.sub("-", text)
