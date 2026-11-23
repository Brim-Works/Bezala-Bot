"""C57 - köp + återbetalning av samma bokning = avbokad, räknas som noll.

(a) köp + återbetalning av samma bokning ger noll
(b) ombokning med nytt bokningsnummer räknas bara en gång
(c) vanlig dubblett (bokningsbekräftelse + e-biljett) funkar som förut
(d) avbokad bokning syns hopfälld men inte i summa/checklista/'utan resa'
(e) flygbens-pekare flyttas till det gällande kvittot (km ändras inte)
(f) samma sak på en riktig PostgreSQL
Plus: Del 2 rutterna (båda riktningarna) och Del 3 frågorna till Mikko.
"""

from __future__ import annotations

import os
from datetime import date, datetime

os.environ.setdefault("APP_PASSWORD", "test-password")
os.environ.setdefault("SESSION_SECRET", "test-secret")
os.environ.setdefault("SCAN_ENABLED", "false")
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import pytest  # noqa: E402

from tests.backend.test_c42 import db  # noqa: E402,F401
from tests.backend.test_c54_waiting_status import pg_url  # noqa: E402,F401
from tests.backend.test_c56_allocation import _cal, _sugg  # noqa: E402

SEG_8HZ = [{"flight_no": "AY 953", "origin": "HEL", "dest": "CPH", "date": "2026-08-25"},
           {"flight_no": "AY 962", "origin": "CPH", "dest": "HEL", "date": "2026-08-26"}]
SEG_7OT = [{"flight_no": "AY 951", "origin": "HEL", "dest": "CPH", "date": "2026-08-25"},
           {"flight_no": "AY 962", "origin": "CPH", "dest": "HEL", "date": "2026-08-26"}]
FLIGHTS_82 = [
    {"id": "f1", "flight_no": "AY 951", "origin": "HEL", "destination_iata": "CPH",
     "start": "2026-08-25T03:20:00+00:00", "end": "2026-08-25T05:00:00+00:00"},
    {"id": "f2", "flight_no": "AY 962", "origin": "CPH", "destination_iata": "HEL",
     "start": "2026-08-26T17:25:00+00:00", "end": "2026-08-26T19:00:00+00:00"},
]


def _m(db, mid, vendor, amount, day, currency="EUR", **kw):  # noqa: F811
    from app.models import ProcessedMessage

    db.add(ProcessedMessage(
        message_id=mid, sender=kw.pop("sender", "x@example.com"),
        subject=kw.pop("subject", vendor), status="saved", vendor=vendor, amount=amount,
        currency=currency, receipt_date=day, category=kw.pop("category", "Flyg"),
        received_at=datetime(2026, 8, 1), processed_at=datetime(2026, 8, 1), **kw))


def seed(db):  # noqa: F811
    from app.models import CalendarTrip

    _cal(db, 82, date(2026, 8, 25), date(2026, 8, 26), "Malmö", "edited")
    db.query(CalendarTrip).filter(CalendarTrip.id == 82).update({"flights": FLIGHTS_82})
    _cal(db, 146, date(2026, 11, 24), date(2026, 11, 25), "Stockholm")
    _cal(db, 147, date(2026, 11, 27), date(2026, 11, 28), "Oslo")
    # 8HZCXX: köpt 13 aug (+674,48), återbetald 15 aug (-674,48). Avbokad.
    _m(db, "hz-et", "Finnair", 674.48, "2026-08-25", booking_ref="8HZCXX",
       subject="YOUR ETICKET RECEIPT", sender="eticket@amadeus.com", flight_segments=SEG_8HZ,
       bezala_transaction_id="5334818", bezala_payment_amount=674.48,
       bezala_payment_date="2026-08-13",
       bezala_payment_merchant="MIKKO KEINONEN: FINNAIR O8HZCXX, VANTAA, FI 674.48 EUR")
    _m(db, "hz-bc", "Finnair", 674.48, "2026-08-25", booking_ref="8HZCXX",
       subject="Bokningsbekräftelse och information för 8HZCXX", flight_segments=SEG_8HZ,
       duplicate_of="hz-et", bezala_transaction_id="5334821", bezala_payment_amount=674.48,
       bezala_payment_date="2026-08-15",
       bezala_payment_merchant="MIKKO KEINONEN: FINNAIR O8HZCXX, VANTAA, FI -674.48 EUR")
    # 7OTOGJ: ombokningen (gäller). E-biljett + bokningsbekräftelse = dubblett.
    _m(db, "ot-et", "Finnair", 514.48, "2026-08-25", booking_ref="7OTOGJ",
       subject="YOUR ETICKET RECEIPT", sender="eticket@amadeus.com", flight_segments=SEG_7OT,
       bezala_transaction_id="5335462", bezala_payment_amount=514.48,
       bezala_payment_merchant="MIKKO KEINONEN: FINNAIR O7OTOGJ, VANTAA, FI 514.48 EUR")
    _m(db, "ot-bc", "Finnair", 514.48, "2026-08-25", booking_ref="7OTOGJ",
       subject="Bokningsbekräftelse och information för 7OTOGJ", flight_segments=SEG_7OT,
       duplicate_of="ot-et")
    _m(db, "sk-0825", "Skånetrafiken", 150.0, "2026-08-25", "SEK", category="Tåg")
    # 8NLAI4: e-biljetten kopplad till återbetalningsraden. Avbokad, ingen resa.
    _m(db, "nl-et", "Finnair", 134.48, "2026-11-26", booking_ref="8NLAI4",
       subject="YOUR ETICKET RECEIPT", bezala_transaction_id="5575527",
       bezala_payment_amount=134.48, bezala_payment_date="2026-11-18",
       bezala_payment_merchant="MIKKO KEINONEN: FINNAIR, VANTAA, FI -134.48 EUR")
    _m(db, "nl-bc", "Finnair", 134.48, "2026-11-26", booking_ref="8NLAI4",
       subject="Bokningsbekräftelse och information för 8NLAI4", duplicate_of="nl-et")
    _sugg(db, 14, date(2026, 8, 25), date(2026, 8, 26),
          ["hz-bc", "hz-et", "ot-et", "ot-bc", "sk-0825"])
    _sugg(db, 23, date(2026, 11, 26), date(2026, 11, 26), ["nl-et", "nl-bc"])
    db.commit()


def _sums(a, tid):
    from app.services.c56_receipt_allocation import totals

    return totals(a["by_trip"].get(tid, []))


def test_a_purchase_and_refund_is_zero(db):  # noqa: F811
    from app.services.c56_receipt_allocation import allocate
    from app.services.c57_cancelled import cancelled_messages

    seed(db)
    c = cancelled_messages(db)
    assert set(c) == {"hz-et", "hz-bc", "nl-et", "nl-bc"}
    assert c["hz-et"]["booking_ref"] == "8HZCXX"
    assert c["hz-et"]["purchase_date"] == "2026-08-13"
    assert c["hz-et"]["refund_date"] == "2026-08-15"
    # 8NLAI4: köpraden saknar kvitto - inget köpdatum (flygdagen 26 nov är inte köpdagen).
    assert c["nl-et"]["purchase_date"] is None
    assert c["nl-et"]["refund_date"] == "2026-11-18"
    a = allocate(db)
    assert _sums(a, 82) == {"EUR": 514.48, "SEK": 150.0}


def test_b_rebooking_counted_once(db):  # noqa: F811
    from app.services.c56_receipt_allocation import allocate

    seed(db)
    a = allocate(db)
    counted = [r for r in a["by_trip"][82] if r["counted"] and r["vendor"] == "Finnair"]
    assert [r["booking_ref"] for r in counted] == ["7OTOGJ"]


def test_c_plain_duplicate_unchanged(db):  # noqa: F811
    from app.services.c56_receipt_allocation import allocate
    from app.services.c57_cancelled import detect

    seed(db)
    a = allocate(db)
    dup = [r for r in a["by_trip"][82] if r["message_id"] == "ot-bc"]
    assert dup and dup[0]["counted"] is False and dup[0]["duplicate_of"] == "ot-et"
    # Bekräftelse + e-biljett utan återbetalning är aldrig en avbokning.
    plain = [
        {"message_id": "a", "vendor": "Finnair", "amount": 611.04, "booking_ref": "7SWR9A",
         "subject": "YOUR ETICKET RECEIPT"},
        {"message_id": "b", "vendor": "Finnair", "amount": 611.04, "booking_ref": "7SWR9A",
         "subject": "Bokningsbekräftelse", "duplicate_of": "a"},
        # Hotellets avbokningsregler och 'cancellation' gör inget kvitto till återbetalning.
        {"message_id": "h", "vendor": "Scandic", "amount": 1038.0, "currency": "SEK",
         "subject": "Free cancellation until 18:00"},
    ]
    assert detect(plain) == {}


def test_partial_refund_is_not_cancelled():
    from app.services.c57_cancelled import detect

    rows = [
        {"message_id": "p", "vendor": "Finnair", "amount": 500.0, "booking_ref": "ABC123",
         "bezala_payment_amount": 500.0, "bezala_payment_merchant": "FINNAIR 500.00 EUR"},
        {"message_id": "r", "vendor": "Finnair", "amount": -120.0, "booking_ref": "ABC123",
         "subject": "Refund"},
    ]
    assert detect(rows) == {}


def test_pair_without_booking_ref():
    from app.services.c57_cancelled import detect

    rows = [
        {"message_id": "p", "vendor": "Scandic Go", "amount": 1038.0, "currency": "SEK",
         "receipt_date": "2026-05-01"},
        {"message_id": "r", "vendor": "Scandic", "amount": -1038.0, "currency": "SEK",
         "receipt_date": "2026-05-03", "subject": "Återbetalning"},
        {"message_id": "x", "vendor": "Scandic", "amount": 1038.0, "currency": "SEK",
         "receipt_date": "2026-09-01"},
    ]
    assert set(detect(rows)) == {"p", "r"}


def test_d_collapsed_row_not_in_sums_checklist_or_unassigned(db):  # noqa: F811
    from app.models import CalendarTrip
    from app.services.calendar_trip_store import list_trips, receipts_without_trip
    from app.services.trip_completeness import checklist, load_receipts

    seed(db)
    assert {r.message_id for r in load_receipts(db)} == {"ot-et", "sk-0825"}
    trip = db.get(CalendarTrip, 82)
    cl = checklist(db, trip)
    flight = [i for i in cl["items"] if i["kind"] == "flight"][0]
    assert flight["status"] == "found" and flight["receipt"]["message_id"] == "ot-et"
    trips = {t["id"]: t for t in list_trips(db)}
    assert trips[82]["receipt_totals"] == {"EUR": 514.48, "SEK": 150.0}
    cb = trips[82]["cancelled_bookings"]
    assert [b["booking_ref"] for b in cb] == ["8HZCXX"]
    assert cb[0]["amount"] == 674.48 and cb[0]["label_sv"].startswith("Avbokad och återbetald")
    assert {r["message_id"] for r in cb[0]["receipts"]} == {"hz-et", "hz-bc"}
    assert all(r["counted"] is False for r in cb[0]["receipts"])
    extra = receipts_without_trip(db)
    assert extra["unassigned"] == []
    assert [b["booking_ref"] for b in extra["cancelled_without_trip"]] == ["8NLAI4"]


def test_e_flight_leg_pointer_moves(db):  # noqa: F811
    from app.models import CalendarTrip, TripLeg
    from app.services import trip_legs as tl
    from app.services.trip_completeness import checklist

    seed(db)
    trip = db.get(CalendarTrip, 82)
    checklist(db, trip)
    db.commit()
    rows, _ = tl.sync_trip_legs(db, trip)
    flights = [r for r in rows if r.mode == "flight"]
    assert len(flights) == 2
    assert {r.receipt_message_id for r in flights} == {"ot-et"}
    # Mikkos egen rad pekar på den avbokade bokningen: pekaren flyttas, km står kvar.
    leg = flights[0]
    leg.set_by, leg.source, leg.km, leg.receipt_message_id = "user", "manual", 900.0, "hz-et"
    db.commit()
    tl.sync_trip_legs(db, trip)
    again = db.get(TripLeg, leg.id)
    assert again.receipt_message_id == "ot-et"
    assert again.km == 900.0 and again.source == "manual" and again.set_by == "user"


def test_undo_counts_booking_again(db):  # noqa: F811
    from app.models import BotFlag
    from app.services.c56_receipt_allocation import allocate
    from app.services.c57_cancelled import list_cancelled, set_cancelled_booking

    seed(db)
    keys = {b["booking"]: b for b in list_cancelled(db)}
    assert set(keys) == {"finnair:8HZCXX", "finnair:8NLAI4"}
    set_cancelled_booking(db, "finnair:8HZCXX", False)
    assert _sums(allocate(db), 82) == {"EUR": 1188.96, "SEK": 150.0}
    set_cancelled_booking(db, "finnair:8HZCXX", True)
    assert _sums(allocate(db), 82) == {"EUR": 514.48, "SEK": 150.0}
    # Inget raderas: flaggan finns kvar med värdet 'false'.
    assert db.query(BotFlag).filter(BotFlag.key == "c57_keep:finnair:8HZCXX").one().value == "false"
    with pytest.raises(Exception):
        set_cancelled_booking(db, "finnair:NOPE", False)


def test_nothing_written_to_receipts(db):  # noqa: F811
    from app.models import ProcessedMessage
    from app.services.calendar_trip_store import list_trips

    seed(db)
    cols = ("message_id", "amount", "duplicate_of", "bezala_transaction_id", "status",
            "deleted_at", "bezala_payment_amount", "bezala_payment_merchant")
    before = [tuple(getattr(m, c) for c in cols) for m in db.query(ProcessedMessage).all()]
    list_trips(db)
    after = [tuple(getattr(m, c) for c in cols) for m in db.query(ProcessedMessage).all()]
    assert before == after


def test_f_postgres(pg_url):  # noqa: F811
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app import models  # noqa: F401
    from app.db import Base
    from app.services.c56_receipt_allocation import allocate
    from app.services.c57_cancelled import set_cancelled_booking

    engine = create_engine(pg_url)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    try:
        seed(s)
        a = allocate(s)
        assert _sums(a, 82) == {"EUR": 514.48, "SEK": 150.0}
        assert a["unassigned"] == []
        assert [b["booking_ref"] for b in a["cancelled_by_trip"][82]] == ["8HZCXX"]
        set_cancelled_booking(s, "finnair:8HZCXX", False)
        assert _sums(allocate(s), 82) == {"EUR": 1188.96, "SEK": 150.0}
    finally:
        s.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ---------------------------------------------------------------- Del 2


@pytest.mark.parametrize("frm,to,km", [
    ("Alvesta", "Copenhagen Airport", 202.4),
    ("Copenhagen Airport", "Alvesta", 202.4),
    ("Copenhagen Airport", "Angelholm", 123.5),
    ("Angelholm", "Copenhagen Airport", 123.5),
    ("Copenhagen Airport", "Bastad", 141.5),
    ("Bastad", "Copenhagen Airport", 141.5),
])
def test_mikkos_train_routes_in_table(frm, to, km):
    from app.data.route_km import lookup_km

    r = lookup_km(frm, to, "skanetrafiken")
    assert r is not None and r["km"] == km and r["is_estimate"] is True
    assert "Mikko" in r["reference"] and "Omio" in r["reference"]


# ---------------------------------------------------------------- Del 3


def test_questions_instead_of_silent_flags():
    from app.models import TripLeg
    from app.services.trip_legs import questions

    taxi = TripLeg(id=66, trip_id=139, leg_key="k", mode="taxi", leg_date=date(2026, 9, 1),
                   km=None, excluded=False)
    q = questions([taxi], ["km_missing", "home_transport_unknown"])
    assert q[0]["kind"] == "km_missing" and q[0]["leg_id"] == 66
    assert "start och slut saknas" in q[0]["text_sv"] and "gissar inte" in q[0]["text_sv"]
    assert q[1]["kind"] == "home_transport"
    assert q[1]["text_sv"] == "Hur tog du dig till och från HEL? Bil, taxi eller annat?"
    assert questions([], []) == []
