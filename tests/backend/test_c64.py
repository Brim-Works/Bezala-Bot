"""C64 — automatisk ifyllnad av nya utkast (konto, moms, beskrivning, km, återbetalningar).

Exemplen följer prod 2026-11-26 (taxi Stockholm, Anthropic-återbetalningar
5596175/5596176). Inga nycklar, bara id och belopp.
"""

from __future__ import annotations

import os
from datetime import date, datetime

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("ANTHROPIC_API_KEY", "")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from app.services import bezala_write_guard as guard  # noqa: E402
from app.services import c60_draft as c60  # noqa: E402
from app.services import c64_autofill as c64  # noqa: E402
from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c47 import _line, _receipt  # noqa: E402
from tests.backend.test_c50 import bound_db  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401

ACCOUNTS = [
    {"id": 67100, "name": "Matkaliput"},
    {"id": 67101, "name": "Taksikulut"},
    {"id": 67102, "name": "Hotelli- ym. majoitus"},
    {"id": 67113, "name": "Paikoituskulut"},
    {"id": 166648, "name": "AI työkalut"},
    {"id": 67121, "name": "Tiliöimättömät ostolaskut"},
]
TAXI_CARD = "MIKKO KEINONEN: TAXI STOCKHOLM, STOCKHOLM, SE 12.40 EUR"
ANTHROPIC_CARD = "MIKKO KEINONEN: ANTHROPIC, SAN FRANCISCO, US -22.95 EUR"
CREATED = "2026-11-26T10:00:00Z"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    from app.services import c50_accounts

    monkeypatch.setattr(c50_accounts, "reference", lambda: (ACCOUNTS, []))
    monkeypatch.setattr(c60, "schedule_verification", lambda *a, **k: None)
    for flag in ("AUTO_COUPLE_ENABLED", "BEZALA_METADATA_PUT_ENABLED", "NETVISOR_ENABLED"):
        monkeypatch.delenv(flag, raising=False)
    monkeypatch.setenv("BEZALA_DRAFT_FILL_ENABLED", "true")


@pytest.fixture
def on(bound_db):  # noqa: F811
    c64.set_auto_fill(bound_db, True, "test")
    return bound_db


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class Fake:
    """Bezala: utkast per id, conditional_values, PUT med id/_destroy på
    kostnadsrader. Varje PUT flyttar updated_at (som Bezala)."""

    def __init__(self, txs: dict, cv: dict | None = None, ignore_puts: bool = False):
        self.txs = {str(k): _copy(t) for k, t in txs.items()}
        self.cv = {str(k): [dict(r) for r in v] for k, v in (cv or {}).items()}
        self.puts: list = []
        self.gets = 0
        self.ignore_puts = ignore_puts
        self.next_id = 400000

    def get_transaction(self, tx_id):
        self.gets += 1
        return _copy(self.txs[str(tx_id)])

    def get_raw(self, path):
        tx_id = path.split("/")[2]
        return _Resp(200, [dict(r) for r in self.cv.get(tx_id, [])])

    def _put_transaction_unchecked(self, tx_id, fields):
        self.puts.append((str(tx_id), _copy(fields)))
        if self.ignore_puts:
            return {"status_code": 200}
        t = self.txs[str(tx_id)]
        for k, val in fields.items():
            if k == "vat_lines_attributes":
                lines = [dict(x) for x in t.get("vat_lines") or []]
                for e in val:
                    hit = [x for x in lines if x.get("id") == e.get("id")]
                    if e.get("_destroy"):
                        lines = [x for x in lines if x.get("id") != e.get("id")]
                    elif hit:
                        hit[0].update({kk: vv for kk, vv in e.items() if kk != "id"})
                    else:
                        lines.append(dict(e))
                t["vat_lines"] = lines
            elif k == c60.CV_KEY:
                cur = self.cv.setdefault(str(tx_id), [])
                for r in val:
                    hit = [x for x in cur if x.get("id") == r.get("id")] if r.get("id") else []
                    if hit:
                        hit[0].update(r)
                    else:
                        self.next_id += 1
                        cur.append({**r, "id": self.next_id})
            else:
                t[k] = val
        t["updated_at"] = "2026-11-26T10:02:00Z"
        return {"status_code": 200}

    def close(self):
        pass


def _copy(x):
    import copy

    return copy.deepcopy(x)


def _tx(tx_id, amount, *, lines, description="", updated="2026-11-26T10:00:20Z",
        state="draft", connected=True, currency="EUR"):
    return {"id": int(tx_id), "state": state, "description": description,
            "date": "2026-11-26", "original_amount": f"{amount:.2f}",
            "original_amount_currency": currency, "billed_amount": f"{amount:.2f}",
            "billed_amount_currency": currency, "connected_to_bill_line": connected,
            "bill_id": 1 if connected else None, "vat_lines": lines,
            "created_at": CREATED, "updated_at": updated}


def _vl(lid, acc=67121, vat=1913, taxable="12.40", cc=None):
    return {"id": lid, "expense_account_id": acc, "vat_id": vat, "taxable": taxable,
            "tax_percentage": "0.0", "cost_center_ids": cc or []}


def _taxi(s, tx="5597345", bid="2600001", **kw):
    line = _line(s, bid, date(2026, 11, 26), 12.40, desc=TAXI_CARD)
    m = _receipt(s, date(2026, 11, 26), "Taxi Stockholm", 12.40, waiting=False,
                 category="Taxi", bezala_transaction_id=tx, matched_at=datetime(2026, 11, 26, 10),
                 bezala_upload_status=c60.STATUS_UNVERIFIED, upload_source="manual_upload",
                 vat_info={"lines": [{"rate": 6.0}], "purchase_country": "SE",
                           "seller_country": "SE", "document": "receipt"},
                 ride_info={"km": 1.1, "start": "14:05", "end": "14:12", "city": "Stockholm"},
                 summary="Taxikvitto Stockholm, Färd: 1.10 km", **kw)
    line.bezala_transaction_id = tx
    line.status = c60.STATUS_UNVERIFIED
    s.commit()
    return line, m


def _anthropic(s, tx="5596175", bid="2600175"):
    line = _line(s, bid, date(2026, 11, 20), -22.95, desc=ANTHROPIC_CARD)
    m = _receipt(s, date(2026, 11, 20), "Anthropic", -22.95, waiting=False,
                 category="AI-verktyg", bezala_transaction_id=tx,
                 matched_at=datetime(2026, 11, 26, 9), bezala_upload_status="success",
                 subject="Your refund from Anthropic",
                 ai_description_en="Anthropic Claude subscription refund, 20 November 2026")
    line.bezala_transaction_id = tx
    s.commit()
    return line, m


def _bezala_after_creation(tx, amount, lines, **kw):
    """Utkastet som Bezala skapar: platshållarkonto 4060 och momskod 1913."""
    return _tx(tx, amount, lines=lines, **kw)


# ================================================================ Del 1


def test_fill_runs_after_verified_coupling_not_before(on):
    line, m = _taxi(on)
    client = Fake({"5597345": _bezala_after_creation(
        "5597345", 12.40, [_vl(9001)], connected=False)})
    out = c60.verify_and_update(on, "5597345", client=client)
    assert out["verified"] is False
    assert "auto_fill" not in out
    assert all("description" not in f for _, f in client.puts)  # inget före verifieringen
    on.refresh(m)
    assert m.draft_fill_status is None
    # Bezala kopplar - nästa läsning verifierar och fyller i.
    client.txs["5597345"].update(connected_to_bill_line=True, bill_id=339700)
    out = c60.verify_and_update(on, "5597345", client=client, attempt=2)
    assert out["verified"] is True
    assert out["auto_fill"]["status"] == c64.STATUS_DONE
    t = client.txs["5597345"]
    assert t["description"] == "Taxi ride within Stockholm, 1.1 km, 26 November 2026"
    assert t["vat_lines"][0]["expense_account_id"] == 67101  # Taksikulut
    assert t["vat_lines"][0]["vat_id"] == 1  # köpt i Sverige → 0 %
    assert t["state"] == "draft"


def test_manual_upload_path_does_not_fill_when_switch_off(bound_db):  # noqa: F811
    """Uppladdning med kortrad: verifieringen startar, men ifyllnaden bara
    när Mikko sagt ja (c64_auto_fill)."""
    line, m = _taxi(bound_db)
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001)])})
    out = c60.verify_and_update(bound_db, "5597345", client=client, c64_only=True)
    assert out["verified"] is True
    assert client.puts == []
    bound_db.refresh(line)
    assert line.status == "resolved"


def test_only_drafts_the_bot_created(on):
    line, m = _taxi(on)
    m.matched_at = None  # inte kopplat av boten
    on.commit()
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001)])})
    out = c60.verify_and_update(on, "5597345", client=client)
    assert out["auto_fill"]["status"] == c64.STATUS_SKIPPED
    assert client.puts == []


# ================================================================ Del 2


def test_untouched_draft_gets_rule_account_and_vat(on):
    _taxi(on)
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001, vat=1914)])})
    c60.verify_and_update(on, "5597345", client=client)
    ln = client.txs["5597345"]["vat_lines"][0]
    assert (ln["expense_account_id"], ln["vat_id"], ln["tax_percentage"]) == (67101, 1, "0.0")
    assert ln["cost_center_ids"] == [c60.COST_CENTER_ID]


def test_touched_draft_keeps_mikkos_values_only_empty_filled(on):
    _taxi(on)
    # Mikko ändrade momsen 5 min efter skapandet; beskrivningen är tom.
    client = Fake({"5597345": _bezala_after_creation(
        "5597345", 12.40, [_vl(9001, acc=67110, vat=864)], updated="2026-11-26T10:05:00Z")})
    out = c60.verify_and_update(on, "5597345", client=client)
    ln = client.txs["5597345"]["vat_lines"][0]
    assert (ln["expense_account_id"], ln["vat_id"]) == (67110, 864)  # Mikkos val
    assert client.txs["5597345"]["description"].startswith("Taxi ride within Stockholm")
    plan = out["auto_fill"]["fill"]
    assert plan["untouched"] is False
    assert any("behålls" in x for x in plan["skipped"])


def test_touched_draft_keeps_bezala_default_vat(on):
    """Rört utkast: momskod 1913 kan vara Mikkos val - rörs inte."""
    _taxi(on)
    client = Fake({"5597345": _bezala_after_creation(
        "5597345", 12.40, [_vl(9001)], updated="2026-11-26T10:03:00Z")})
    c60.verify_and_update(on, "5597345", client=client)
    assert client.txs["5597345"]["vat_lines"][0]["vat_id"] == 1913


def test_untouched_needs_bots_own_read():
    t = _tx("1", 1.0, lines=[])
    assert c64.is_untouched(t, None)[0] is False
    seen = {"tx": "1", "created_at": CREATED, "base_updated_at": "2026-11-26T10:00:30Z",
            "last_updated_at": "2026-11-26T10:00:20Z", "fingerprint": c64.fingerprint(t)}
    seen["last_updated_at"] = t["updated_at"]
    assert c64.is_untouched(t, seen)[0] is True
    t2 = dict(t, description="Mikko skrev")
    assert c64.is_untouched(t2, seen) == (False, "fält ändrade av någon annan än boten")
    late = dict(seen, base_updated_at="2026-11-26T10:01:30Z")
    assert c64.is_untouched(t, late)[0] is False


def test_other_state_is_refused(on):
    _taxi(on)
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001)],
                                                     state="reviewing")})
    out = c60.verify_and_update(on, "5597345", client=client)
    assert client.puts == []
    assert "auto_fill" not in out or out["auto_fill"]["status"] == c64.STATUS_SKIPPED
    plan = c64.plan_auto_fill(on, c60._row_for_tx(on, "5597345"), None,
                              client.txs["5597345"], accounts=ACCOUNTS)
    assert plan["changes"] == {} and "Inte ett utkast" in plan["skipped"][0]


# ================================================================ Del 3


def _anthropic_before():
    """5596175 som det såg ut före Mikkos handpåläggning."""
    return _bezala_after_creation("5596175", 22.95, [
        _vl(7001, vat=1914, taxable="22.95"), _vl(7002, vat=1914, taxable="0.0")])


def test_refund_negative_sign_prefix_one_cost_row(on):
    _anthropic(on)
    client = Fake({"5596175": _anthropic_before()})
    out = c60.verify_and_update(on, "5596175", client=client)
    assert out["verified"] is True
    t = client.txs["5596175"]
    assert t["original_amount"] == "-22.95"
    assert t["description"] == "Refund - Anthropic Claude subscription refund, 20 November 2026"
    assert len(t["vat_lines"]) == 1
    ln = t["vat_lines"][0]
    assert (ln["id"], ln["taxable"], ln["expense_account_id"], ln["vat_id"]) == \
        (7001, "-22.95", 166648, 1)  # OSS: 0 % + flagga
    assert out["auto_fill"]["status"] == c64.STATUS_DONE
    assert "Supplier charged Finnish VAT - check buyer VAT ID" in out["auto_fill"]["fill"]["flags"]


def test_c60_fill_never_writes_positive_amount_on_negative_line(bound_db):  # noqa: F811
    line, m = _anthropic(bound_db)
    t = _tx("5596175", 0.0, lines=[])
    t["billed_amount"] = None
    changes, _ = c60.plan_amount(bound_db, m, line, t)
    assert changes["original_amount"] == "-22.95"


def test_touched_refund_zero_row_left_alone(on):
    _anthropic(on)
    before = _anthropic_before()
    before["updated_at"] = "2026-11-26T10:10:00Z"
    client = Fake({"5596175": before})
    out = c60.verify_and_update(on, "5596175", client=client)
    assert len(client.txs["5596175"]["vat_lines"]) == 2
    assert client.txs["5596175"]["original_amount"] == "22.95"
    assert any("rört" in x for x in out["auto_fill"]["fill"]["skipped"])


def test_dry_run_anthropic_refunds_before_mikko(bound_db, capsys):  # noqa: F811
    """Del 3.4: torrkörning för 5596175 och 5596176 som de såg ut före Mikkos
    handpåläggning. Skriver inget."""
    rows = []
    for tx, bid in (("5596175", "2600175"), ("5596176", "2600176")):
        line, m = _anthropic(bound_db, tx, bid)
        before = _anthropic_before()
        before["id"] = int(tx)
        before["vat_lines"] = [dict(x, id=x["id"] + int(tx) % 10) for x in before["vat_lines"]]
        plan = c64.plan_auto_fill(bound_db, m, line, before, accounts=ACCOUNTS,
                                  conditional=None, untouched=True, link=False)
        rows.append((tx, before, plan))
    for tx, before, plan in rows:
        ch = plan["changes"]
        assert ch["original_amount"] == "-22.95"
        assert ch["description"].startswith("Refund - ")
        assert sum(1 for e in ch["vat_lines_attributes"] if e.get("_destroy")) == 1
        print(f"{tx}: före belopp {before['original_amount']}, beskrivning tom, "
              f"{len(before['vat_lines'])} kostnadsrader, konto 67121, moms 1914 → "
              f"efter {ch['original_amount']}, '{ch['description']}', "
              f"konto {ch['vat_lines_attributes'][0]['expense_account_id']}, "
              f"moms vat_id {ch['vat_lines_attributes'][0]['vat_id']}, "
              f"taxable {ch['vat_lines_attributes'][0]['taxable']}, 0-raden tas bort")


# ================================================================ Del 2.4: en regel


def _case(s, kind):
    cards = {
        "taxi_se": ("Taxi Stockholm", "Taxi", TAXI_CARD, {"lines": [{"rate": 6.0}],
                                                          "purchase_country": "SE"}),
        "flight": ("Finnair", "Flyg", "MIKKO KEINONEN: FINNAIR, VANTAA, FI 446.77 EUR", None),
        "hotel_se": ("Clarion Hotel Arlanda", "Hotell",
                     "MIKKO KEINONEN: CLARION HOTEL ARLANDA, SIGTUNA, SE 150.00 EUR",
                     {"lines": [{"rate": 12.0}], "purchase_country": "SE"}),
        "train_no": ("Flytoget", "Tåg", "MIKKO KEINONEN: FLYTOGET, OSLO, NO 20.10 EUR", None),
        "moovy": ("Moovy", "Parkering", "MIKKO KEINONEN: MOOVY, VANTAA, FI 37.49 EUR", None),
        "anthropic": ("Anthropic", "AI-verktyg",
                      "MIKKO KEINONEN: ANTHROPIC, SAN FRANCISCO, US 22.95 EUR", None),
    }
    vendor, cat, card, vat = cards[kind]
    line = _line(s, f"27{len(kind)}{kind[:2]}", date(2026, 11, 20), 10.0, desc=card)
    m = _receipt(s, date(2026, 11, 20), vendor, 10.0, waiting=False, category=cat,
                 vat_info=vat, ai_description_en=f"{vendor}, 20 November 2026",
                 flight_segments=[{"flight_no": "AY 803", "origin": "HEL", "dest": "ARN",
                                   "date": "2026-11-20"}] if kind == "flight" else None)
    return line, m


@pytest.mark.parametrize("kind,vat_id,account", [
    ("taxi_se", 1, 67101), ("flight", 1, 67100), ("hotel_se", 1, 67102),
    ("train_no", 1, 67100), ("moovy", 864, 67113), ("anthropic", 1, 166648),
])
def test_upload_suggestion_and_fill_give_same_vat(bound_db, kind, vat_id, account):  # noqa: F811
    line, m = _case(bound_db, kind)
    rule = c64.draft_rule(bound_db, m, line)
    params = {"vat_lines_attributes": [{"taxable": "10.00", "tax_percentage": "0.255",
                                        "expense_account_id": 67110, "vat_code_id": 5}]}
    c64.apply_rule_to_params(params, rule)  # = förslaget vid uppladdningen (put_skipped)
    suggestion = params["vat_lines_attributes"][0]
    tx = _tx("5599000", 10.0, lines=[_vl(1, acc=None, vat=None, taxable="10.00")])
    plan = c64.plan_auto_fill(bound_db, m, line, tx, accounts=ACCOUNTS, untouched=True,
                              link=False)
    filled = plan["changes"]["vat_lines_attributes"][0]
    assert suggestion["vat_id"] == filled["vat_id"] == vat_id
    assert suggestion["tax_percentage"] == filled["tax_percentage"]
    assert suggestion["expense_account_id"] == filled["expense_account_id"] == account
    assert "vat_code_id" not in suggestion


def test_match_to_bezala_uses_the_rule(bound_db, monkeypatch):  # noqa: F811
    """put_skipped-loggen visar regelns moms: taxi i Sverige 0 %, inte 25,5 %."""
    from app.services.bezala_field_mapper import build_receipt_params

    line, m = _taxi(bound_db)
    params = build_receipt_params(
        file_name="k.pdf", sender=None, vendor="Taxi Stockholm", category="Taxi",
        amount=12.40, currency="EUR", receipt_date="2026-11-26",
        accounts=[{"id": 67101, "name": "Taksikulut", "default_vat_id": 864}],
        cost_centers=[], vat_rates=[])
    c64.apply_rule_to_params(params, c64.draft_rule(bound_db, m, line))
    assert params["vat_lines_attributes"][0]["vat_id"] == 1
    assert params["rule"]["vat"] == "0%"


# ================================================================ Del 4/5


def test_ride_km_from_receipt_text():
    from app.models import ProcessedMessage

    r = ProcessedMessage(message_id="x", vendor="Taxi Stockholm", category="Taxi",
                         summary="Taxi, Färd: 1.10 km, 14:05-14:12")
    ride = c64.receipt_ride(r)
    assert (ride["km"], ride["start"], ride["end"]) == (1.1, "14:05", "14:12")


def test_normalize_ride_info():
    from app.services.receipt_analyzer import normalize_ride_info

    assert normalize_ride_info({"km": "1,10", "start": "14.05", "city": " Stockholm "}) == \
        {"km": 1.1, "start": "14:05", "end": None, "city": "Stockholm"}
    assert normalize_ride_info({"km": None}) is None


def test_taxi_leg_linked_and_km_vehicle_from_receipt(on):
    from tests.backend.test_c60 import _trip_leg

    line, m = _taxi(on)
    leg = _trip_leg(on, seq=1, leg_key="manual:1", leg_date=date(2026, 11, 26), mode="taxi",
                    from_place="Office", to_place="Conference", km=None, source="manual",
                    set_by="user", note="between office and customer conference")
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001)])},
                  cv={"5597345": [{"id": 266999, "expense_id": 5597345, "field_id": 3,
                                    "expense_type": "Receipt", "value": "1",
                                    "field_type": "ExpenseCategory::ConditionalField"}]})
    out = c60.verify_and_update(on, "5597345", client=client)
    on.refresh(leg)
    assert leg.receipt_message_id == m.message_id and leg.km == 1.1
    assert leg.source == "receipt" and leg.is_estimate is False
    vals = {r["field_id"]: r["value"] for r in client.cv["5597345"]}
    assert vals == {1: "Taxi", 2: "1.1", 3: "1"}
    assert client.txs["5597345"]["description"] == (
        "Taxi ride within Stockholm between office and customer conference, 1.1 km, "
        "26 November 2026")
    assert out["auto_fill"]["status"] == c64.STATUS_DONE


def test_receipt_km_wins_over_route_table(on):
    from tests.backend.test_c60 import _trip_leg

    line, m = _taxi(on)
    leg = _trip_leg(on, seq=1, leg_date=date(2026, 11, 26), mode="taxi", km=8.0,
                    source="route_table", is_estimate=True, receipt_message_id=m.message_id)
    c60.legs_for_receipt(on, m)
    on.refresh(leg)
    assert (leg.km, leg.source, leg.is_estimate) == (1.1, "receipt", False)


def test_mikkos_manual_km_never_overwritten(on):
    from tests.backend.test_c60 import _trip_leg

    line, m = _taxi(on)
    leg = _trip_leg(on, seq=1, leg_date=date(2026, 11, 26), mode="taxi", km=3.0,
                    source="manual", set_by="user", receipt_message_id=m.message_id)
    c60.legs_for_receipt(on, m)
    on.refresh(leg)
    assert leg.km == 3.0


def test_two_candidate_legs_link_nothing(on):
    from tests.backend.test_c60 import _trip_leg

    line, m = _taxi(on)
    for i in (1, 2):
        _trip_leg(on, seq=i, leg_key=f"m{i}", leg_date=date(2026, 11, 26), mode="taxi",
                  source="manual", set_by="user")
    assert c60.legs_for_receipt(on, m) == []


def test_no_city_no_guess(bound_db):  # noqa: F811
    line, m = _taxi(bound_db)
    m.ride_info = {"km": 1.1}
    bound_db.commit()
    assert c64.taxi_description(bound_db, m, line) is None


# ================================================================ Del 1.3


def test_failed_write_marks_fill_unverified_and_never_retries(on):
    _taxi(on)
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001)])},
                  ignore_puts=True)
    out = c60.verify_and_update(on, "5597345", client=client)
    assert out["auto_fill"]["status"] == c64.STATUS_UNVERIFIED
    m = c60._row_for_tx(on, "5597345")
    assert m.draft_fill_status == c64.STATUS_UNVERIFIED and "inte bekräftad" in m.draft_fill_note
    n = len(client.puts)
    again = c60.verify_and_update(on, "5597345", client=client, attempt=2)
    assert len(client.puts) == n  # inget andra försök
    assert "Redan körd" in again["auto_fill"]["result"]


def test_bezala_error_marks_fill_unverified(on):
    _taxi(on)
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001)])})

    def boom(tx_id, fields):
        raise RuntimeError("Bezala 500")

    client._put_transaction_unchecked = boom
    out = c60.verify_and_update(on, "5597345", client=client)
    assert out["auto_fill"]["status"] == c64.STATUS_UNVERIFIED
    assert client.txs["5597345"]["description"] == ""


# ================================================================ flaggor


def test_all_flags_false_in_code(bound_db, monkeypatch):  # noqa: F811
    import inspect

    from app.services import netvisor_client

    for flag in ("AUTO_COUPLE_ENABLED", "BEZALA_METADATA_PUT_ENABLED", "NETVISOR_ENABLED",
                 "BEZALA_DRAFT_FILL_ENABLED"):
        monkeypatch.delenv(flag, raising=False)
    assert guard.env_auto_couple_enabled() is False
    assert guard.metadata_put_enabled() is False
    assert c60.draft_fill_enabled() is False
    assert netvisor_client.netvisor_enabled() is False if hasattr(
        netvisor_client, "netvisor_enabled") else True
    c64.set_auto_fill(bound_db, True, "test")
    assert c64.auto_fill_on(bound_db) is False  # kräver även BEZALA_DRAFT_FILL_ENABLED
    src = inspect.getsource(c64) + inspect.getsource(c60)
    assert '_env_bool(FLAG_DRAFT_FILL, False)' in src
    assert "True)" not in inspect.getsource(c64.auto_fill_on)


def test_switch_defaults_off(bound_db):  # noqa: F811
    assert c64.auto_fill_on(bound_db) is False


def test_preview_writes_nothing(on):
    _taxi(on)
    _anthropic(on)
    client = Fake({"5597345": _bezala_after_creation("5597345", 12.40, [_vl(9001)]),
                   "5596175": _anthropic_before()})
    out = c64.preview(on, ["5597345", "5596175"], client=client)
    assert out["dry_run"] is True and client.puts == []
    assert {i["transaction_id"] for i in out["items"]} == {"5597345", "5596175"}
    item = out["items"][1]
    assert item["untouched"] is False  # ingen botläsning sparad
    assert item["if_untouched"]["original_amount"] == "-22.95"
    assert c60._row_for_tx(on, "5596175").bezala_draft_seen is None


# ================================================================ Postgres


def test_postgres_auto_fill(pg_url, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base
    from app.models import BezalaWriteLog

    engine = create_engine(pg_url)
    with engine.begin() as c:
        c.exec_driver_sql("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    @contextmanager
    def _session(_db=None):
        yield s

    monkeypatch.setattr(guard, "_session", _session)
    try:
        c64.set_auto_fill(s, True, "test")
        _anthropic(s)
        client = Fake({"5596175": _anthropic_before()})
        out = c60.verify_and_update(s, "5596175", client=client)
        assert out["auto_fill"]["status"] == c64.STATUS_DONE
        m = c60._row_for_tx(s, "5596175")
        assert m.bezala_draft_seen["last_by"] == "bot_write"
        assert m.draft_fill_status == "done"
        log = s.query(BezalaWriteLog).filter_by(operation="draft_fill").one()
        assert log.state_before["state"] == log.state_after["state"] == "draft"
        assert client.txs["5596175"]["original_amount"] == "-22.95"
    finally:
        s.close()
        engine.dispose()

