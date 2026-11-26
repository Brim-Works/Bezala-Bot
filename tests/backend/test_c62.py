"""C62 — Sträcka (km), Vehicle och Antal passagerare (Bezalas ConditionalField)."""

from __future__ import annotations

import os

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

from app.services import c62_conditional as c62  # noqa: E402


class _Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text
        self.headers = {"content-type": "application/json" if data is not None else "text/html"}

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class ProbeClient:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get_raw(self, path):
        self.calls.append(path)
        return self.routes.get(path, _Resp(404, text="Not found"))

    def close(self):
        pass


def test_probe_finds_conditional_values_and_only_reads():
    cv = [{"id": 266932, "field_id": 3, "value": "1"}, {"id": 1, "field_id": 1, "value": "Train"}]
    client = ProbeClient({
        "/transactions/5594772?include=conditional_values": _Resp(200, {"id": 5594772}),
        "/receipts/5594772": _Resp(200, {"id": 5594772, "conditional_values": cv}),
    })
    out = c62.probe_reads("5594772", client=client)
    assert out["found_in"] == ["/receipts/5594772"]
    assert len(client.calls) == len(c62.PROBE_PATHS)
    first = out["results"][0]
    assert first["status"] == 200 and "conditional" not in first


def test_get_raw_rejects_odd_paths():
    import pytest

    from app.services.bezala_client import BezalaClient, BezalaError

    c = BezalaClient.__new__(BezalaClient)
    with pytest.raises(BezalaError):
        c.get_raw("https://evil.example/x")
    with pytest.raises(BezalaError):
        c.get_raw("/../x")


# ================================================================ Del 2: skriva fälten

from datetime import date  # noqa: E402

import pytest  # noqa: E402

from app.services import c60_draft as c60  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c50 import bound_db  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401
from tests.backend.test_c60 import (  # noqa: E402
    ACCOUNTS, FakeBezala, _finnair, _no_bezala, _trip_leg,  # noqa: F401
)
from tests.backend.test_c61 import _bezala_zero, _skane  # noqa: E402


class FakeCV(FakeBezala):
    """FakeBezala + conditional_values (GET /transactions/<id>/conditional_values,
    PUT conditional_values_attributes uppdaterar rad med id, annars ny rad)."""

    def __init__(self, txs, cv=None, emissions_after=None):
        super().__init__(txs)
        self.cv = {str(k): [dict(r) for r in v] for k, v in (cv or {}).items()}
        self.next_id = 300000
        self.emissions_after = emissions_after

    def get_raw(self, path):
        tx_id = path.split("/")[2]
        assert path == f"/transactions/{tx_id}/conditional_values"
        return _Resp(200, [dict(r) for r in self.cv.get(tx_id, [])])

    def _put_transaction_unchecked(self, tx_id, fields):
        fields = dict(fields)
        rows = fields.pop(c60.CV_KEY, None)
        if rows is not None:
            self.puts.append((str(tx_id), {c60.CV_KEY: rows}))
            cur = self.cv.setdefault(str(tx_id), [])
            for r in rows:
                if r.get("id") is not None:
                    hit = [x for x in cur if x["id"] == r["id"]]
                    assert hit, "okänt id"
                    hit[0].update(r)
                else:
                    self.next_id += 1
                    cur.append({**r, "id": self.next_id})
            if self.emissions_after:
                self.txs[str(tx_id)]["emissions"] = self.emissions_after
            if not fields:
                return {"status_code": 202}
        return super()._put_transaction_unchecked(tx_id, fields)


def _cv_row(tx, fid, value, rid):
    return {"id": rid, "expense_id": int(tx), "expense_type": "Receipt", "field_id": fid,
            "field_type": "ExpenseCategory::ConditionalField", "value": value}


PASSENGER_DEFAULT = {"5594772": [_cv_row("5594772", 3, "1", 266932)]}


def _train(db, km=30.4, **leg):
    line, m = _skane(db)
    _trip_leg(db, seq=1, leg_date=date(2026, 11, 5), mode="train", from_place="CPH",
              to_place="Malmö C", km=km, source="route_table",
              receipt_message_id=m.message_id, **leg)
    return line, m


def _filled_tx(**kw):
    return _bezala_zero(original_amount="150.00", emissions="6.07", **kw)


def test_read_values_parses_rows():
    client = FakeCV({}, {"5594772": [_cv_row("5594772", 2, "30.4", 266942),
                                     _cv_row("5594772", 1, "Train", 266943),
                                     _cv_row("5594772", 3, "1", 266932)]})
    v = c62.read_values(client, "5594772")
    assert c62.value_of(v, "vehicle") == "Train" and c62.value_of(v, "km") == "30.4"
    assert v["passengers"] == {"id": 266932, "value": "1"}


def test_read_values_none_when_unreadable():
    class C:
        def get_raw(self, path):
            return _Resp(404, text="nope")

    assert c62.read_values(C(), "1") is None
    assert c62.read_values(object(), "1") is None


def test_train_is_filled(bound_db, monkeypatch):
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    line, m = _train(bound_db)
    client = FakeCV({"5594772": _filled_tx()}, PASSENGER_DEFAULT, emissions_after="1.1")
    dry = c60.fill_drafts(bound_db, ["5594772"], client=client)
    assert client.puts == []
    rows = dry["items"][0]["changes"][c60.CV_KEY]
    assert {r["field_id"]: r["value"] for r in rows} == {1: "Train", 2: "30.4"}
    assert all(r["field_type"] == "ExpenseCategory::ConditionalField"
               and r["expense_id"] == 5594772 and r["expense_type"] == "Receipt"
               for r in rows)
    out = c60.fill_drafts(bound_db, ["5594772"], client=client, apply=True)
    item = out["items"][0]
    assert item["result"] == "Skrivet och bekräftat", item
    assert "state" not in client.puts[0][1]
    assert item["emissions_before"] == "6.07" and item["emissions_after"] == "1.1"
    # Inga dubbletter: en rad per fält.
    assert sorted(r["field_id"] for r in client.cv["5594772"]) == [1, 2, 3]
    fix = c60.distance_fix(c60.writes_for(bound_db, "5594772"))
    assert {f["field"]: (f["before"], f["after"]) for f in fix["fields"]} == {
        "vehicle": (None, "Train"), "km": (None, "30.4")}
    assert fix["emissions_before"] == "6.07" and fix["emissions_after"] == "1.1"


def test_existing_passenger_row_is_updated_not_duplicated(bound_db, monkeypatch):
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    line, m = _train(bound_db)
    client = FakeCV({"5594772": _filled_tx()},
                    {"5594772": [_cv_row("5594772", 3, "", 266932)]})
    plan = c60.plan_fill(bound_db, m, line, client.txs["5594772"], accounts=ACCOUNTS,
                         conditional=c62.read_values(client, "5594772"))
    pas = [r for r in plan["changes"][c60.CV_KEY] if r["field_id"] == 3][0]
    assert pas["id"] == 266932 and pas["value"] == "1"
    c60.apply_fill(client, "5594772", plan, db=bound_db)
    assert [r["field_id"] for r in client.cv["5594772"]].count(3) == 1


def test_flight_is_filled_large_and_small(bound_db):
    line, m = _finnair(bound_db)
    _trip_leg(bound_db, seq=1, leg_date=date(2026, 11, 3), mode="flight", from_place="HEL",
              to_place="ARN", km=396.0, source="airport_table", flight_no="AY823")
    _trip_leg(bound_db, seq=2, leg_date=date(2026, 11, 3), mode="flight", from_place="ARN",
              to_place="HEL", km=396.0, source="airport_table", flight_no="AY818")
    from tests.backend.test_c60 import _draft

    p = c62.plan(bound_db, m, _draft("5594776"), c62.parse_values([]))
    assert p["fields"] == {"vehicle": "Airplane: Small ~ 700km/h", "km": "792",
                           "passengers": "1"}


def test_flight_long_leg_is_large(bound_db):
    line, m = _finnair(bound_db)
    _trip_leg(bound_db, seq=1, leg_date=date(2026, 11, 3), mode="flight", from_place="HEL",
              to_place="CPH", km=885.0, source="airport_table", flight_no="AY823")
    from tests.backend.test_c60 import _draft

    p = c62.plan(bound_db, m, _draft("5594776"), c62.parse_values([]))
    assert p["fields"]["vehicle"] == "Airplane: Large ~ 900km/h"


@pytest.mark.parametrize("leg", [dict(km=None, km_reason="direction_unknown"),
                                 dict(is_estimate=True)])
def test_leg_without_km_leaves_km_and_vehicle_empty(bound_db, leg):
    line, m = _train(bound_db, **leg)
    p = c62.plan(bound_db, m, _filled_tx(), c62.parse_values([]))
    assert "km" not in p["fields"] and "vehicle" not in p["fields"]
    assert "km saknas" in p["notes"]


def test_mikkos_values_are_never_overwritten(bound_db):
    line, m = _train(bound_db)
    vals = c62.parse_values([_cv_row("5594772", 1, "Bus", 1), _cv_row("5594772", 2, "45", 2),
                             _cv_row("5594772", 3, "2", 3)])
    p = c62.plan(bound_db, m, _filled_tx(), vals)
    assert p["fields"] == {} and p["rows"] == []
    assert sum("behålls" in s for s in p["skipped"]) == 3


@pytest.mark.parametrize("state", ["reviewing", "accounted", None])
def test_non_draft_is_refused(bound_db, monkeypatch, state):
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")
    line, m = _train(bound_db)
    client = FakeCV({"5594772": _filled_tx(state=state)}, PASSENGER_DEFAULT)
    out = c60.fill_drafts(bound_db, ["5594772"], client=client, apply=True)
    assert client.puts == []
    assert "Inte ett utkast" in out["items"][0]["skipped"][0]


def test_unreadable_values_write_nothing(bound_db):
    line, m = _train(bound_db)
    p = c62.plan(bound_db, m, _filled_tx(), None)
    assert p["rows"] == [] and "kan inte läsas" in p["skipped"][0]


def test_no_leg_writes_nothing(bound_db):
    line, m = _skane(bound_db)
    p = c62.plan(bound_db, m, _filled_tx(), c62.parse_values([]))
    assert p["rows"] == [] and "Inget ben" in p["notes"][0]


def test_flag_off_writes_nothing(bound_db, monkeypatch):
    monkeypatch.delenv("BEZALA_DRAFT_FILL_ENABLED", raising=False)
    line, m = _train(bound_db)
    client = FakeCV({"5594772": _filled_tx()}, PASSENGER_DEFAULT)
    out = c60.fill_drafts(bound_db, ["5594772"], client=client, apply=True)
    assert client.puts == [] and "false" in out["items"][0]["result"]


def test_default_dry_run_includes_all_bezala_drafts(bound_db):
    line, m = _train(bound_db)

    class C(FakeCV):
        def list_transactions(self):
            return [{"id": 5594772, "state": "draft"}, {"id": 1, "state": "accounted"},
                    {"id": 2, "state": "draft"}]

    client = C({"5594772": _filled_tx()}, PASSENGER_DEFAULT)
    out = c60.fill_drafts(bound_db, client=client, days=1)
    ids = [i["transaction_id"] for i in out["items"]]
    assert ids == ["5594772", "2"] and client.puts == []


def test_vehicle_options_match_bezala():
    assert set(c62.MODE_TO_VEHICLE.values()) <= set(c62.VEHICLE_OPTIONS)
    assert {c62.AIRPLANE_SMALL, c62.AIRPLANE_LARGE} <= set(c62.VEHICLE_OPTIONS)
    assert c62.km_text(30.4) == "30.4" and c62.km_text(39.0) == "39"


def test_all_write_flags_default_false(monkeypatch):
    from app.services import bezala_write_guard as guard

    for name in ("AUTO_COUPLE_ENABLED", "BEZALA_METADATA_PUT_ENABLED", "NETVISOR_ENABLED",
                 "BEZALA_DRAFT_FILL_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    assert guard.env_auto_couple_enabled() is False
    assert guard.metadata_put_enabled() is False
    assert c60.draft_fill_enabled() is False


# ================================================================ PostgreSQL


def test_postgres_train_filled_and_logged(pg_url, monkeypatch):  # noqa: F811
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
        line, m = _train(s)
        client = FakeCV({"5594772": _filled_tx()}, PASSENGER_DEFAULT, emissions_after="1.1")
        out = c60.fill_drafts(s, ["5594772"], client=client, apply=True)
        assert out["items"][0]["result"] == "Skrivet och bekräftat"
        log = s.query(BezalaWriteLog).filter_by(operation="draft_fill").one()
        cv_after = log.state_after["values"]["conditional"]
        assert cv_after["vehicle"]["value"] == "Train" and cv_after["km"]["value"] == "30.4"
        assert log.state_before["values"]["conditional"]["vehicle"] is None
        fix = c60.distance_fix(c60.writes_for(s, "5594772"))
        assert fix["emissions_after"] == "1.1"
        # Andra körningen: allt ifyllt → inget skrivs.
        again = c60.fill_drafts(s, ["5594772"], client=client, apply=True)
        assert c60.CV_KEY not in again["items"][0]["changes"]
        # Icke-utkast avvisas även i Postgres.
        client.txs["5594772"]["state"] = "reviewing"
        res = c60.fill_drafts(s, ["5594772"], client=client, apply=True)
        assert "Inte ett utkast" in res["items"][0]["skipped"][0]
        assert len(client.puts) == 1
    finally:
        s.close()
        engine.dispose()


def test_change_fee_with_borrowed_flights_writes_nothing(bound_db):
    """Ändringsavgift utan egna flygsegment: flygen hör till biljetten."""
    from tests.backend.test_c47 import _receipt
    from tests.backend.test_c60 import _draft

    ticket = _receipt(bound_db, date(2026, 11, 16), "Finnair", 540.79, waiting=False,
                      category="Flyg", booking_ref="8LXVP2",
                      flight_segments=[{"flight_no": "AY 803", "origin": "HEL", "dest": "ARN",
                                        "date": "2026-11-24"}])
    fee = _receipt(bound_db, date(2026, 11, 22), "Finnair", 32.92, waiting=False,
                   category="Flyg", booking_ref="8LXVP2", bezala_transaction_id="5594766")
    _trip_leg(bound_db, seq=1, leg_date=date(2026, 11, 24), mode="flight", from_place="HEL",
              to_place="ARN", km=399.0, source="airport_table", flight_no="AY803")
    p = c62.plan(bound_db, fee, _draft("5594766"), c62.parse_values([]))
    assert p["rows"] == [] and "annat kvitto" in p["notes"][0]
    p2 = c62.plan(bound_db, ticket, _draft("5594780"), c62.parse_values([]))
    assert p2["fields"]["km"] == "399"
