"""C65 — MCP Apps-vyer för Travel Tinder (matchning, status, före/efter).

- Verktygens JSON-svar är oförändrade (med och utan UI-stöd hos klienten).
- Tre ui://-resurser med rätt MIME och tomma CSP-listor.
- show_match: matchning, mismatch, dubblett, kvitto delat med återbetalning.
- Vyerna renderas i en sandboxad iframe med fixtures (Node + Playwright).
- Fritext renderas som text, knappar skriver bara efter bekräftelse, ingen attest.
- Alla flaggor false i koden. Inga Bezala-anrop.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app import mcp_apps  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _line, _receipt  # noqa: E402
from tests.backend.test_c50 import bound_db  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401
from tests.backend.test_c60 import ACCOUNTS, FakeBezala, _draft, _finnair  # noqa: E402
from tests.backend.test_mcp import _full_flow, _rpc, env  # noqa: E402,F401

TAXI = "MIKKO KEINONEN: TAXI HELSINKI, HELSINKI, FI 25.50 EUR"
XSS = '<img src=x onerror="parent.__xss=1">Evil & Co'
UI_CAPS = {"extensions": {mcp_apps.UI_EXTENSION: {"mimeTypes": [mcp_apps.MIME]}}}


@pytest.fixture(autouse=True)
def _no_bezala(monkeypatch):
    from app.services import c50_accounts, c60_draft

    monkeypatch.setattr(c50_accounts, "reference", lambda: (ACCOUNTS, []))
    monkeypatch.setattr(c60_draft, "schedule_verification", lambda *a, **k: None)
    monkeypatch.delenv("BEZALA_DRAFT_FILL_ENABLED", raising=False)
    monkeypatch.delenv(mcp_apps.FLAG_BUTTONS, raising=False)


# ================================================================ MCP: meta, resurser, svar


def _init(c, token, caps):
    r = _rpc(c, token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": caps,
                                      "clientInfo": {"name": "t", "version": "1"}})
    assert r.status_code == 200, r.text


def test_tools_point_at_ui_resources(env):  # noqa: F811
    c, _, _ = env
    tok = _full_flow(c)["access_token"]
    tools = {t["name"]: t for t in _rpc(c, tok, "tools/list").json()["result"]["tools"]}
    want = {"list_pending_decisions": mcp_apps.MATCH_URI, "show_match": mcp_apps.MATCH_URI,
            "get_card_line": mcp_apps.STATUS_URI, "list_missing": mcp_apps.STATUS_URI,
            "fill_bezala_drafts": mcp_apps.FILL_URI}
    for name, uri in want.items():
        assert tools[name]["_meta"]["ui"]["resourceUri"] == uri
    # Alla andra verktyg (t.ex. decide, couple_receipt) har ingen vy.
    for name, t in tools.items():
        if name not in want:
            assert not (t.get("_meta") or {}).get("ui"), name
    # Inget verktyg för att skicka för attest.
    assert not [n for n in tools if re.search(r"submit|attest|send_for|approve_expense", n)]

    res = {r["uri"]: r for r in _rpc(c, tok, "resources/list").json()["result"]["resources"]}
    for uri in (mcp_apps.MATCH_URI, mcp_apps.STATUS_URI, mcp_apps.FILL_URI):
        assert res[uri]["mimeType"] == "text/html;profile=mcp-app"
        body = _rpc(c, tok, "resources/read", {"uri": uri}).json()["result"]["contents"][0]
        assert body["mimeType"] == "text/html;profile=mcp-app"
        assert body["text"].startswith("<!doctype html>")
        csp = body["_meta"]["ui"]["csp"]
        assert csp == {"connectDomains": [], "resourceDomains": [], "frameDomains": []}


def test_tool_answer_unchanged_with_and_without_ui(env):  # noqa: F811
    c, scope, Session = env
    from app.services.decision_queue import list_pending

    with scope() as s:
        _line(s, "501", date(2026, 11, 3), 25.5, desc=TAXI)
        _receipt(s, date(2026, 11, 3), "Taxi Helsinki", 25.5, waiting=False)
    s = Session()
    direct = json.loads(json.dumps(list_pending(s, limit=20, offset=0), default=str))
    s.close()
    tok = _full_flow(c)["access_token"]
    answers = []
    for caps in ({}, UI_CAPS):
        _init(c, tok, caps)
        r = _rpc(c, tok, "tools/call", {"name": "list_pending_decisions", "arguments": {}})
        result = r.json()["result"]
        assert not result.get("isError")
        assert "_meta" not in result or not (result["_meta"] or {}).get("ui")
        answers.append(result)
    assert answers[0] == answers[1]
    data = json.loads(answers[0]["content"][0]["text"])
    assert data == direct
    assert data["items"][0]["kind"] == "receipt"


def test_client_supports_ui():
    assert mcp_apps.client_supports_ui(UI_CAPS)
    assert not mcp_apps.client_supports_ui({})
    assert not mcp_apps.client_supports_ui(None)
    assert not mcp_apps.client_supports_ui({"extensions": {mcp_apps.UI_EXTENSION: {"mimeTypes": ["text/html"]}}})


# ================================================================ show_match


def test_show_match_good(db):  # noqa: F811
    from app.services.c65_views import match_details

    _line(db, "601", date(2026, 11, 3), 25.5, desc=TAXI)
    _receipt(db, date(2026, 11, 3), "Taxi Helsinki", 25.5, waiting=False)
    d = match_details(db, "601")
    assert d["source"] == "suggested"
    assert d["score"]["parts"] == {"amount": 50, "date": 30, "vendor": 30, "location": 0}
    assert d["max_points"] == {"amount": 50, "date": 30, "vendor": 30, "location": 10}
    assert d["fields_ok"]["amount"] and d["fields_ok"]["date"] and d["fields_ok"]["vendor"]
    assert d["card_line"]["place"] == "FI"
    assert d["warnings"] == []


def test_show_match_mismatch(db):  # noqa: F811
    from app.services.c65_views import match_details

    _line(db, "602", date(2026, 11, 3), 25.5, desc=TAXI)
    m = _receipt(db, date(2026, 10, 1), "Scandic Malmö", 180.0, "SEK", waiting=False)
    d = match_details(db, "602", m.message_id)
    assert d["source"] == "given"
    assert not d["fields_ok"]["amount"] and not d["fields_ok"]["vendor"] and not d["fields_ok"]["date"]
    assert d["fields_ok"]["location"] is False  # FI ↔ SE
    assert d["score"]["total"] < 50


def test_show_match_duplicate_and_used_elsewhere(db):  # noqa: F811
    from app.services.c65_views import match_details

    _line(db, "603", date(2026, 11, 3), 25.5, desc=TAXI)
    dup = _receipt(db, date(2026, 11, 3), "Taxi Helsinki", 25.5, waiting=False, duplicate_of="c47-orig")
    used = _receipt(db, date(2026, 11, 3), "Taxi Helsinki", 25.5, waiting=False,
                    bezala_transaction_id="777", bezala_payment_amount=25.5)
    kinds = [w["kind"] for w in match_details(db, "603", dup.message_id)["warnings"]]
    assert kinds == ["duplicate"]
    w = match_details(db, "603", used.message_id)["warnings"]
    assert [x["kind"] for x in w] == ["used_elsewhere"] and "777" in w[0]["text"]


def test_show_match_receipt_shared_with_refund(db):  # noqa: F811
    from app.services.c65_views import match_details

    _line(db, "604", date(2026, 11, 3), 209.48, desc="MIKKO KEINONEN: FINNAIR, VANTAA, FI 209.48 EUR")
    m = _receipt(db, date(2026, 11, 3), "Finnair", 209.48, waiting=False, booking_ref="ABC123",
                 bezala_transaction_id="888", bezala_payment_amount=-209.48,
                 bezala_payment_date="2026-11-10")
    w = match_details(db, "604", m.message_id)["warnings"]
    assert [x["kind"] for x in w] == ["shared_refund"]
    assert "återbetalningen" in w[0]["text"]


def test_show_match_unknown_line(db):  # noqa: F811
    from app.services.c65_views import match_details
    from app.services.calendar_trip_edit import TripEditError

    with pytest.raises(TripEditError):
        match_details(db, "nope")


def test_show_match_postgres(pg_url):  # noqa: F811
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base
    from app.services.c65_views import match_details

    engine = create_engine(pg_url)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine)()
    try:
        _line(s, "pg-65", date(2026, 11, 3), 25.5, desc=TAXI)
        m = _receipt(s, date(2026, 11, 3), "Taxi Helsinki", 25.5, waiting=False,
                     message_id="pg-65-m")
        d = match_details(s, "pg-65")
        assert d["receipt"]["message_id"] == m.message_id
        assert d["score"]["total"] >= 100
    finally:
        s.close()
        engine.dispose()


# ================================================================ HTML: säkerhet och flaggor


def test_html_is_self_contained_and_safe():
    js = "".join((mcp_apps.UI_DIR / f).read_text() for f in ("common.js", "match.js", "status.js", "fill.js"))
    for uri in mcp_apps.VIEWS:
        html = mcp_apps.build_html(uri)
        # Inga externa skript/bilder/stilar, inga nätanrop.
        assert not re.search(r"<(script|link|img|iframe)[^>]+(src|href)=", html, re.I)
        assert "window.BB_CONFIG = {\"buttons\": false" in html
    for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                "new Function", "fetch(", "XMLHttpRequest", "WebSocket", "localStorage"):
        assert bad not in js, bad
    # Inga hemligheter i UI-filerna.
    assert not re.search(r"(api[_-]?key|password|secret|token)\s*[:=]\s*['\"]\w", js, re.I)
    # Bara de befintliga skrivande verktygen, inga attest-knappar.
    assert re.search(r'WRITE_TOOLS = \["decide", "couple_receipt", "fill_bezala_drafts"\]', js)
    assert not re.search(r"attest\"|för attest\b.*btn|submit_|send_for_approval", js, re.I)


def test_script_cannot_break_out():
    assert "</script" not in mcp_apps._script_safe("a</script>b")


def test_all_flags_false_in_code(monkeypatch):
    from app.services import bezala_write_guard as guard
    from app.services import c60_draft

    for name in ("AUTO_COUPLE_ENABLED", "BEZALA_METADATA_PUT_ENABLED", "NETVISOR_ENABLED",
                 "BEZALA_DRAFT_FILL_ENABLED", mcp_apps.FLAG_BUTTONS):
        monkeypatch.delenv(name, raising=False)
    assert guard.env_auto_couple_enabled() is False
    assert guard.metadata_put_enabled() is False
    assert c60_draft.draft_fill_enabled() is False
    assert mcp_apps.buttons_enabled() is False


# ================================================================ rendering (Node + Playwright)


def _has_playwright() -> bool:
    if not shutil.which("node"):
        return False
    r = subprocess.run(["node", "-e", "require('playwright')"], capture_output=True)
    return r.returncode == 0


needs_browser = pytest.mark.skipif(not _has_playwright(), reason="node + playwright saknas")
RENDER = Path(__file__).with_name("c65_render.cjs")


def _render(scenarios: list[dict]) -> dict:
    r = subprocess.run(["node", str(RENDER)], input=json.dumps({"scenarios": scenarios}),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    for name, res in out.items():
        assert not res.get("error"), (name, res.get("error"))
        assert not res.get("errors"), (name, res.get("errors"))
    return out


def _match(vendor="Taxi Helsinki", ok=(True, True, True, None), warnings=(), source="suggested"):
    parts = [50 if ok[0] else 0, 30 if ok[1] else 5, 30 if ok[2] else 3, 10 if ok[3] else 0]
    return {
        "bill_line_id": "601", "source": source, "read_only": True,
        "card_line": {"bill_line_id": "601", "vendor": "TAXI HELSINKI", "amount": 25.5, "currency": "EUR",
                      "date": "2026-11-03", "place": "FI", "status": "missing", "bezala_transaction_id": None},
        "receipt": {"message_id": "m1", "vendor": vendor, "amount": 25.5 if ok[0] else 99.0, "currency": "EUR",
                    "date": "2026-11-03", "place": None, "file_name": "k.pdf",
                    "drive_link": "https://drive.google.com/file/d/abc/view"},
        "max_points": {"amount": 50, "date": 30, "vendor": 30, "location": 10},
        "score": {"total": sum(parts), "parts": dict(zip(("amount", "date", "vendor", "location"), parts)),
                  "date_days_off": 0},
        "fields_ok": dict(zip(("amount", "date", "vendor", "location"), ok)),
        "warnings": list(warnings),
    }


def _queue():
    return {"count": 2, "total": 2, "offset": 0, "returned": 2, "counts": {"receipt": 1, "trip": 1},
            "items": [
                {"id": "receipt:601:m1", "kind": "receipt", "title": "Taxi ↔ TAXI", "subtitle": "x",
                 "yes_label": "Couple", "no_label": "Not this pair", "warning": None,
                 "receipt": {"message_id": "m1", "vendor": "Taxi Helsinki", "amount": 25.5, "currency": "EUR",
                             "date": "2026-11-03", "drive_link": "https://drive.google.com/x"},
                 "card_line": {"bill_line_id": "601", "vendor": "TAXI HELSINKI", "amount": 25.5,
                               "currency": "EUR", "date": "2026-11-03"}},
                {"id": "trip:5", "kind": "trip", "title": "Malmö 3-5 nov", "subtitle": "3/4 found",
                 "yes_label": "Approve", "no_label": "Reject"},
            ]}


def _card_line(state=None, status="resolved", coupled=True, sugg=False):
    d = {"bill_line_id": "601",
         "card_line": {"bill_line_id": "601", "vendor": "TAXI HELSINKI", "amount": 25.5, "currency": "EUR",
                       "date": "2026-11-03", "status": status, "bezala_transaction_id": "5590001"},
         "coupled_receipts": [{"message_id": "m1", "vendor": "Taxi Helsinki", "amount": 25.5, "currency": "EUR",
                               "receipt_date": "2026-11-03", "bezala_transaction_id": "5590001",
                               "drive_link": "https://drive.google.com/x", "score": 110}] if coupled else []}
    if sugg:
        d["suggested_receipt"] = {"message_id": "m2", "vendor": "Taxi", "amount": 25.5, "currency": "EUR",
                                  "date": "2026-11-03", "score": 100}
    if state:
        d["bezala_transaction"] = {"transactions": [{"transaction_id": "5590001", "state": state,
                                                     "is_draft": state == "draft", "status_fields": {"state": state}}]}
    return d


def _fill_result(bound_db, state="draft"):  # noqa: F811
    from app.services import c60_draft as c60

    _finnair(bound_db)
    tx = _draft("5594776", state=state, description="Mikkos egen text",
                original_amount="418.00", original_amount_currency="EUR")
    return json.loads(json.dumps(c60.fill_drafts(bound_db, ["5594776"], client=FakeBezala({"5594776": tx})),
                                 default=str))


@needs_browser
def test_views_render_with_fixtures(bound_db, tmp_path):  # noqa: F811
    match_html = mcp_apps.build_html(mcp_apps.MATCH_URI)
    status_html = mcp_apps.build_html(mcp_apps.STATUS_URI)
    fill_html = mcp_apps.build_html(mcp_apps.FILL_URI)
    fill_draft = _fill_result(bound_db)
    reviewing = dict(fill_draft)
    reviewing["items"] = [dict(fill_draft["items"][0], state="reviewing", changes={},
                               skipped=["Inte ett utkast (state='reviewing') - boten skriver inte"])]
    shared = {"kind": "shared_refund", "text": "Kvittot används redan för återbetalningen 2026-11-10 (utlägg 888)."}
    dup = {"kind": "duplicate", "text": "Kvittot är en dubblett av ett annat kvitto (c47-1)."}
    out = _render([
        {"name": "match", "html": match_html, "result": _match(), "attrs": {"fields": ["[data-field]", "data-cls"]}},
        {"name": "mismatch", "html": match_html, "result": _match(vendor="Scandic", ok=(False, False, False, False)),
         "attrs": {"fields": ["[data-field]", "data-cls"]}},
        {"name": "dup", "html": match_html, "result": _match(warnings=[dup]),
         "attrs": {"w": ["[data-warning]", "data-warning"]}},
        {"name": "shared", "html": match_html, "result": _match(warnings=[shared]),
         "attrs": {"w": ["[data-warning]", "data-warning"]}},
        {"name": "xss", "html": match_html, "result": _match(vendor=XSS)},
        {"name": "queue", "html": match_html, "result": _queue(), "responses": {"show_match": _match()},
         "settle": 600},
        {"name": "status_draft", "html": status_html, "result": _card_line(state="draft"),
         "responses": {"show_match": _match(source="coupled")},
         "attrs": {"steps": ["[data-step]", "data-cls"]}},
        {"name": "status_reviewing", "html": status_html, "result": _card_line(state="reviewing"),
         "attrs": {"steps": ["[data-step]", "data-cls"]}},
        {"name": "status_accounted", "html": status_html, "result": _card_line(state="accounted"),
         "attrs": {"steps": ["[data-step]", "data-cls"]}},
        {"name": "status_failed", "html": status_html, "result": _card_line(state="failed"),
         "attrs": {"steps": ["[data-step]", "data-cls"]}},
        {"name": "status_unverified", "html": status_html,
         "result": _card_line(status="coupling_unverified", coupled=False, sugg=True),
         "attrs": {"steps": ["[data-step]", "data-cls"]}},
        {"name": "status_missing", "html": status_html, "result": _card_line(status="missing", coupled=False),
         "attrs": {"steps": ["[data-step]", "data-cls"]}},
        {"name": "status_click", "html": status_html, "result": _card_line(state="draft"),
         "responses": {"show_match": _match(source="coupled")},
         "actions": [{"click": "[data-step=receipt]", "wait": 500}]},
        {"name": "fill", "html": fill_html, "result": fill_draft, "screenshot": str(tmp_path / "fill.png"),
         "attrs": {"state": ["[data-state]", "data-state"]}},
        {"name": "fill_reviewing", "html": fill_html, "result": reviewing,
         "attrs": {"state": ["[data-state]", "data-state"]}},
    ])
    # Matchning: grönt/rött per fält och poängen med uppdelning.
    assert out["match"]["attrs"]["fields"] == ["ok", "ok", "ok", "idle"]
    assert "Poäng 110" in out["match"]["text"] and "belopp 50 + datum 30 + leverantör 30 + plats 0" in out["match"]["text"]
    assert "KORTRAD" in out["match"]["text"].upper() and "k.pdf" in out["match"]["text"]
    assert out["mismatch"]["attrs"]["fields"] == ["bad", "bad", "bad", "bad"]
    assert out["dup"]["attrs"]["w"] == ["duplicate"] and "dubblett" in out["dup"]["text"]
    assert out["shared"]["attrs"]["w"] == ["shared_refund"] and "återbetalningen" in out["shared"]["text"]
    # Fritext är text, aldrig HTML.
    assert XSS in out["xss"]["text"]
    assert "<img" not in out["xss"]["html"] and "&lt;img" in out["xss"]["html"]
    # Kön: detaljer hämtas med show_match (bara läsning), inga knappar (flaggan av).
    assert [c["name"] for c in out["queue"]["calls"]] == ["show_match"]
    assert "Andra beslut" in out["queue"]["text"] and "Malmö" in out["queue"]["text"]
    # Statuskedjan.
    assert out["status_draft"]["attrs"]["steps"] == ["ok", "ok", "ok", "idle", "idle"]
    assert out["status_reviewing"]["attrs"]["steps"] == ["ok", "ok", "ok", "ok", "idle"]
    assert out["status_accounted"]["attrs"]["steps"] == ["ok", "ok", "ok", "ok", "ok"]
    assert out["status_failed"]["attrs"]["steps"][2] == "bad"
    assert "fastnat" in out["status_failed"]["text"]
    assert out["status_unverified"]["attrs"]["steps"][1:3] == ["warn", "bad"]
    assert "coupling_unverified" in out["status_unverified"]["text"]
    assert out["status_missing"]["attrs"]["steps"][1] == "bad" and "Kvitto saknas" in out["status_missing"]["text"]
    assert "Poäng 110" in out["status_click"]["text"]
    # Före/efter.
    t = out["fill"]["text"]
    assert out["fill"]["attrs"]["state"] == ["draft"]
    assert "Mikkos egen text" in t and "ändrat av Mikko - rörs inte" in t and "ändras" in t
    assert "418,00 EUR" in t and "446,77 EUR" in t and "Hoppas över" in t
    assert out["fill_reviewing"]["attrs"]["state"] == ["reviewing"]
    assert "Hoppas över - inte utkast (1)" in out["fill_reviewing"]["text"]
    assert "Mikkos egen text" not in out["fill_reviewing"]["text"]
    # Inga knappar som skriver eller skickar för attest när flaggan är av.
    for name, res in out.items():
        assert not [b for b in res["buttons"] if re.search(r"attest|skicka (för|in)|^skicka$|submit", b, re.I)], name
        assert all(c["name"] in ("show_match", "get_card_line", "ui/open-link") for c in res["calls"]), name
    assert not [b for b in out["queue"]["buttons"] if b in ("Couple", "Not this pair", "Senare")]
    assert not [b for b in out["fill"]["buttons"] if "Fyll i" in b]


@needs_browser
def test_write_buttons_need_confirmation(bound_db):  # noqa: F811
    html = mcp_apps.build_html(mcp_apps.MATCH_URI, buttons=True)
    fill_html = mcp_apps.build_html(mcp_apps.FILL_URI, buttons=True)
    decided = {"item_id": "receipt:601:m1", "decision": "yes", "result": "Utkast skapat, koppling inte verifierad än"}
    out = _render([
        {"name": "q", "html": html, "result": _queue(), "settle": 600,
         "responses": {"show_match": _match(), "decide": decided},
         "actions": [
             {"click": "[data-write=decide]", "wait": 200}, {"snapshot": True},
             {"click": "[data-confirm=no]", "wait": 200}, {"snapshot": True},
             {"click": "[data-write=decide]", "wait": 200},
             {"click": "[data-confirm=yes]", "wait": 400}, {"snapshot": True},
             {"eval": "BB.callTool('decide', {item_id: 'x', decision: 'yes'}).then(() => 'ok', e => e.message)"},
             {"eval": "BB.callTool('fill_bezala_drafts', {apply: true}).then(() => 'ok', e => e.message)"},
             {"eval": "BB.callTool('couple_receipt', {}).then(() => 'ok', e => e.message)"},
         ]},
        {"name": "single", "html": html, "result": _match(),
         "responses": {"couple_receipt": {"status": "utkast skapat, koppling inte verifierad än"}},
         "actions": [{"click": "[data-write=couple_receipt]", "wait": 200}, {"snapshot": True},
                     {"click": "[data-confirm=yes]", "wait": 400}]},
        {"name": "single_dup", "html": html, "result": _match(warnings=[{"kind": "duplicate", "text": "dubblett"}])},
        {"name": "fill", "html": fill_html, "result": _fill_result(bound_db),
         "responses": {"fill_bezala_drafts": {"apply": True, "note": "ok", "count": 0, "items": []}},
         "actions": [{"click": "[data-write=fill_bezala_drafts]", "wait": 200},
                     {"click": "[data-confirm=yes]", "wait": 400}]},
    ])
    q = out["q"]
    # 1) Klick → bekräftelse visas, inget anrop än.
    assert "Utkast skapas, inget skickas." in q["steps"][0]["text"]
    assert "Koppla kvitto Taxi Helsinki 25,50 EUR till kortrad TAXI HELSINKI 25,50 EUR?" in q["steps"][0]["text"]
    assert [c["name"] for c in q["steps"][0]["calls"]] == ["show_match"]
    # 2) Avbryt → inget skrivs.
    assert [c["name"] for c in q["steps"][1]["calls"]] == ["show_match"]
    # 3) Ja → exakt ett decide-anrop med Mikkos svar.
    writes = [c for c in q["steps"][2]["calls"] if c["name"] == "decide"]
    assert writes == [{"name": "decide", "arguments": {"item_id": "receipt:601:m1", "decision": "yes"}}]
    assert "koppling inte verifierad" in q["steps"][2]["text"]
    # Skrivande verktyg nås inte utan bekräftelse.
    assert [s["eval"] for s in q["steps"][3:]] == ["Inte tillåtet från vyn: decide",
                                                    "Inte tillåtet från vyn: fill_bezala_drafts",
                                                    "Inte tillåtet från vyn: couple_receipt"]
    s = out["single"]
    assert "Koppla kvitto Taxi Helsinki" in s["steps"][0]["text"] and s["steps"][0]["calls"] == []
    assert [c["arguments"] for c in s["calls"] if c["name"] == "couple_receipt"] == [
        {"bill_line_id": "601", "message_id": "m1"}]
    # Dubblett → ingen kopplingsknapp.
    assert "Koppla kvitto" not in out["single_dup"]["buttons"]
    f = [c for c in out["fill"]["calls"] if c["name"] == "fill_bezala_drafts"]
    assert f == [{"name": "fill_bezala_drafts", "arguments": {"transaction_ids": ["5594776"], "apply": True}}]
    for res in out.values():
        assert not [b for b in res["buttons"] if re.search(r"attest|skicka (för|in)|^skicka$|submit", b, re.I)]
