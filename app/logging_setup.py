"""C47 Del 6.7 — loggning.

Railway visar allt som skrivs till stderr som nivå "error". Tidigare gick
all loggning (logging.basicConfig) till stderr, och fontTools (PDF-
konverteringen i WeasyPrint) loggar hundratals INFO/DEBUG-rader per kvitto
— det blev hundratals falska "error"-rader. Nu:

* DEBUG/INFO/WARNING → stdout, ERROR/CRITICAL → stderr
* fontTools (och WeasyPrints underloggare) → WARNING
"""

from __future__ import annotations

import logging
import sys

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
QUIET_LOGGERS = ("fontTools", "fontTools.subset", "fontTools.ttLib", "weasyprint.progress")


class _BelowError(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.ERROR


def configure_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_bezala", False):
            root.removeHandler(h)
    fmt = logging.Formatter(FORMAT)
    out = logging.StreamHandler(sys.stdout)
    out.setLevel(logging.DEBUG)
    out.addFilter(_BelowError())
    out.setFormatter(fmt)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.ERROR)
    err.setFormatter(fmt)
    for h in (out, err):
        h._bezala = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(level)
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
