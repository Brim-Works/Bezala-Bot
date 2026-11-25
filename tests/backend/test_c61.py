"""C61 — Skånetrafiken-belopp via boten + Sträcka (km), Vehicle, passagerare.

Skånetrafiken skriver "Summa betalt: 150 SEK" (utan decimaler) och
"Varav moms 0,0%: 0 SEK". Bezala tog nollan.
"""

from __future__ import annotations

import os
from datetime import date

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services import c60_draft as c60  # noqa: E402
from app.services import receipt_total as rt  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _line, _receipt  # noqa: E402
from tests.backend.test_c50 import bound_db  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401
from tests.backend.test_c60 import (  # noqa: E402
    ACCOUNTS, SKANE, FakeBezala, _draft, _finnair, _no_bezala, _trip_leg,  # noqa: F401
)

SKANE_HTML = """<html><head><style>p{}</style></head><body>
<h1>Tack för ditt köp!</h1>
<table>
<tr><td>Enkelbiljett, Öresund</td><td>150 SEK</td></tr>
<tr><td>Summa betalt:</td><td>150 SEK</td></tr>
<tr><td>Varav moms 0,0%:</td><td>0 SEK</td></tr>
</table></body></html>"""


# ================================================================ Del 1: parsern


@pytest.mark.parametrize("text,amount,cur", [
    ("Summa betalt: 150 SEK\nVarav moms 0,0%: 0 SEK", 150.0, "SEK"),
    ("Varav moms 0,0%: 0 SEK\nSumma betalt: 150 SEK", 150.0, "SEK"),
    ("Summa betalt: 22,80 SEK\nVarav moms 6%: 1,29 SEK", 22.8, "SEK"),
    ("Summa betalt: 245,00 SEK", 245.0, "SEK"),
    ("Totalt: 1 250 kr\nVarav moms 25%: 250 kr", 1250.0, "SEK"),
    ("Summa betalt\n150 SEK\nVarav moms 0,0%\n0 SEK", 150.0, "SEK"),
    ("Total 598.00 SEK", 598.0, "SEK"),
])
def test_parse_total(text, amount, cur):
    t = rt.parse_total(text)
    assert t is not None and t.amount == amount and t.currency == cur


def test_vat_row_with_zero_is_never_the_total():
    assert rt.parse_total("Varav moms 0,0%: 0 SEK") is None
    assert rt.parse_total("Moms: 0 SEK\nBiljett 150 SEK") is None


def test_parse_total_from_html_table():
    t = rt.parse_total(None, SKANE_HTML)
    assert t.text() == "150.00 SEK" and t.label.lower() == "summa betalt"


def test_no_total_line_gives_none():
    assert rt.parse_total("Hej Mikko, din biljett är klar.") is None
    assert rt.parse_total("") is None


def test_summary_header_is_added_without_changing_the_mail():
    t = rt.parse_total(None, SKANE_HTML)
    out = rt.add_summary_header(SKANE_HTML, t)
    assert "Sammanfattning av Bezala Bot: Total: 150.00 SEK" in out
    # Mejlets eget innehåll finns kvar oförändrat efter sidhuvudsraden.
    assert out.replace(rt.summary_header_html(t), "") == SKANE_HTML


def test_stamp_pdf_adds_header_line_and_keeps_text():
    pytest.importorskip("weasyprint")
    from app.services.html_pdf_converter import HtmlToPdfError, html_to_pdf

    try:
        pdf = html_to_pdf(SKANE_HTML)
    except HtmlToPdfError as exc:
        pytest.skip(f"weasyprint fungerar inte här: {exc}")
    out, total = rt.stamp_pdf_total(pdf)
    text = rt.pdf_text(out)
    assert total.text() == "150.00 SEK"
    assert "Sammanfattning av Bezala Bot: Total: 150.00 SEK" in text
    assert "Summa betalt" in text and "Varav moms 0,0%" in text
    again, _ = rt.stamp_pdf_total(out)  # läggs inte till två gånger
    assert again == out
    # Ingen totalrad i PDF:en → orörd.
    plain = html_to_pdf("<html><body><p>Hej</p></body></html>")
    assert rt.stamp_pdf_total(plain) == (plain, None)


def test_pipeline_total_wins_over_ai_zero():
    from types import SimpleNamespace

    from app.services.pipeline import _apply_receipt_total

    analysis = SimpleNamespace(amount=0.0, currency="SEK")
    _apply_receipt_total(analysis, rt.parse_total(None, SKANE_HTML),
                         SimpleNamespace(message_id="m1"))
    assert analysis.amount == 150.0 and analysis.currency == "SEK"


def test_is_skanetrafiken():
    assert rt.is_skanetrafiken("noreply@skanetrafiken.se")
    assert rt.is_skanetrafiken(None, "Kvitto från Skånetrafiken")
    assert not rt.is_skanetrafiken("noreply@arlandaexpress.se")


# ================================================================ Del 1: reservväg i utkastet


def _skane(db, tx="5594772", bid="2493711", amount=150.0, line_amount=14.01):
    from app.models import CurrencyRate

    line = _line(db, bid, date(2026, 11, 5), line_amount, desc=SKANE)
    m = _receipt(db, date(2026, 11, 5), "Skånetrafiken", amount, "SEK", waiting=False,
                 category="Kollektivtrafik", bezala_transaction_id=tx,
                 ai_description_en="Skånetrafiken single ticket CPH Airport Kastrup to "
                                   "Malmö C, 5 November 2026")
    line.bezala_transaction_id = tx
    if not db.query(CurrencyRate).filter_by(date="2026-11-05").first():
        db.add(CurrencyRate(date="2026-11-05", from_currency="SEK", to_currency="EUR",
                            rate=0.0934))
    db.commit()
    return line, m


def _bezala_zero(tx="5594772", **kw):
    """Så som Bezala läste Skånetrafiken: 0 SEK, men kortradens 14,01 EUR."""
    base = dict(connected_to_bill_line=True, bill_id=339638, date="2026-11-05",
                original_amount="0.0", original_amount_currency="SEK",
                billed_amount="14.01", billed_amount_currency="EUR",
                description="x", vat_lines=[{"id": 1, "expense_account_id": 67100,
                                             "vat_id": 1}])
    base.update(kw)
    return _draft(tx, **base)


def test_bezala_read_zero_is_corrected(bound_db):
    line, m = _skane(bound_db)
    plan = c60.plan_fill(bound_db, m, line, _bezala_zero(), accounts=ACCOUNTS)
    assert plan["changes"]["original_amount"] == "150.00"
    assert plan["changes"]["original_amount_currency"] == "SEK"


def test_bezala_read_zero_is_not_verified(bound_db):
    line, m = _skane(bound_db)
    v = c60.verify_coupling(_bezala_zero(), line)
    assert v["ok"] is False and any("0" in p for p in v["problems"])


def test_bezala_read_right_amount_nothing_changes(bound_db):
    line, m = _skane(bound_db)
    tx = _bezala_zero(original_amount="150.00")
    plan = c60.plan_fill(bound_db, m, line, tx, accounts=ACCOUNTS)
    assert "original_amount" not in plan["changes"]
    assert c60.verify_coupling(tx, line)["ok"] is True


def test_receipt_amount_zero_is_never_guessed(bound_db):
    line, m = _skane(bound_db, amount=0.0)
    plan = c60.plan_fill(bound_db, m, line, _bezala_zero(), accounts=ACCOUNTS)
    assert "original_amount" not in plan["changes"]
    assert any("gissas aldrig" in s for s in plan["skipped"])


def test_zero_is_fixed_in_normal_coupling_flow_and_shown(bound_db, monkeypatch):
    """Vanliga kopplingsflödet: verify_and_update rättar 0 → 150 SEK (flaggan på)."""
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    line, m = _skane(bound_db)
    client = FakeBezala({"5594772": _bezala_zero()})
    out = c60.verify_and_update(bound_db, "5594772", client=client)
    assert client.puts and client.puts[0][1]["original_amount"] == "150.00"
    assert "state" not in client.puts[0][1]
    assert out["verified"] is True
    bound_db.refresh(line)
    assert line.status == "resolved"
    fix = c60.amount_fix(c60.writes_for(bound_db, "5594772"))
    assert fix["before"] == "0.00 SEK" and fix["after"] == "150.00 SEK"
    assert "rättade beloppet" in fix["text"]


def test_zero_not_written_when_flag_off(bound_db):
    line, m = _skane(bound_db)
    client = FakeBezala({"5594772": _bezala_zero()})
    out = c60.verify_and_update(bound_db, "5594772", client=client)
    assert client.puts == [] and out["verified"] is False


@pytest.mark.parametrize("state", ["reviewing", "accounted"])
def test_skane_non_draft_is_refused(bound_db, monkeypatch, state):
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    line, m = _skane(bound_db)
    client = FakeBezala({"5594772": _bezala_zero(state=state)})
    out = c60.fill_drafts(bound_db, ["5594772"], client=client, apply=True)
    assert client.puts == []
    assert "Inte ett utkast" in out["items"][0]["skipped"][0]


def test_amount_fix_none_without_amount_write():
    assert c60.amount_fix([{"outcome": "ok", "fields": {"description": "x"}}]) is None
    assert c60.amount_fix([]) is None


# ================================================================ Del 2: km, Vehicle, passagerare


@pytest.fixture
def known_fields(monkeypatch):
    """Som om Mikko visat fältnamnen (DevTools). I koden är de None."""
    monkeypatch.setattr(c60, "DISTANCE_FIELDS",
                        {"km": "distance_km", "vehicle": "vehicle", "passengers": "passengers"})
    monkeypatch.setattr(c60, "VEHICLE_VALUES", {"train": "train"})


def _train_receipt(db, km=39.0, **leg):
    line, m = _skane(db)
    _trip_leg(db, seq=1, leg_date=date(2026, 11, 5), mode="train", from_place="CPH",
              to_place="Malmö C", km=km, source="route_table",
              receipt_message_id=m.message_id, **leg)
    return line, m


def test_field_names_unknown_in_code():
    assert c60.distance_field_names() == set()
    assert all(v is None for v in c60.DISTANCE_FIELDS.values())


def test_distance_written_on_empty_draft(bound_db, monkeypatch, known_fields):
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    line, m = _train_receipt(bound_db)
    client = FakeBezala({"5594772": _bezala_zero(original_amount="150.00", emissions="6.07")})
    dry = c60.fill_drafts(bound_db, ["5594772"], client=client)
    assert client.puts == []
    assert dry["items"][0]["changes"] == {"distance_km": 39.0, "vehicle": "train",
                                          "passengers": 1}
    out = c60.fill_drafts(bound_db, ["5594772"], client=client, apply=True)
    assert client.puts[0][1] == {"distance_km": 39.0, "vehicle": "train", "passengers": 1}
    assert out["items"][0]["result"] == "Skrivet och bekräftat"
    assert out["items"][0]["distance"]["bezala_kg_per_km"] == round(6.07 / 39.0, 3)


def test_mikkos_distance_values_are_kept(bound_db, known_fields):
    line, m = _train_receipt(bound_db)
    tx = _bezala_zero(original_amount="150.00", distance_km=45, vehicle="bus", passengers=2)
    plan = c60.plan_fill(bound_db, m, line, tx, accounts=ACCOUNTS)
    assert not {"distance_km", "vehicle", "passengers"} & set(plan["changes"])
    assert sum("behålls" in s for s in plan["skipped"]) >= 3


def test_leg_without_km_leaves_km_empty(bound_db, known_fields):
    line, m = _train_receipt(bound_db, km=None, km_reason="direction_unknown")
    plan = c60.plan_distance_write(bound_db, m, _bezala_zero(original_amount="150.00"))
    assert "distance_km" not in plan["changes"]
    assert "km saknas" in plan["notes"]


def test_estimated_km_leaves_km_empty(bound_db, known_fields):
    line, m = _train_receipt(bound_db, is_estimate=True)
    plan = c60.plan_distance_write(bound_db, m, _bezala_zero(original_amount="150.00"))
    assert "distance_km" not in plan["changes"] and "km saknas" in plan["notes"]


def test_flight_without_km_writes_nothing(bound_db, known_fields):
    line, m = _finnair(bound_db)
    _trip_leg(bound_db, seq=1, leg_date=date(2026, 11, 3), mode="flight", from_place="HEL",
              to_place="ARN", km=None, source="airport_table", flight_no="AY823")
    plan = c60.plan_distance_write(bound_db, m, _draft("5594776"))
    assert plan["changes"] == {}


@pytest.mark.parametrize("state", ["reviewing", "accounted", None])
def test_distance_non_draft_refused(bound_db, known_fields, state):
    line, m = _train_receipt(bound_db)
    plan = c60.plan_distance_write(bound_db, m, _bezala_zero(state=state))
    assert plan["changes"] == {} and "Inte ett utkast" in plan["skipped"][0]


def test_distance_fields_only_allowed_when_known(known_fields):
    from app.services import bezala_write_guard as guard

    allowed = guard.DRAFT_FILL_FIELDS | c60.distance_field_names()
    guard.check_fields({"distance_km": 1, "vehicle": "train", "passengers": 1}, allowed)
    with pytest.raises(guard.BezalaWriteBlocked):
        guard.check_fields({"distance_km": 1}, guard.DRAFT_FILL_FIELDS)


def test_all_write_flags_default_false(monkeypatch):
    from app.services import bezala_write_guard as guard

    for name in ("AUTO_COUPLE_ENABLED", "BEZALA_METADATA_PUT_ENABLED", "NETVISOR_ENABLED",
                 "BEZALA_DRAFT_FILL_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert guard.env_auto_couple_enabled() is False
    assert guard.metadata_put_enabled() is False
    assert c60.draft_fill_enabled() is False


# ================================================================ PostgreSQL


def test_postgres_skane_zero_fixed_and_logged(pg_url, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base
    from app.models import BezalaWriteLog
    from app.services import bezala_write_guard as guard

    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    @contextmanager
    def _session(_db=None):
        yield s

    monkeypatch.setattr(guard, "_session", _session)
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    try:
        line, m = _skane(s)
        client = FakeBezala({"5594772": _bezala_zero()})
        out = c60.verify_and_update(s, "5594772", client=client)
        assert out["verified"] is True
        log = s.query(BezalaWriteLog).filter_by(operation="draft_fill").one()
        assert log.state_before["values"]["original_amount"] == "0.0"
        assert log.state_after["values"]["original_amount"] == "150.00"
        fix = c60.amount_fix(c60.writes_for(s, "5594772"))
        assert fix["after"] == "150.00 SEK"
        # Icke-utkast avvisas även i Postgres.
        client.txs["5594772"]["state"] = "reviewing"
        client.txs["5594772"]["original_amount"] = "0.0"
        res = c60.fill_drafts(s, ["5594772"], client=client, apply=True)
        assert "Inte ett utkast" in res["items"][0]["skipped"][0]
        assert len(client.puts) == 1
    finally:
        s.close()
        engine.dispose()
