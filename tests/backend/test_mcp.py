"""C40 — MCP-connector: OAuth (inloggning) + verktyg för kalenderresor.

Inloggningskrav:
  - utan token → 401 (med WWW-Authenticate + resource_metadata)
  - fel användare → nekas
  - utgången token → 401
  - giltig token → fungerar
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("GMAIL_CLIENT_ID", "")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "")
os.environ.setdefault("GMAIL_REFRESH_TOKEN", "")
os.environ.setdefault("DRIVE_REFRESH_TOKEN", "")
os.environ.setdefault("BEZALA_USERNAME", "test@example.com")
os.environ.setdefault("BEZALA_PASSWORD", "secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
BASE = "https://testserver"
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


# ---------- Fixtures ----------


@pytest.fixture()
def env():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.main as main_module
    import app.mcp_oauth as oauth
    import app.mcp_server as server
    from app import models  # noqa: F401
    from app.config import get_settings
    from app.db import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    @contextmanager
    def scope():
        s = Session()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app = main_module.app
    saved = dict(app.dependency_overrides)
    app.dependency_overrides[oauth.get_db] = _get_db
    with patch.object(server, "SessionLocal", Session), \
         patch.object(get_settings(), "app_password", "test-password"):
        os.environ.pop("PUBLIC_BASE_URL", None)
        os.environ.pop("APP_OWNER", None)
        with TestClient(app, base_url=BASE, follow_redirects=False) as c:
            yield c, scope, Session
    app.dependency_overrides.clear()
    app.dependency_overrides.update(saved)


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _register(c):
    r = c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CALLBACK]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def _login(c):
    r = c.post("/login", data={"password": "test-password", "next": "/"})
    assert r.status_code == 303


def _authorize(c, client_id, challenge, state="st-1"):
    return c.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
        "code_challenge": challenge, "code_challenge_method": "S256",
        "state": state, "scope": "trips", "resource": f"{BASE}/mcp",
    })


def _consent(c, page_html, decision="allow"):
    import re
    rid = re.search(r'name="request_id" value="([^"]+)"', page_html).group(1)
    csrf = re.search(r'name="csrf" value="([^"]+)"', page_html).group(1)
    return c.post("/oauth/authorize", data={"request_id": rid, "csrf": csrf, "decision": decision})


def _full_flow(c):
    """Kör hela claude.ai-flödet och returnera token-svaret."""
    client_id = _register(c)
    verifier, challenge = _pkce()
    _login(c)
    r = _authorize(c, client_id, challenge)
    assert r.status_code == 200, r.text
    r = _consent(c, r.text)
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["state"] == ["st-1"]
    r = c.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": q["code"][0],
        "redirect_uri": CALLBACK, "client_id": client_id,
        "code_verifier": verifier, "resource": f"{BASE}/mcp",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    body["client_id"] = client_id
    return body


def _rpc(c, token, method, params=None, rid=1):
    headers = dict(MCP_HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return c.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": rid, "method": method, "params": params or {},
    })


def _call(c, token, name, **args):
    r = _rpc(c, token, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    if not result.get("isError"):
        import json
        result["data"] = json.loads(result["content"][0]["text"])
    return result


def _seed(scope):
    from app.models import CalendarTrip, CalendarTripDay

    with scope() as s:
        t = CalendarTrip(
            start_date=date(2026, 8, 25), end_date=date(2026, 8, 27),
            start_at="2026-08-25T06:20:00+03:00", end_at="2026-08-27T20:00:00+03:00",
            destination="Köpenhamn", country="DK", status="suggested",
            flags=["needs_review"],
            flights=[
                {"id": "f951", "flight_no": "AY 951", "start": "2026-08-25T06:20:00+03:00",
                 "end": "2026-08-25T07:00:00+02:00", "origin": "HEL", "destination_iata": "CPH"},
                {"id": "f953", "flight_no": "AY 953", "start": "2026-08-25T07:25:00+03:00",
                 "end": "2026-08-25T08:05:00+02:00", "origin": "HEL", "destination_iata": "CPH"},
            ],
            flight_options=[
                {"id": "f951", "flight_no": "AY 951", "start": "2026-08-25T06:20:00+03:00",
                 "end": "2026-08-25T07:00:00+02:00"},
                {"id": "f953", "flight_no": "AY 953", "start": "2026-08-25T07:25:00+03:00",
                 "end": "2026-08-25T08:05:00+02:00"},
            ],
            hotels=[], source_event_ids=[],
        )
        s.add(t)
        s.flush()
        for i, d in enumerate((25, 26, 27)):
            s.add(CalendarTripDay(
                trip_id=t.id, day_date=date(2026, 8, d), destination="Köpenhamn",
                start_time="06:20" if i == 0 else None,
                end_time="20:00" if i == 2 else None,
                meetings=[{"id": "m1", "summary": "Customer workshop",
                           "start_time": "10:00", "end_time": "12:00"}] if i == 1 else [],
                hotel={"id": "h", "name": "Scandic"}, source_event_ids=[],
            ))
        s.add(CalendarTrip(
            start_date=date(2026, 11, 26), end_date=date(2026, 11, 26),
            destination="Stockholm", country="SE", status="suggested",
            flags=["missing_return"], flights=[], hotels=[], source_event_ids=[],
            flight_options=[],
        ))
        s.flush()
        return t.id


# ---------- Metadata ----------


def test_metadata_endpoints(env):
    c, _, _ = env
    pr = c.get("/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"] == f"{BASE}/mcp"
    assert pr["authorization_servers"] == [BASE]
    assert c.get("/.well-known/oauth-protected-resource").json() == pr

    asm = c.get("/.well-known/oauth-authorization-server").json()
    assert asm["issuer"] == BASE
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert "none" in asm["token_endpoint_auth_methods_supported"]
    assert asm["registration_endpoint"] == f"{BASE}/oauth/register"


def test_public_base_url_env_wins(env):
    c, _, _ = env
    with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://bot.example/"}):
        pr = c.get("/.well-known/oauth-protected-resource/mcp").json()
    assert pr["resource"] == "https://bot.example/mcp"


# ---------- Inloggningskraven ----------


def test_without_token_401_with_resource_metadata(env):
    c, _, _ = env
    r = _rpc(c, None, "tools/list")
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert www.startswith("Bearer ")
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in www
    # Även GET och fel schema.
    assert c.get("/mcp").status_code == 401
    r = c.post("/mcp", headers={**MCP_HEADERS, "Authorization": "Basic abc"}, json={})
    assert r.status_code == 401


def test_garbage_token_401(env):
    c, _, _ = env
    assert _rpc(c, "bzb_at_nope", "tools/list").status_code == 401


def test_valid_token_works(env):
    c, scope, _ = env
    tok = _full_flow(c)
    assert tok["token_type"] == "Bearer" and tok["expires_in"] == 3600
    r = _rpc(c, tok["access_token"], "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    })
    assert r.status_code == 200, r.text
    assert r.json()["result"]["serverInfo"]["name"] == "Bezala Bot"
    r = _rpc(c, tok["access_token"], "tools/list")
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert names == {
        "list_trips", "get_trip", "update_trip", "update_trip_day", "add_trip_day",
        "remove_trip_day", "approve_trip", "unapprove_trip", "refresh_calendar",
        # C42
        "get_trip_checklist", "set_expectation_status", "list_missing",
        "set_card_line_status",
        # C43
        "mark_private_expense",
        # C44
        "couple_receipt", "get_card_line", "search_receipts", "list_duplicate_receipts",
    }


def test_expired_token_401(env):
    c, _, Session = env
    from app.models import McpOAuthToken

    tok = _full_flow(c)
    s = Session()
    s.query(McpOAuthToken).filter(McpOAuthToken.kind == "access").update(
        {McpOAuthToken.expires_at: datetime.utcnow() - timedelta(seconds=1)}
    )
    s.commit()
    s.close()
    r = _rpc(c, tok["access_token"], "tools/list")
    assert r.status_code == 401
    assert "expired" in r.headers["www-authenticate"]


def test_wrong_user_denied_at_authorize(env):
    c, _, _ = env
    client_id = _register(c)
    _, challenge = _pkce()
    _login(c)
    # Sessionen tillhör ett annat konto än ägaren.
    with patch.dict(os.environ, {"APP_OWNER": "someone-else"}):
        r = _authorize(c, client_id, challenge)
    assert r.status_code == 302
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert q["error"] == ["access_denied"]
    assert "code" not in q


def test_wrong_user_token_denied(env):
    """Token utfärdad till ett konto som inte (längre) är ägaren → 401."""
    c, _, _ = env
    tok = _full_flow(c)
    with patch.dict(os.environ, {"APP_OWNER": "someone-else"}):
        r = _rpc(c, tok["access_token"], "tools/list")
    assert r.status_code == 401


def test_authorize_requires_login(env):
    c, _, _ = env
    client_id = _register(c)
    _, challenge = _pkce()
    r = _authorize(c, client_id, challenge)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("/login?next=")
    # Login med next → tillbaka till authorize.
    nxt = parse_qs(urlsplit(loc).query)["next"][0]
    r = c.post("/login", data={"password": "test-password", "next": nxt})
    assert r.headers["location"] == nxt
    r = c.post("/login", data={"password": "test-password", "next": "https://evil.example"})
    assert r.headers["location"] == "/"


def test_wrong_password_no_session(env):
    c, _, _ = env
    r = c.post("/login", data={"password": "wrong"})
    assert "error" in r.headers["location"]
    client_id = _register(c)
    _, challenge = _pkce()
    assert _authorize(c, client_id, challenge).status_code == 303


def test_user_denies_consent(env):
    c, _, _ = env
    client_id = _register(c)
    _, challenge = _pkce()
    _login(c)
    r = _consent(c, _authorize(c, client_id, challenge).text, decision="deny")
    assert parse_qs(urlsplit(r.headers["location"]).query)["error"] == ["access_denied"]


# ---------- PKCE, koder, refresh, återkallelse ----------


def test_pkce_wrong_verifier_rejected_and_code_single_use(env):
    c, _, _ = env
    client_id = _register(c)
    verifier, challenge = _pkce()
    _login(c)
    r = _consent(c, _authorize(c, client_id, challenge).text)
    code = parse_qs(urlsplit(r.headers["location"]).query)["code"][0]
    bad = c.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
        "client_id": client_id, "code_verifier": "x" * 50,
    })
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    # Koden är förbrukad även efter misslyckat försök.
    again = c.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": CALLBACK,
        "client_id": client_id, "code_verifier": verifier,
    })
    assert again.status_code == 400


def test_authorize_requires_s256(env):
    c, _, _ = env
    client_id = _register(c)
    _login(c)
    r = c.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
        "code_challenge": "abc", "code_challenge_method": "plain", "state": "s",
    })
    assert parse_qs(urlsplit(r.headers["location"]).query)["error"] == ["invalid_request"]


def test_register_rejects_unknown_redirect(env):
    c, _, _ = env
    r = c.post("/oauth/register", json={"redirect_uris": ["https://evil.example/cb"]})
    assert r.status_code == 400
    r = c.post("/oauth/register", json={"redirect_uris": ["http://localhost:5555/callback"]})
    assert r.status_code == 201


def test_unknown_redirect_at_authorize_not_redirected(env):
    c, _, _ = env
    client_id = _register(c)
    _, challenge = _pkce()
    r = c.get("/oauth/authorize", params={
        "response_type": "code", "client_id": client_id,
        "redirect_uri": "https://evil.example/cb",
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    assert r.status_code == 400


def test_refresh_rotates_and_old_tokens_die(env):
    c, _, _ = env
    tok = _full_flow(c)
    r = c.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": tok["client_id"],
    })
    assert r.status_code == 200
    new = r.json()
    assert new["refresh_token"] != tok["refresh_token"]
    assert _rpc(c, tok["access_token"], "tools/list").status_code == 401
    assert _rpc(c, new["access_token"], "tools/list").status_code == 200
    reuse = c.post("/oauth/token", data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
    })
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"


def test_revoke_endpoint_and_app_revoke_all(env):
    c, _, Session = env
    from app.models import McpOAuthToken

    tok = _full_flow(c)
    assert c.post("/oauth/revoke", data={"token": tok["refresh_token"]}).status_code == 200
    assert _rpc(c, tok["access_token"], "tools/list").status_code == 401

    tok2 = _full_flow(c)
    grants = c.get("/api/mcp/tokens").json()["grants"]
    assert len(grants) == 1
    assert c.post("/api/mcp/tokens/revoke-all").json()["revoked"] >= 2
    assert _rpc(c, tok2["access_token"], "tools/list").status_code == 401

    # Tokens lagras bara som hash.
    s = Session()
    stored = {t.token_hash for t in s.query(McpOAuthToken).all()}
    s.close()
    assert tok2["access_token"] not in stored
    assert hashlib.sha256(tok2["access_token"].encode()).hexdigest() in stored


def test_token_for_other_resource_rejected(env):
    c, _, _ = env
    tok = _full_flow(c)
    with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://other.example"}):
        assert _rpc(c, tok["access_token"], "tools/list").status_code == 401


# ---------- Verktygen ----------


def test_tools_review_edit_approve(env):
    c, scope, Session = env
    trip_id = _seed(scope)
    at = _full_flow(c)["access_token"]

    res = _call(c, at, "list_trips", flagged_only=True)
    trips = res["data"]["trips"]
    assert {t["flags"][0] for t in trips} == {"needs_review", "missing_return"}

    res = _call(c, at, "list_trips", date_from="2026-11-01")
    assert [t["destination"] for t in res["data"]["trips"]] == ["Stockholm"]

    detail = _call(c, at, "get_trip", trip_id=trip_id)["data"]
    assert detail["flight_departure"] == {"utc": "2026-08-25T03:20Z",
                                          "helsinki": "2026-08-25 06:20"}
    assert [d["date"] for d in detail["days"]] == ["2026-08-25", "2026-08-26", "2026-08-27"]
    assert detail["days"][0]["start_time_utc"] == "2026-08-25T03:20Z"
    assert detail["days"][1]["meetings"][0]["summary"] == "Customer workshop"
    assert detail["days"][1]["country"] == "DK"
    assert [o["flight_id"] for o in detail["flight_options"]] == ["f951", "f953"]

    # needs_review: välj AY 953.
    out = _call(c, at, "update_trip", trip_id=trip_id, selected_flight_id="f953",
                purpose="Customer workshop with Visma Denmark")["data"]
    assert out["status"] == "edited"
    assert out["flags"] == []
    # C41: flygtiden följer valet, resan börjar 100 min tidigare (hemifrån).
    assert out["flight_departure"]["helsinki"] == "2026-08-25 07:25"
    assert out["travel_start"]["helsinki"] == "2026-08-25 05:45"
    assert [f["flight_no"] for f in out["flights"]] == ["AY 953"]
    assert out["days"][0]["start_time_helsinki"] == "05:45"
    assert "warnings" not in out
    assert out["purpose"] == "Customer workshop with Visma Denmark"

    out = _call(c, at, "update_trip_day", trip_id=trip_id, date="2026-08-26",
                country="se", destination="Malmö")["data"]
    assert out["days"][1]["country"] == "SE"

    out = _call(c, at, "add_trip_day", trip_id=trip_id, date="2026-08-28",
                destination="Köpenhamn")["data"]
    assert out["end_date"] == "2026-08-28"
    # C41: inte på engelska → varning, men inget blockeras.
    assert any("Copenhagen" in w for w in out["warnings"])
    out = _call(c, at, "remove_trip_day", trip_id=trip_id, date="2026-08-28")["data"]
    assert out["end_date"] == "2026-08-27"

    out = _call(c, at, "approve_trip", trip_id=trip_id)["data"]
    assert out["status"] == "approved"
    out = _call(c, at, "unapprove_trip", trip_id=trip_id)["data"]
    assert out["status"] == "edited"

    # Kortet i appen ser ändringen.
    from app.services.calendar_trip_store import list_trips as store_list
    s = Session()
    card = next(t for t in store_list(s) if t["id"] == trip_id)
    s.close()
    assert card["status"] == "edited" and card["purpose"].startswith("Customer")


def test_tools_invalid_values_give_clear_errors(env):
    c, scope, _ = env
    trip_id = _seed(scope)
    at = _full_flow(c)["access_token"]

    def err(name, **args):
        res = _call(c, at, name, **args)
        assert res["isError"] is True, res
        return res["content"][0]["text"]

    assert "Unknown country code: XX" in err("update_trip", trip_id=trip_id, country="XX")
    assert "end must be after start" in err(
        "update_trip", trip_id=trip_id, start="2026-08-27T10:00", end="2026-08-25T10:00")
    assert "not found" in err("get_trip", trip_id=9999)
    assert "not an option" in err("update_trip", trip_id=trip_id, selected_flight_id="nope")
    assert "HH:MM" in err("update_trip_day", trip_id=trip_id, date="2026-08-26",
                          start_time="25:00")
    assert "Unknown status" in err("list_trips", status="done")
    # Inget ändrades av de misslyckade anropen.
    detail = _call(c, at, "get_trip", trip_id=trip_id)["data"]
    assert detail["status"] == "suggested" and detail["country"] == "DK"


def test_refresh_calendar_never_overwrites_edited_or_approved(env):
    c, scope, Session = env
    from app.models import CalendarTrip
    from tests.backend.test_trip_detector import _same_week_events

    with scope() as s:
        s.add(CalendarTrip(start_date=date(2026, 5, 4), end_date=date(2026, 5, 5),
                           status="edited", destination="Stockholm (edited)",
                           purpose="Board meeting"))
        s.add(CalendarTrip(start_date=date(2026, 5, 5), end_date=date(2026, 5, 5),
                           status="approved", destination="Approved"))
    at = _full_flow(c)["access_token"]
    with patch("app.services.calendar_client.fetch_events", return_value=_same_week_events()):
        res = _call(c, at, "refresh_calendar")["data"]
    assert res["status"] == "ok"
    assert res["saved"]["skipped_locked"] == 1
    s = Session()
    rows = {t.destination: t for t in s.query(CalendarTrip).all()}
    s.close()
    assert rows["Stockholm (edited)"].status == "edited"
    assert rows["Stockholm (edited)"].purpose == "Board meeting"
    assert rows["Approved"].status == "approved"


def test_tools_touch_only_calendar_tables(env):
    """Hårdregel: inga skrivningar utanför calendar_trips/calendar_trip_days
    (och inga anrop till Bezala/Netvisor)."""
    c, scope, _ = env
    trip_id = _seed(scope)
    at = _full_flow(c)["access_token"]

    from sqlalchemy import event as sa_event
    from sqlalchemy.orm import Session as SASession

    touched: set[str] = set()

    def on_flush(session, ctx, _inst):
        for obj in list(session.new) + list(session.dirty) + list(session.deleted):
            touched.add(obj.__tablename__)

    sa_event.listen(SASession, "before_flush", on_flush)
    try:
        with patch("app.services.bezala_client.BezalaClient.__init__",
                   side_effect=AssertionError("Bezala must not be used")):
            _call(c, at, "update_trip", trip_id=trip_id, purpose="Sales meeting")
            _call(c, at, "add_trip_day", trip_id=trip_id, date="2026-08-28")
            _call(c, at, "remove_trip_day", trip_id=trip_id, date="2026-08-28")
            _call(c, at, "approve_trip", trip_id=trip_id)
    finally:
        sa_event.remove(SASession, "before_flush", on_flush)
    # mcp_oauth_tokens uppdateras med last_used_at vid tokenvalidering.
    assert touched - {"mcp_oauth_tokens"} <= {"calendar_trips", "calendar_trip_days"}
