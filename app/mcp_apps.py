"""C65 — MCP Apps (visuella vyer i Claude) för Travel Tinder-MCP:n.

Tre `ui://`-resurser (HTML, MIME `text/html;profile=mcp-app`) kopplas till
befintliga verktyg via `_meta.ui.resourceUri`:

- ui://bezala/match.html  — matchningskort (list_pending_decisions, show_match)
- ui://bezala/status.html — statuskedja per utlägg (get_card_line, list_missing)
- ui://bezala/fill.html   — före/efter vid ifyllnad (fill_bezala_drafts)

Verktygens svar (JSON) ändras inte: `_meta` ligger bara på verktygsdefinitionen
och klienter utan UI-stöd ignorerar den. Servern körs stateless, så klientens
`capabilities.extensions["io.modelcontextprotocol/ui"]` går inte att läsa per
anrop; därför deklareras `_meta` alltid och svaren är desamma för alla klienter.

Säkerhet: vyerna är självbärande (inline CSS/JS), CSP-listorna är tomma (inga
externa resurser, inga nätanrop), fritext renderas som text, och knappar som
skriver finns bara när MCP_APPS_BUTTONS_ENABLED=true (false i koden).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

MIME = "text/html;profile=mcp-app"
UI_EXTENSION = "io.modelcontextprotocol/ui"
FLAG_BUTTONS = "MCP_APPS_BUTTONS_ENABLED"

MATCH_URI = "ui://bezala/match.html"
STATUS_URI = "ui://bezala/status.html"
FILL_URI = "ui://bezala/fill.html"

VIEWS = {
    MATCH_URI: ("match.js", "Matchning"),
    STATUS_URI: ("status.js", "Status per utlägg"),
    FILL_URI: ("fill.js", "Före och efter"),
}

# Vilket verktyg som visar vilken vy.
TOOL_VIEWS = {
    "list_pending_decisions": MATCH_URI,
    "show_match": MATCH_URI,
    "get_card_line": STATUS_URI,
    "list_missing": STATUS_URI,
    "fill_bezala_drafts": FILL_URI,
}

# Inga externa domäner: allt är inline, kvitton öppnas som länk via värden.
RESOURCE_UI_META = {
    "ui": {
        "csp": {"connectDomains": [], "resourceDomains": [], "frameDomains": []},
        "prefersBorder": True,
    }
}

UI_DIR = Path(__file__).parent / "ui"


def buttons_enabled() -> bool:
    return os.getenv(FLAG_BUTTONS, "false").strip().lower() in ("1", "true", "yes", "on")


def ui_meta(tool_name: str) -> dict | None:
    uri = TOOL_VIEWS.get(tool_name)
    return {"ui": {"resourceUri": uri}} if uri else None


def client_supports_ui(capabilities) -> bool:
    """True om klienten annonserar MCP Apps (mimeTypes med text/html;profile=mcp-app)."""
    if capabilities is None:
        return False
    if not isinstance(capabilities, dict):
        capabilities = capabilities.model_dump(by_alias=True, exclude_none=True)
    ext = (capabilities.get("extensions") or {}).get(UI_EXTENSION) or {}
    return MIME in (ext.get("mimeTypes") or [])


@lru_cache(maxsize=None)
def _file(name: str) -> str:
    return (UI_DIR / name).read_text(encoding="utf-8")


def _script_safe(text: str) -> str:
    # Inget "</script" inne i ett inline-skript.
    return text.replace("</", "<\\/")


def build_html(uri: str, *, buttons: bool | None = None) -> str:
    script, title = VIEWS[uri]
    buttons = buttons_enabled() if buttons is None else buttons
    config = _script_safe(json.dumps({"buttons": bool(buttons), "view": uri}))
    return (
        "<!doctype html>\n<html lang=\"sv\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>Bezala Bot - {title}</title><style>{_file('common.css')}</style></head>"
        "<body><div id=\"app\"></div>"
        f"<script>window.BB_CONFIG = {config};</script>"
        f"<script>{_script_safe(_file('common.js'))}</script>"
        f"<script>{_script_safe(_file(script))}</script>"
        "</body></html>\n"
    )


def register(mcp) -> None:
    """Registrera de tre ui://-resurserna på FastMCP-servern."""
    for uri, (_, title) in VIEWS.items():
        def make(u=uri):
            def read() -> str:
                return build_html(u)
            return read

        mcp.resource(uri, name=uri.rsplit("/", 1)[-1], title=f"Bezala Bot - {title}",
                     description=f"MCP App-vy: {title} (bara läsning).",
                     mime_type=MIME, meta=RESOURCE_UI_META)(make())
