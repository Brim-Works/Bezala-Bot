"""C40 — OAuth 2.1-auktoriseringsserver för MCP-connectorn i claude.ai.

Följer MCP-specens authorization-del (2025-06-18 / 2025-11-25):
  * Protected Resource Metadata (RFC 9728) på
    /.well-known/oauth-protected-resource[/mcp]
  * Authorization Server Metadata (RFC 8414) på
    /.well-known/oauth-authorization-server
  * Dynamisk klientregistrering (RFC 7591) på /oauth/register — publika
    klienter, endast tillåtna redirect-URI:er (claude.ai + localhost).
  * Authorization code + PKCE (endast S256), resource-parameter (RFC 8707).
  * Refresh-tokens roteras. Återkallelse (RFC 7009) på /oauth/revoke.

Authorize-steget återanvänder appens befintliga inloggning (sessionen från
/login). Bara ägarkontot (APP_OWNER, Mikko) får godkänna. Alla koder och
tokens lagras som SHA-256-hash med utgångstid i Postgres.
"""

from __future__ import annotations

import base64
import hashlib
import html
import logging
import os
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import McpOAuthClient, McpOAuthCode, McpOAuthToken

logger = logging.getLogger(__name__)

router = APIRouter()

MCP_PATH = "/mcp"
SCOPE = "trips"
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
MAX_CLIENTS = 200

DEFAULT_REDIRECT_URIS = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


# ---------- Hjälpfunktioner ----------


def owner() -> str:
    """Det enda konto som får använda MCP. Sätts med APP_OWNER."""
    return os.environ.get("APP_OWNER", "").strip() or "mikko"


def public_base_url(request: Request) -> str:
    """https://bezala-bot-production.up.railway.app (utan avslutande /).

    PUBLIC_BASE_URL vinner. Annars X-Forwarded-Proto/Host (Railway
    terminerar TLS i sin edge) och sist request.base_url.
    """
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host", "").split(",")[0].strip()
        or request.headers.get("host", "").strip()
    )
    if proto and host:
        return f"{proto}://{host}"
    return str(request.base_url).rstrip("/")


def resource_url(request: Request) -> str:
    return public_base_url(request) + MCP_PATH


def resource_metadata_url(request: Request) -> str:
    return public_base_url(request) + "/.well-known/oauth-protected-resource" + MCP_PATH


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.utcnow()


def _allowed_redirect(uri: str) -> bool:
    """claude.ai-callback, extra från MCP_EXTRA_REDIRECT_URIS, eller
    loopback (http://localhost|127.0.0.1:<valfri port>/…) för Claude Code."""
    extra = [
        u.strip() for u in os.environ.get("MCP_EXTRA_REDIRECT_URIS", "").split(",")
        if u.strip()
    ]
    if uri in DEFAULT_REDIRECT_URIS or uri in extra:
        return True
    try:
        parts = urlsplit(uri)
    except ValueError:
        return False
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1")


def _redirect_matches(registered: list[str], uri: str) -> bool:
    if uri in registered:
        return True
    # Loopback: porten får variera (RFC 8252 §7.3).
    try:
        p = urlsplit(uri)
    except ValueError:
        return False
    if p.scheme != "http" or p.hostname not in ("localhost", "127.0.0.1"):
        return False
    for r in registered:
        rp = urlsplit(r)
        if rp.scheme == "http" and rp.hostname == p.hostname and rp.path == p.path:
            return True
    return False


def _same_resource(given: str | None, expected: str) -> bool:
    if not given:
        return True  # äldre klienter (2025-03-26) skickar inte resource
    return given.rstrip("/") == expected.rstrip("/")


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": error, "error_description": description},
        status_code=status,
        headers=_NO_STORE,
    )


def session_user(request: Request) -> str | None:
    if not request.session.get("authenticated"):
        return None
    return request.session.get("user")


# ---------- Metadata ----------


def _protected_resource(request: Request) -> dict:
    return {
        "resource": resource_url(request),
        "authorization_servers": [public_base_url(request)],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Bezala Bot",
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource_metadata(request: Request):
    return _protected_resource(request)


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
def authorization_server_metadata(request: Request):
    base = public_base_url(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SCOPE],
    }


# ---------- Dynamisk klientregistrering (RFC 7591) ----------


@router.post("/oauth/register")
async def register_client(request: Request, db: Session = Depends(get_db)):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _oauth_error("invalid_client_metadata", "Body must be JSON.")
    if not isinstance(body, dict):
        return _oauth_error("invalid_client_metadata", "Body must be a JSON object.")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        return _oauth_error("invalid_redirect_uri", "redirect_uris is required.")
    bad = [u for u in uris if not _allowed_redirect(u)]
    if bad:
        return _oauth_error(
            "invalid_redirect_uri",
            f"Redirect URI not allowed for this server: {', '.join(bad)}",
        )
    if db.query(McpOAuthClient).count() >= MAX_CLIENTS:
        return _oauth_error("invalid_client_metadata", "Too many registered clients.")

    client_id = "bzb_" + secrets.token_urlsafe(24)
    name = str(body.get("client_name") or "")[:255] or None
    db.add(McpOAuthClient(client_id=client_id, client_name=name, redirect_uris=uris))
    db.commit()
    logger.info("MCP OAuth: registrerade klient %s (%s)", client_id, name)
    return JSONResponse(
        {
            "client_id": client_id,
            "client_id_issued_at": int(_now().timestamp()),
            "client_name": name,
            "redirect_uris": uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": SCOPE,
        },
        status_code=201,
        headers=_NO_STORE,
    )


# ---------- Authorize ----------


_CONSENT_PAGE = """<!doctype html>
<html lang="sv"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ge Claude åtkomst — Bezala Bot</title>
<style>
  :root {{ --bg:#f7f7f4; --surface:#fff; --border:#e4e3db; --text:#111412;
          --text-2:#4b524c; --accent:oklch(48% 0.09 165); }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#111412; --surface:#1a1d1b; --border:#2c302d; --text:#eef0ec;
            --text-2:#a9b0aa; }} }}
  body {{ font-family: system-ui, -apple-system, sans-serif; background:var(--bg);
         color:var(--text); display:grid; place-items:center; min-height:100vh;
         margin:0; padding:16px; box-sizing:border-box; }}
  .card {{ background:var(--surface); border:1px solid var(--border);
          border-radius:12px; padding:24px; max-width:420px; width:100%; }}
  h1 {{ font-size:1.15rem; margin:0 0 12px; }}
  p, li {{ color:var(--text-2); line-height:1.45; }}
  .row {{ display:flex; gap:8px; margin-top:20px; }}
  button {{ flex:1; padding:10px; border-radius:8px; font-size:1rem; cursor:pointer;
           border:1px solid var(--border); background:transparent; color:var(--text); }}
  button.ok {{ background:var(--accent); color:#fff; border-color:transparent; }}
  code {{ font-size:.85rem; }}
</style></head>
<body><form class="card" method="post" action="/oauth/authorize">
  <h1>Ge Claude åtkomst till dina resor?</h1>
  <p><strong>{client}</strong> vill använda Bezala Bot via MCP.</p>
  <ul>
    <li>Läsa kalenderresorna</li>
    <li>Redigera och godkänna resor (bara i Bezala Bot)</li>
  </ul>
  <p>Inget skickas till Bezala eller Netvisor. Du kan återkalla åtkomsten
     när som helst.</p>
  <p><code>{redirect_host}</code></p>
  <input type="hidden" name="request_id" value="{request_id}">
  <input type="hidden" name="csrf" value="{csrf}">
  <div class="row">
    <button type="submit" name="decision" value="deny">Avbryt</button>
    <button type="submit" name="decision" value="allow" class="ok">Tillåt</button>
  </div>
</form></body></html>
"""


def _error_redirect(redirect_uri: str, state: str | None, error: str, desc: str, iss: str):
    params = {"error": error, "error_description": desc, "iss": iss}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(redirect_uri + sep + urlencode(params), status_code=302)


@router.get("/oauth/authorize")
def authorize(request: Request, db: Session = Depends(get_db)):
    q = request.query_params
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    state = q.get("state")
    iss = public_base_url(request)

    client = db.get(McpOAuthClient, client_id) if client_id else None
    if client is None:
        return HTMLResponse("Okänd klient (client_id).", status_code=400)
    if not redirect_uri or not _redirect_matches(client.redirect_uris or [], redirect_uri):
        return HTMLResponse("Ogiltig redirect_uri för klienten.", status_code=400)

    # Härifrån får fel skickas tillbaka till klienten.
    if q.get("response_type") != "code":
        return _error_redirect(redirect_uri, state, "unsupported_response_type",
                               "Only response_type=code is supported.", iss)
    challenge = q.get("code_challenge", "")
    if not challenge or q.get("code_challenge_method") != "S256":
        return _error_redirect(redirect_uri, state, "invalid_request",
                               "PKCE with code_challenge_method=S256 is required.", iss)
    resource = q.get("resource") or resource_url(request)
    if not _same_resource(resource, resource_url(request)):
        return _error_redirect(redirect_uri, state, "invalid_target",
                               "Unknown resource.", iss)

    user = session_user(request)
    if user is None:
        nxt = "/oauth/authorize?" + str(request.url.query)
        return RedirectResponse("/login?" + urlencode({"next": nxt}), status_code=303)
    if user != owner():
        logger.warning("MCP OAuth: nekade authorize för användare %r", user)
        return _error_redirect(redirect_uri, state, "access_denied",
                               "This account may not use Bezala Bot MCP.", iss)

    request_id = secrets.token_urlsafe(16)
    csrf = secrets.token_urlsafe(16)
    request.session["mcp_authz"] = {
        "id": request_id,
        "csrf": csrf,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "resource": resource,
        "scope": (q.get("scope") or SCOPE)[:255],
    }
    page = _CONSENT_PAGE.format(
        client=html.escape(client.client_name or "Claude"),
        redirect_host=html.escape(urlsplit(redirect_uri).netloc),
        request_id=request_id,
        csrf=csrf,
    )
    return HTMLResponse(page, headers={"X-Frame-Options": "DENY", **_NO_STORE})


@router.post("/oauth/authorize")
def authorize_decision(
    request: Request,
    request_id: str = Form(...),
    csrf: str = Form(...),
    decision: str = Form(...),
    db: Session = Depends(get_db),
):
    pending = request.session.pop("mcp_authz", None)
    if (
        not pending
        or not secrets.compare_digest(pending.get("id", ""), request_id)
        or not secrets.compare_digest(pending.get("csrf", ""), csrf)
    ):
        return HTMLResponse("Ogiltig eller utgången begäran. Börja om från Claude.", status_code=400)
    iss = public_base_url(request)
    redirect_uri = pending["redirect_uri"]
    state = pending.get("state")

    user = session_user(request)
    if user is None or user != owner():
        return _error_redirect(redirect_uri, state, "access_denied",
                               "This account may not use Bezala Bot MCP.", iss)
    if decision != "allow":
        return _error_redirect(redirect_uri, state, "access_denied",
                               "The user denied access.", iss)

    code = secrets.token_urlsafe(32)
    db.add(McpOAuthCode(
        code_hash=hash_secret(code),
        client_id=pending["client_id"],
        redirect_uri=redirect_uri,
        code_challenge=pending["code_challenge"],
        resource=pending["resource"],
        scope=pending.get("scope"),
        subject=user,
        expires_at=_now() + CODE_TTL,
    ))
    db.commit()
    params = {"code": code, "iss": iss}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    logger.info("MCP OAuth: kod utfärdad för klient %s", pending["client_id"])
    return RedirectResponse(redirect_uri + sep + urlencode(params), status_code=302)


# ---------- Token ----------


def _issue_tokens(db: Session, client_id: str, subject: str, resource: str,
                  scope: str | None) -> dict:
    grant_id = secrets.token_hex(16)
    access = "bzb_at_" + secrets.token_urlsafe(32)
    refresh = "bzb_rt_" + secrets.token_urlsafe(32)
    now = _now()
    for value, kind, ttl in ((access, "access", ACCESS_TTL), (refresh, "refresh", REFRESH_TTL)):
        db.add(McpOAuthToken(
            token_hash=hash_secret(value), kind=kind, client_id=client_id,
            subject=subject, resource=resource, scope=scope, grant_id=grant_id,
            expires_at=now + ttl,
        ))
    db.commit()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_TTL.total_seconds()),
        "refresh_token": refresh,
        "scope": scope or SCOPE,
    }


def _pkce_ok(verifier: str, challenge: str) -> bool:
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    digest = hashlib.sha256(verifier.encode("ascii", "ignore")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, challenge)


@router.post("/oauth/token")
async def token(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    grant_type = form.get("grant_type")
    client_id = form.get("client_id") or ""
    expected_resource = resource_url(request)

    if grant_type == "authorization_code":
        code = form.get("code") or ""
        row = db.get(McpOAuthCode, hash_secret(code)) if code else None
        if row is None or row.used_at is not None or row.expires_at < _now():
            if row is not None and row.used_at is not None:
                # Återanvänd kod → slå ut allt som utfärdats för klienten.
                _revoke_client(db, row.client_id)
            return _oauth_error("invalid_grant", "Invalid or expired code.")
        row.used_at = _now()
        db.commit()
        if client_id and client_id != row.client_id:
            return _oauth_error("invalid_grant", "Code was issued to another client.")
        if (form.get("redirect_uri") or "") != row.redirect_uri:
            return _oauth_error("invalid_grant", "redirect_uri does not match.")
        if not _pkce_ok(form.get("code_verifier") or "", row.code_challenge):
            return _oauth_error("invalid_grant", "PKCE verification failed.")
        if not _same_resource(form.get("resource"), row.resource):
            return _oauth_error("invalid_target", "resource does not match.")
        if row.subject != owner():
            return _oauth_error("invalid_grant", "Account not allowed.")
        if not _same_resource(row.resource, expected_resource):
            return _oauth_error("invalid_target", "Unknown resource.")
        return JSONResponse(
            _issue_tokens(db, row.client_id, row.subject, row.resource, row.scope),
            headers=_NO_STORE,
        )

    if grant_type == "refresh_token":
        value = form.get("refresh_token") or ""
        row = (
            db.query(McpOAuthToken)
            .filter(McpOAuthToken.token_hash == hash_secret(value),
                    McpOAuthToken.kind == "refresh")
            .first()
        ) if value else None
        if row is None or row.revoked_at is not None or row.expires_at < _now():
            return _oauth_error("invalid_grant", "Invalid, expired or revoked refresh token.")
        if client_id and client_id != row.client_id:
            return _oauth_error("invalid_grant", "Token was issued to another client.")
        if row.subject != owner():
            return _oauth_error("invalid_grant", "Account not allowed.")
        if not _same_resource(form.get("resource"), row.resource):
            return _oauth_error("invalid_target", "resource does not match.")
        # Rotation: gamla paret återkallas i samma svar som det nya ges ut.
        _revoke_grant(db, row.grant_id)
        return JSONResponse(
            _issue_tokens(db, row.client_id, row.subject, row.resource, row.scope),
            headers=_NO_STORE,
        )

    return _oauth_error("unsupported_grant_type", f"Unsupported grant_type: {grant_type}")


# ---------- Återkallelse ----------


def _revoke_grant(db: Session, grant_id: str) -> int:
    n = (
        db.query(McpOAuthToken)
        .filter(McpOAuthToken.grant_id == grant_id, McpOAuthToken.revoked_at.is_(None))
        .update({McpOAuthToken.revoked_at: _now()}, synchronize_session=False)
    )
    db.commit()
    return n


def _revoke_client(db: Session, client_id: str) -> int:
    n = (
        db.query(McpOAuthToken)
        .filter(McpOAuthToken.client_id == client_id, McpOAuthToken.revoked_at.is_(None))
        .update({McpOAuthToken.revoked_at: _now()}, synchronize_session=False)
    )
    db.commit()
    return n


def revoke_all(db: Session) -> int:
    n = (
        db.query(McpOAuthToken)
        .filter(McpOAuthToken.revoked_at.is_(None))
        .update({McpOAuthToken.revoked_at: _now()}, synchronize_session=False)
    )
    db.commit()
    return n


@router.post("/oauth/revoke")
async def revoke(request: Request, db: Session = Depends(get_db)):
    """RFC 7009 — svarar alltid 200 (läcker inte om token fanns)."""
    form = await request.form()
    value = form.get("token") or ""
    if value:
        row = (
            db.query(McpOAuthToken)
            .filter(McpOAuthToken.token_hash == hash_secret(value))
            .first()
        )
        if row is not None:
            _revoke_grant(db, row.grant_id)
    return JSONResponse({}, headers=_NO_STORE)


def _require_app_session(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Not authenticated")


@router.get("/api/mcp/tokens")
def list_grants(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_require_app_session),
):
    """Aktiva MCP-inloggningar (en rad per refresh-token)."""
    rows = (
        db.query(McpOAuthToken)
        .filter(McpOAuthToken.kind == "refresh",
                McpOAuthToken.revoked_at.is_(None),
                McpOAuthToken.expires_at > _now())
        .order_by(McpOAuthToken.created_at.desc())
        .all()
    )
    names = {c.client_id: c.client_name for c in db.query(McpOAuthClient).all()}
    return {"grants": [
        {
            "grant_id": r.grant_id,
            "client_id": r.client_id,
            "client_name": names.get(r.client_id),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "expires_at": r.expires_at.isoformat(),
        }
        for r in rows
    ]}


@router.post("/api/mcp/tokens/revoke-all")
def revoke_all_endpoint(
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_require_app_session),
):
    """Återkalla ALLA MCP-tokens. Claude måste logga in igen."""
    return {"revoked": revoke_all(db)}


@router.post("/api/mcp/tokens/{grant_id}/revoke")
def revoke_grant_endpoint(
    grant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _: None = Depends(_require_app_session),
):
    return {"revoked": _revoke_grant(db, grant_id)}


# ---------- Validering av bearer-token (används av /mcp) ----------


def validate_access_token(db: Session, value: str, expected_resource: str) -> str | None:
    """Returnerar fel-beskrivning, eller None om token är giltig."""
    if not value:
        return "Missing bearer token."
    row = (
        db.query(McpOAuthToken)
        .filter(McpOAuthToken.token_hash == hash_secret(value),
                McpOAuthToken.kind == "access")
        .first()
    )
    if row is None:
        return "Unknown token."
    if row.revoked_at is not None:
        return "Token has been revoked."
    if row.expires_at < _now():
        return "Token has expired."
    if row.subject != owner():
        return "Account not allowed."
    if not _same_resource(row.resource, expected_resource):
        return "Token was issued for another resource."
    row.last_used_at = _now()
    db.commit()
    return None
