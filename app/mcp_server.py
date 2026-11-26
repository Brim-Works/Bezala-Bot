"""C40 — MCP-server (Streamable HTTP) på /mcp för Claude-chatten.

Reseverktygen läser och ändrar BARA `calendar_trips` / `calendar_trip_days`
(C42: även trip_expectations/card_lines). C43 mark_private_expense ändrar
card_lines och skapar — bara med create_draft=True — ett Bezala-UTKAST.
Inget skickas för attest och inget går till Netvisor.

Varje anrop kräver en giltig OAuth-access-token (se app/mcp_oauth.py).
Utan token svarar /mcp 401 med `WWW-Authenticate: Bearer
resource_metadata=…` så att claude.ai startar inloggningen.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.db import SessionLocal
from app.mcp_oauth import resource_metadata_url, resource_url, validate_access_token
from app.services import calendar_trip_edit as edit
from app.services import trip_completeness as completeness
from app.services.calendar_trip_edit import TripEditError

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Bezala Bot travel review. Trips are detected from Mikko's Google Calendar. "
    "Use these tools to review, correct and approve trips. Edits only change "
    "Bezala Bot's own trip list; nothing is sent to Bezala or Netvisor. "
    "Times without a timezone are Helsinki time. Everything that goes to Netvisor "
    "is in English: purpose, destination (English city name, e.g. 'Copenhagen', "
    "'Gothenburg', 'Malmö') and country (ISO code). Trip times are travel times "
    "(leaving home → back home: 100 min before a HEL departure, 60 min after a HEL "
    "landing); flight times are shown separately. A flight to CPH usually means "
    "work in Sweden (Malmö, Helsingborg, Växjö, Ljungby). Ask Mikko before approving. "
    "Completeness: get_trip_checklist shows which receipts a trip should have "
    "(flight, one hotel per night, HEL parking, optional local transport) and "
    "whether each is found; list_missing lists everything missing, including "
    "Bezala card payments without a receipt (read-only — nothing is created in Bezala). "
    "Card lines flagged 'possible_private' are parking outside any trip — ask Mikko "
    "whether it was private; if so use mark_private_expense (Bezala comment 'Private "
    "expense - to be deducted from salary.'). Nothing is ever submitted for approval. "
    "Free meals per day (free_meals 0-2: lunch and dinner, breakfast never counts) are "
    "prefilled from the calendar (kick-off = dinner, customer day = lunch, dinner/lunch "
    "events) and can be corrected with update_trip_day (free_meals, free_meal_note); "
    "2 free meals = the day's Netvisor proposal is marked 'Inklusive kost'. "
    "reject_trip hides a trip that is not a business trip; refresh never recreates it. "
    "time_source tells where travel times come from: 'parking' (Moovy entry/exit ± "
    "drive), 'flight_default' (flights + margin) or 'manual'. Edited/approved trips "
    "are never recalculated — get_trip shows 'suggestions' instead. list_missing gives "
    "a suggested_receipt per card line; couple_receipt couples it (Bezala draft, never "
    "submitted) ONLY when Mikko explicitly asks. 'domestic_unconfirmed' = hotel "
    "without flight: ask Mikko whether it was a business trip. "
    "Decision queue (Travel Tinder): list_pending_decisions gives one card per "
    "pending decision (receipt <-> card line, trips to approve, subscriptions, "
    "duplicate charges, receipts without card payment, overdue refunds) with a "
    "yes_label/no_label; decide(item_id, 'yes'|'no'|'later') acts on it - ask Mikko "
    "first and use his answer; undo_last_decision reverts the latest one. "
    "expect_refund marks a card line whose money should come back (e.g. cancelled trip). "
    "Subscriptions need an account Mikko chooses (list_bezala_accounts, "
    "set_subscription_account) before they can be activated - never guess accounts. "
    "Receipts without amount are never coupled automatically. Private Gmail receipts "
    "without a company card charge after 14 days are ignored (ignored_private_source). "
    "get_cleanup_report and investigate_vendor are read-only diagnostics. "
    "C50: automatic coupling is OFF unless AUTO_COUPLE_ENABLED; couplings never write "
    "the description to Bezala - couple_receipt returns description_to_paste for Mikko. "
    "list_bot_bezala_writes(days) is a read-only report of what the bot wrote to Bezala. "
    "C53 emissions data: get_trip shows 'legs' (km per leg and mode); "
    "get_emissions_summary(year) sums km/kWh/litres; update_trip_leg/add_trip_leg "
    "correct legs; export_trip_legs gives CSV/JSON. km_missing = ask Mikko, never guess. "
    "No kg CO2e - Admin applies own factors. C54: Admin's seven rows (short/medium/long "
    "flights km, train, bus, non-company car km, hotel nights); company car is info only; "
    "ground km from a route table (route_table, is_estimate) - receipt km and Mikko's "
    "manual km always win; export_trip_legs(mode='admin'|'detail'|'route_table'). "
    "C56: list_trip_suggestions shows the receipt-based AI suggestions and which are "
    "covered by a calendar trip, old or duplicates; merge_duplicate_trip_suggestions "
    "is a dry-run unless apply=true - only after Mikko says yes to the list. "
    "C57: a booking bought and refunded (receipt on a negative card line, or purchase + "
    "refund receipt, same vendor and booking/amount) is 'cancelled': not in trip totals, "
    "checklists or 'receipts without a trip'; shown collapsed as 'Cancelled and refunded "
    "(not counted)'. list_cancelled_bookings; set_cancelled_booking(cancelled=false) "
    "counts it again (undo). get_trip 'questions' = things to ask Mikko (never guess). "
    "C60: couple_receipt one at a time, never in parallel; its answer is 'utkast skapat, "
    "koppling inte verifierad än' and the card line stays 'coupling_unverified' until the "
    "bot has read the draft again (~75 s) and Bezala shows connected_to_bill_line, bill_id, "
    "the card line amount and a date. fill_bezala_drafts / repair_unverified_drafts are "
    "dry runs; apply=true only after Mikko says yes in the chat (needs "
    "BEZALA_DRAFT_FILL_ENABLED=true; only state 'draft'; only empty fields). "
    "C64: preview_auto_fill is a dry run (GET only) of the automatic fill after a "
    "verified coupling (account, VAT, description, sign, empty 0-rows, km). The "
    "automation runs only with BEZALA_DRAFT_FILL_ENABLED=true AND set_auto_fill(on=true), "
    "which is called ONLY after Mikko says yes in the chat. Bezala defaults (account "
    "4060, VAT 1913/1914) are replaced only on untouched drafts; Mikko's values never. "
    "C65 views (MCP Apps, read-only): list_pending_decisions and show_match show the "
    "'Matchning' view, get_card_line and list_missing the status chain, "
    "fill_bezala_drafts the before/after table. The JSON answers are unchanged."
)

mcp = FastMCP("Bezala Bot", instructions=INSTRUCTIONS)

# C65 — MCP Apps: tre ui://-vyer. Verktygens JSON-svar ändras inte.
from app import mcp_apps  # noqa: E402
from app.mcp_apps import ui_meta  # noqa: E402

mcp_apps.register(mcp)


async def _run(fn: Callable[..., Any], *args, **kwargs) -> Any:
    """Kör DB-logik i tråd med egen session. TripEditError → verktygsfel."""

    def work():
        db = SessionLocal()
        try:
            return fn(db, *args, **kwargs)
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    try:
        return await anyio.to_thread.run_sync(work)
    except TripEditError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("MCP-verktyg misslyckades")
        raise RuntimeError(f"Internal error: {exc}") from exc


# ---------- Verktyg ----------


@mcp.tool()
async def list_trips(
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    flagged_only: bool = False,
    include_rejected: bool = False,
) -> dict:
    """List calendar trips: id, dates, destination (work city, English), country,
    purpose (English), status, flags, travel times and flight times (Helsinki),
    and completeness ('complete' | 'incomplete' + number of missing receipts).

    status: 'suggested' | 'edited' | 'approved' | 'rejected'. Rejected trips are
    hidden unless include_rejected=true (or status='rejected'). date_from/date_to: YYYY-MM-DD
    (trips overlapping the range). flagged_only: only trips with flags
    ('needs_review' = pick the outbound flight, 'missing_return' = no return flight
    found, 'destination_unconfirmed' = work city unknown, ask Mikko).
    """
    trips = await _run(edit.list_trips, status=status, date_from=date_from,
                       date_to=date_to, flagged_only=flagged_only,
                       include_rejected=include_rejected)
    return {"count": len(trips), "trips": trips}


@mcp.tool()
async def get_trip(trip_id: int) -> dict:
    """Full trip: travel times (travel_start/travel_end) and flight times
    (flight_departure/flight_arrival), every day with travel times (UTC + Helsinki),
    arrival airport, work city (destination) and where it came from
    (destination_source), country + English name, hotel, meetings, purpose,
    linked receipts, flight options and the prepared Netvisor description
    (C55: 'Travel for <purpose> in <city>').
    C53: `legs` = one row per leg (flight/train/taxi/bus/car_phev) with km, energy
    (kWh or litres), source and is_estimate; `legs_flags` (km_missing,
    home_transport_unknown, return_flight_missing) and `legs_totals`."""

    def work(db, tid):
        from app.services import trip_legs

        detail = edit.get_trip(db, tid)
        trip = edit._get(db, tid)
        detail.update(trip_legs.trip_legs_view(db, trip))
        return detail

    return await _run(work, trip_id)


@mcp.tool()
async def update_trip(
    trip_id: int,
    start: str | None = None,
    end: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    purpose: str | None = None,
    selected_flight_id: str | None = None,
) -> dict:
    """Edit a trip. Sets status to 'edited' (an approved trip must be approved again).

    start/end: TRAVEL times (leaving home/office, back home), ISO datetime, e.g.
    '2026-08-25T06:15' (Helsinki) or with offset/Z. Flight times are not changed.
    destination: work city in English. country: ISO alpha-2, comma-separated for
    several ('SE,NO'). purpose: one sentence in English (sent to Netvisor).
    selected_flight_id: for 'needs_review' trips, the flight_id from flight_options
    that Mikko actually took (travel start is recalculated from it).
    Only omitted fields stay unchanged. Returns the updated trip; non-English text
    is saved but gives a 'warnings' entry.
    """
    return await _run(edit.update_trip, trip_id, start=start, end=end,
                      destination=destination, country=country, purpose=purpose,
                      selected_flight_id=selected_flight_id)


@mcp.tool()
async def update_trip_day(
    trip_id: int,
    date: str,
    start_time: str | None = None,
    end_time: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    hotel: str | None = None,
    purpose: str | None = None,
    free_meals: int | None = None,
    free_meal_sources: list[str] | None = None,
    free_meal_note: str | None = None,
) -> dict:
    """Edit one day of a trip. date: YYYY-MM-DD. Times HH:MM Helsinki (travel times).
    destination = work city in English (e.g. 'Malmö', 'Copenhagen'); purpose in
    English. Pass an empty string to clear a field. Status → 'edited'.
    free_meals: 0-2 free meals that day, lunch and dinner (breakfast never counts;
    reduces the per diem). 2 = the day's Netvisor proposal is marked
    'Inklusive kost' (meals included). free_meal_note: optional English note,
    e.g. 'Kick-off lunch and dinner'. Sets meals_source='manual'.
    free_meal_sources: any of 'hotel_breakfast', 'lunch_customer_day',
    'dinner_kickoff', 'dinner_calendar', 'lunch_calendar', 'ticket'. Sources only
    → the count is calculated.
    Works on edited trips too. C55: free meals are never written in the Netvisor
    text - only via 'Inklusive kost'; the bot's own view shows meals_note.
    Returns the updated trip (with 'warnings' if a field is not in English)."""
    return await _run(edit.update_trip_day, trip_id, date, start_time=start_time,
                      end_time=end_time, destination=destination, country=country,
                      hotel=hotel, purpose=purpose, free_meals=free_meals,
                      free_meal_sources=free_meal_sources,
                      free_meal_note=free_meal_note)


@mcp.tool()
async def add_trip_day(
    trip_id: int,
    date: str,
    start_time: str | None = None,
    end_time: str | None = None,
    destination: str | None = None,
    country: str | None = None,
    hotel: str | None = None,
    purpose: str | None = None,
) -> dict:
    """Add a day to a trip (extends the trip if outside it). date: YYYY-MM-DD.
    destination/purpose in English. Status → 'edited'. Returns the updated trip."""
    return await _run(edit.add_trip_day, trip_id, date, start_time=start_time,
                      end_time=end_time, destination=destination, country=country,
                      hotel=hotel, purpose=purpose)


@mcp.tool()
async def remove_trip_day(trip_id: int, date: str) -> dict:
    """Remove a day from a trip (e.g. Mikko was home). Removing the first/last
    day shortens the trip. Status → 'edited'. Returns the updated trip."""
    return await _run(edit.remove_trip_day, trip_id, date)


@mcp.tool()
async def approve_trip(trip_id: int) -> dict:
    """Mark a trip as approved. Only changes the status — nothing is sent anywhere."""
    return await _run(edit.approve_trip, trip_id)


@mcp.tool()
async def unapprove_trip(trip_id: int) -> dict:
    """Undo approval (back to 'edited', or 'suggested' if never edited). Status only."""
    return await _run(edit.unapprove_trip, trip_id)


@mcp.tool()
async def reject_trip(trip_id: int, reason: str | None = None) -> dict:
    """Reject a trip (not a business trip, wrongly detected …). Status →
    'rejected'. refresh_calendar never recreates it (same dates + same flights/
    events = same trip), list_trips hides it and list_missing ignores it.
    reason: short English note. Undo with unreject_trip. Only on Mikko's word."""
    return await _run(edit.reject_trip, trip_id, reason)


@mcp.tool()
async def unreject_trip(trip_id: int) -> dict:
    """Undo reject_trip: back to 'edited' (if it was edited) or 'suggested'."""
    return await _run(edit.unreject_trip, trip_id)


@mcp.tool()
async def refresh_calendar() -> dict:
    """Re-read Google Calendar and re-run trip detection. Never overwrites
    'edited' or 'approved' trips. Suggested trips get new ids afterwards."""
    from app.services.calendar_trip_store import refresh_from_calendar

    def work(db):
        result = refresh_from_calendar(db)
        if result["status"] == "ok":
            result["trips"] = edit.list_trips(db)
            result["ungrouped"] = [
                {"date": u.get("date"), "summary": u.get("summary"), "reason": u.get("reason")}
                for u in result.get("ungrouped", [])
            ]
        return result

    return await _run(work)


# ---------- C42: fullständighetskontroll ----------


@mcp.tool()
async def get_trip_checklist(trip_id: int) -> dict:
    """Expected receipts for a trip and their status. Items: 'flight' (Finnair/
    Amadeus ticket covering the trip), 'hotel' (one per night, slot = night date;
    a day trip gets 'not_applicable'), 'parking' (HEL parking, company car),
    'local' (optional: train/bus/taxi when the arrival airport is not the work
    city). Status: 'found' (with receipt link), 'missing', 'not_applicable',
    'optional'. set_by 'user' = Mikko's own mark (never overwritten).
    card_lines = Bezala card payments during the trip that have no receipt."""
    return await _run(completeness.get_trip_checklist, trip_id)


@mcp.tool()
async def set_expectation_status(
    trip_id: int,
    kind: str,
    status: str,
    note: str | None = None,
    date: str | None = None,
) -> dict:
    """Mark a checklist item. kind: 'flight' | 'hotel' | 'parking' | 'local'.
    status: 'not_applicable' (e.g. stayed with friends, no parking), 'found'
    (Mikko confirms it is handled), or 'auto' (remove Mikko's mark).
    date: YYYY-MM-DD, only for one hotel night (omit = all nights).
    Mikko's marks survive recalculation and calendar refresh. Ask Mikko first.
    Returns the updated checklist."""
    return await _run(completeness.set_expectation_status, trip_id, kind, status,
                      note=note, date_=date)


@mcp.tool()
async def set_hotel_night_choice(
    trip_id: int,
    night: str,
    choice: str,
    note: str | None = None,
) -> dict:
    """C58 - Mikko's choice for one hotel night without a receipt (night =
    YYYY-MM-DD). choice: 'no_hotel' (no hotel that night: removed from the
    checklist and from hotel nights), 'handled_in_bezala' (done, counts as a
    hotel night), 'invoiced_to_company' (hotel invoices the company directly,
    no receipt or Bezala expense expected; done, counts as a hotel night, never
    an expense line in Netvisor), 'receipt_coming' (stays open, no warning for
    7 days) or 'undo'. Never prefill - ask Mikko first. Only Bezala Bot's own
    table changes. Returns the updated checklist."""
    from app.services.trip_completeness import set_hotel_night_choice as _set

    return await _run(_set, trip_id, night, choice, note)


@mcp.tool()
async def get_hotel_night_tips(trip_id: int) -> dict:
    """C58 - read-only tips for hotel nights without a receipt: hotel receipts
    in the bot near the night and Bezala expenses with hotel words near the
    date (GET only). Nothing is coupled; show the tips to Mikko, never choose."""
    from app.services.c58_hotel_nights import hotel_night_tips

    return await _run(hotel_night_tips, trip_id)


@mcp.tool(meta=ui_meta("list_missing"))
async def list_missing(older_than_days: int = 5) -> dict:
    """Everything missing: checklist items with status 'missing' on trips that
    ended at least `older_than_days` days ago, plus Bezala card payments
    (bill_lines) without a receipt that are at least that old — with age in days,
    the trip they fall in (trip_id) and a suggested receipt source
    ('gmail_filter' / 'link_fetch' sender, or 'unknown'). Reads Bezala
    (read-only) first; nothing is created or sent."""
    return await _run(completeness.list_missing, older_than_days)


@mcp.tool()
async def set_card_line_status(
    bill_line_id: str,
    status: str,
    note: str | None = None,
) -> dict:
    """Mark a Bezala card payment without receipt. status:
    'receipt_not_available' (no receipt exists — stop listing it as missing),
    'private_expense' (private purchase on the company card, deducted from salary —
    prefer mark_private_expense) or 'missing' (undo). Only changes Bezala Bot's own
    list; nothing goes to Bezala."""
    return await _run(completeness.set_card_line_status, bill_line_id, status, note=note)


# ---------- C43: privat utlägg ----------


@mcp.tool()
async def mark_private_expense(
    bill_line_id: str, note: str | None = None, create_draft: bool = False,
) -> dict:
    """Mark a Bezala card payment as a PRIVATE expense (paid by mistake with the
    company card). It stops counting as missing, and the Bezala comment becomes
    'Private expense - to be deducted from salary.' so payroll deducts it.
    note: optional English note. create_draft=True also creates a Bezala DRAFT for
    the row with that comment — it is never submitted for approval.
    Only on Mikko's word. Undo: set_card_line_status(bill_line_id, 'missing')."""
    from app.services import bill_line_status as bls
    from app.services.trip_completeness import _today, card_line_dict

    def work(db):
        try:
            row = bls.mark_private_expense(db, bill_line_id, note)
        except bls.BillLineStatusError as exc:
            raise TripEditError(str(exc)) from exc
        if create_draft:
            from app.services.bezala_client import BezalaClient

            bezala = BezalaClient()
            try:
                bls.create_private_expense_draft(db, bezala, bill_line_id)
            finally:
                bezala.close()
            db.refresh(row)
        return card_line_dict(row, _today())

    return await _run(work)


# ---------- C44: kopplingsförslag och läsverktyg ----------


@mcp.tool()
async def couple_receipt(bill_line_id: str, message_id: str) -> dict:
    """Couple a receipt in Bezala Bot to a Bezala card line (bill_line) using the
    normal match-to-Bezala flow: the receipt PDF is attached and the Bezala
    expense stays a DRAFT — it is never submitted for approval.
    Only when Mikko explicitly asks. Use list_missing (suggested_receipt) or
    get_card_line to find the message_id first. C60: couplings run one at a time
    (call them one after another, not in parallel). The answer says "utkast
    skapat, koppling inte verifierad än": the bot reads the draft again after
    ~75 s and only then marks the card line resolved (else coupling_unverified)."""
    from app.services.card_line_coupling import couple_receipt as _couple

    return await _run(_couple, bill_line_id, message_id)


@mcp.tool()
async def verify_coupling(transaction_id: str, bill_line_id: str | None = None) -> dict:
    """C60: read a Bezala draft again (GET) and check the coupling to the card
    line: connected_to_bill_line, bill_id, amount = card line, date set. The card
    line becomes 'resolved' only when everything matches, otherwise
    'coupling_unverified'. With BEZALA_DRAFT_FILL_ENABLED=true empty fields of the
    draft are filled first (state draft only). Never submits for approval."""
    from app.services.c60_draft import verify_and_update

    return await _run(verify_and_update, transaction_id, bill_line_id=bill_line_id)


@mcp.tool(meta=ui_meta("fill_bezala_drafts"))
async def fill_bezala_drafts(transaction_ids: list[str] | None = None, days: int = 7,
                             apply: bool = False) -> dict:
    """C60: fill Bezala drafts with description, account, VAT, cost center (and
    amount/currency/date when Bezala read them wrong). Default is a DRY RUN that
    shows before/after per draft. Only empty fields or Bezala defaults are filled -
    Mikko's own text is never overwritten. apply=true writes ONLY after Mikko said
    yes in the chat, and only when BEZALA_DRAFT_FILL_ENABLED=true; state is read
    before and after and only state "draft" is written. Nothing is deleted or
    submitted for approval. Without transaction_ids: drafts coupled in the last
    `days` days plus every Bezala draft (state draft). C62: also Vehicle,
    Sträcka (km) and Antal passagerare (conditional_values) - only empty fields,
    km never guessed ("km saknas"); shows Bezala's emissions before/after. C63:
    route-table km (estimate) is used for train/bus/taxi and marked "km
    uppskattat, rutttabell"; flights are always "Airplane: Large ~ 900km/h"
    unless the aircraft is known to be a small propeller plane."""
    from app.services.c60_draft import fill_drafts

    return await _run(fill_drafts, transaction_ids, apply=apply, days=days)


@mcp.tool()
async def repair_unverified_drafts(apply: bool = False) -> dict:
    """C60: the 8 drafts from 2026-11-25 that Bezala did not couple to the card
    line (Finnair 446,77/732,57/380,21/209,48, Skånetrafiken 14,01 x2/22,80/22,81).
    DRY RUN by default: before/after per transaction (amount, currency, date,
    description, account, VAT). apply=true only after Mikko said yes in the chat
    (and BEZALA_DRAFT_FILL_ENABLED=true). Drafts are never deleted or submitted."""
    from app.services.c60_draft import repair_broken_drafts

    return await _run(repair_broken_drafts, apply=apply)


@mcp.tool()
async def preview_auto_fill(transaction_ids: list[str] | None = None) -> dict:
    """C64 DRY RUN (Bezala GET only, nothing written anywhere): for every current
    Bezala draft plus the reference drafts 5596172, 5596173, 5596175, 5596176,
    5596178 and 5597345: what Bezala has now (account, VAT, description, amount,
    cost rows), what the automatic fill would write, and whether the draft counts
    as untouched (updated_at at most one minute after creation and no field changed
    by anyone but the bot). 'if_untouched' shows what it would write if it were
    untouched."""
    from app.services.c64_autofill import preview

    return await _run(preview, transaction_ids)


@mcp.tool()
async def set_auto_fill(on: bool) -> dict:
    """C64: turn the automatic fill of new drafts (after a verified coupling) on or
    off. Call with on=true ONLY after Mikko said yes in the chat to the dry run.
    Also needs BEZALA_DRAFT_FILL_ENABLED=true. Only state 'draft' is ever written;
    nothing is submitted for approval."""
    from app.services.c64_autofill import set_auto_fill as _set

    return await _run(_set, bool(on))


@mcp.tool(meta=ui_meta("get_card_line"))
async def get_card_line(bill_line_id: str) -> dict:
    """Read-only: a Bezala card line (bill_line) with its status in Bezala Bot,
    the receipt coupled to it (if any), the match score and when/how the coupling
    was made, plus the best suggested receipt if it still has none. May read
    Bezala transactions (GET only) to find the coupled receipt."""
    from app.services.card_line_coupling import get_card_line as _get

    return await _run(_get, bill_line_id)


@mcp.tool(meta=ui_meta("show_match"))
async def show_match(bill_line_id: str, message_id: str | None = None) -> dict:
    """C65 read-only: compare a Bezala card line with a receipt field by field
    (vendor, amount, currency, date, place) with the match score split into
    amount/date/vendor/place, and notes when the receipt is a duplicate or is
    already used on another row (e.g. shared with a refund). Without message_id:
    the coupled receipt, else the suggested one. In Claude it is shown as the
    'Matchning' view. Nothing is written anywhere."""
    from app.services.c65_views import match_details

    return await _run(match_details, bill_line_id, message_id)


@mcp.tool()
async def search_receipts(
    query: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 30,
) -> dict:
    """Read-only search among receipts in Bezala Bot (vendor, sender, subject,
    file name, amount, booking reference). date_from/date_to: receipt date
    YYYY-MM-DD. Shows coupling to Bezala, parking entry/exit, flight segments
    and duplicate marks. Nothing is changed."""
    from app.services.card_line_coupling import search_receipts as _search

    return await _run(_search, query, date_from, date_to, limit)


@mcp.tool()
async def list_duplicate_receipts(year: int | None = None) -> dict:
    """Read-only: receipts captured twice (same amount + date + e-ticket/booking
    number). Duplicates are left out of checklists and coupling suggestions;
    Bezala is never touched. Shows whether a duplicate became a Bezala draft."""
    from app.services.receipt_details import duplicate_report

    return await _run(duplicate_report, year)


# ---------- C46: avsändare för privat Gmail ----------


def _sender_error(exc: Exception) -> TripEditError:
    return TripEditError(str(exc))


@mcp.tool()
async def list_private_senders() -> dict:
    """List the senders Bezala Bot may read in Mikko's PRIVATE Gmail (read-only
    access). Each sender: address (exact address or '@domain.com', which also
    matches subdomains), active, added_via (ui/mcp/env), note, last scan and how
    many mails were fetched/saved/rejected. last_scan.rejected_unknown_sender is
    only a count — mail from other senders is never fetched or stored."""
    from app.services.private_senders import list_senders

    return await _run(list_senders)


@mcp.tool()
async def add_private_sender(address: str, note: str | None = None) -> dict:
    """Add a sender for Mikko's PRIVATE Gmail, e.g. when a work expense receipt
    arrives there. address: exact e-mail address or '@domain.com' (matches
    subdomains too). Public mail domains (gmail.com, outlook.com, icloud.com …)
    and the private account itself are refused. Starts a one-time background scan
    from 2026-01-01 for just this sender (already processed mails are skipped).
    Returns what was added and whether the scan started. Only on Mikko's word."""
    from app.services.private_senders import InvalidSender, add_sender

    def work(db):
        try:
            return add_sender(db, address, note, via="mcp")
        except InvalidSender as exc:
            raise _sender_error(exc) from exc

    return await _run(work)


@mcp.tool()
async def remove_private_sender(address: str) -> dict:
    """Stop reading a sender in Mikko's PRIVATE Gmail (the row is kept with
    active=false, so the environment default never adds it back). Already saved
    receipts are not touched."""
    from app.services.private_senders import InvalidSender, remove_sender

    def work(db):
        try:
            return remove_sender(db, address)
        except InvalidSender as exc:
            raise _sender_error(exc) from exc

    return await _run(work)


# ---------- C47: svep-kön, prenumerationer, återbetalningar ----------


@mcp.tool(meta=ui_meta("list_pending_decisions"))
async def list_pending_decisions(limit: int = 20, offset: int = 0) -> dict:
    """The decision queue (same as the Travel Tinder swipe view), one card per
    pending decision, most urgent first: 'receipt' (couple this receipt to this
    card line?), 'trip' (approve the trip?), 'sub' (is this a subscription to
    auto-handle?), 'subdev' (subscription amount changed), 'subnr' (subscription
    without receipt), 'dup' (more card charges than receipts), 'notxn' (receipt
    without card payment after 14 days), 'refund' (expected refund not seen).
    'subpriv' (likely private subscription), trip cards carry completeness
    ('3/4 found, missing: hotel') and free_meals.
    Each card has item_id, title, subtitle, yes_label and no_label (and 'warning'
    when something must be checked first). Paging: total = all cards, use
    offset (next_offset) to get the next page. Read-only."""
    from app.services.decision_queue import list_pending

    return await _run(list_pending, limit=max(1, min(int(limit or 20), 100)),
                      offset=max(0, int(offset or 0)))


@mcp.tool()
async def decide(item_id: str, decision: str) -> dict:
    """Act on one card from list_pending_decisions. decision: 'yes' (the card's
    yes_label, e.g. couple the receipt = Bezala DRAFT, never submitted; approve the
    trip), 'no' (no_label, e.g. never suggest this pair again; reject the trip) or
    'later' (skip for now). Only on Mikko's word. undo_last_decision reverts."""
    from app.services.decision_queue import decide as _decide

    return await _run(_decide, item_id, decision, source="mcp")


@mcp.tool()
async def undo_last_decision() -> dict:
    """Undo the latest queue decision (uncouples a coupled receipt - deletes the
    Bezala draft - un-approves/un-rejects a trip, restores a subscription …)."""
    from app.services.decision_queue import undo_last

    return await _run(undo_last)


@mcp.tool()
async def expect_refund(bill_line_id: str, note: str | None = None) -> dict:
    """Mark a Bezala card line whose money should come back (e.g. cancelled
    trip). A later card line with a negative amount from the same vendor (same
    absolute amount ±1 %) is linked to it automatically; after 30 days without a
    refund it shows up in list_missing and the decision queue. Bezala Bot only."""
    from app.services.refunds import expect_refund as _expect

    return await _run(_expect, bill_line_id, note)


@mcp.tool()
async def list_subscriptions() -> dict:
    """Read-only: subscriptions on autopilot (vendor, expected amount, account Mikko
    chose earlier, active/suggested). Active subscriptions are coupled automatically
    when receipt and card line match, with description e.g. 'Cursor subscription -
    Nov 2026'. Suggestions are confirmed via the decision queue."""
    from app.services.subscriptions import list_subscriptions as _list

    return await _run(lambda db: {"subscriptions": _list(db)})


# ---------- C50 ----------


@mcp.tool()
async def get_emissions_summary(year: int = 2026) -> dict:
    """C53/C54 - Admin's seven report rows for the year (`admin`) and per trip
    (`per_trip[].admin`): short (<700 km) / medium (700-3700) / long (3700+)
    flights km per flight leg, train km, bus km, non-company car km (taxi, own
    car) and hotel nights; `estimate` marks rows that contain route-table or
    assumed values, `missing` counts legs without km (`per_trip[].missing_legs`
    with reason). Company car (home - HEL, plug-in hybrid) is separate in
    `company_car_info_only` and NOT in Admin's totals. Also by_mode, kWh/litres,
    trips_rejected_not_counted. No kg CO2e - Admin applies own factors.
    Same numbers as the Trips view and export_trip_legs."""
    from app.services import trip_legs

    return await _run(trip_legs.get_emissions_summary, year)


@mcp.tool()
async def update_trip_leg(
    leg_id: int,
    km: float | None = None,
    mode: str | None = None,
    date: str | None = None,
    from_place: str | None = None,
    to_place: str | None = None,
    energy_type: str | None = None,
    energy_amount: float | None = None,
    energy_unit: str | None = None,
    source: str | None = None,
    is_estimate: bool | None = None,
    note: str | None = None,
    flight_category: str | None = None,
    excluded: bool | None = None,
) -> dict:
    """C53 - correct one leg (leg_id from get_trip 'legs'). Only Bezala Bot's own
    table changes. mode: flight|train|taxi|bus|car_phev|car_private (own car);
    energy_type: jet_fuel|electricity|petrol|none; energy_unit: kWh|l; source:
    receipt|airport_table|route_table|assumption|manual. flight_category:
    short|medium|long or '' (= from km). A km you set becomes source=manual and
    is never overwritten. Setting km on a car_phev leg
    recalculates kWh/litres unless energy_amount is given. excluded=true drops
    the leg (e.g. Mikko got a lift instead of driving). Edited legs are locked
    (never recalculated). Ask Mikko for the value - never guess."""
    from app.services import trip_legs

    return await _run(trip_legs.update_trip_leg, leg_id, km=km, mode=mode, date_=date,
                      from_place=from_place, to_place=to_place, energy_type=energy_type,
                      energy_amount=energy_amount, energy_unit=energy_unit, source=source,
                      is_estimate=is_estimate, note=note, flight_category=flight_category,
                      excluded=excluded)


@mcp.tool()
async def add_trip_leg(
    trip_id: int,
    mode: str,
    date: str,
    km: float | None = None,
    from_place: str | None = None,
    to_place: str | None = None,
    energy_type: str = "none",
    energy_amount: float | None = None,
    energy_unit: str | None = None,
    is_estimate: bool = False,
    note: str | None = None,
) -> dict:
    """C53 - add a leg Mikko tells you about (source=manual), e.g. a train
    without receipt. Bezala Bot's own table only."""
    from app.services import trip_legs

    return await _run(trip_legs.add_trip_leg, trip_id, mode=mode, date_=date, km=km,
                      from_place=from_place, to_place=to_place, energy_type=energy_type,
                      energy_amount=energy_amount, energy_unit=energy_unit,
                      is_estimate=is_estimate, note=note)


@mcp.tool()
async def export_trip_legs(year: int = 2026, format: str = "json",
                           mode: str = "detail") -> dict:
    """C53/C54 - export for Admin. mode: 'detail' = one row per leg (trip_id, date,
    mode, from, to, km, km_reason, energy, estimate yes/no, source, admin_category,
    receipt + Bezala id); 'admin' = one row per trip and year with exactly Admin's
    seven columns (Short/Medium/Long flights km, Train km, Bus km, Non-company car
    km, Hotel nights) plus a Total row - company car NOT included; 'route_table' =
    the km route table (operator, from, to, km, symmetric, reference).
    format: 'json' (rows) or 'csv' (text). English and plain '-' only."""
    from app.services import trip_legs

    def work(db, y, fmt, md):
        if md not in ("detail", "admin", "route_table"):
            return {"error": "mode must be detail, admin or route_table"}
        if fmt == "csv":
            return {"year": y, "format": "csv", "mode": md,
                    "csv": trip_legs.export_csv(db, y, md)}
        rows = (trip_legs.admin_export_rows(db, y) if md == "admin"
                else trip_legs.route_table_rows() if md == "route_table"
                else trip_legs.export_rows(db, y))
        return {"year": y, "format": "json", "mode": md, "count": len(rows), "rows": rows}

    return await _run(work, year, (format or "json").lower(), (mode or "detail").lower())


@mcp.tool()
async def list_suggestable_booking_confirmations() -> dict:
    """C53 Del 0b dry run (read-only): booking confirmations / duplicates
    (duplicate_of set, or a non e-ticket with the same booking_ref as an e-ticket)
    that could be suggested before C53, per vendor, with the original and where it
    is coupled. Since C53 only the original e-ticket receipt is suggested."""
    from app.services.c53_receipts import confirmation_dry_run

    return await _run(confirmation_dry_run)


@mcp.tool()
async def list_bot_bezala_writes(days: int = 14) -> dict:
    """Read-only: every Bezala transaction Bezala Bot wrote to in the last `days`
    days (id, vendor, amount, what was written, current status in Bezala read
    live) plus the kill switch status. Nothing is changed or withdrawn - Mikko
    decides himself what to do with transactions that left draft state."""
    from app.services.bezala_writes_report import list_bot_bezala_writes as _list

    return await _run(_list, days)


@mcp.tool()
async def inspect_bezala_transaction(transaction_id: str | None = None,
                                     bill_line_id: str | None = None) -> dict:
    """Read-only (GET only): a Bezala transaction's `state` and key fields, to
    compare a draft with an expense waiting for approval. Give transaction_id, or
    bill_line_id to find the transaction that references that card line. Draft =
    state "draft"; waiting for approval = "reviewing" (docs/bezala_states.md).
    Nothing is written to Bezala."""
    import asyncio

    from app.services.c51_bezala_inspect import inspect as _inspect

    return await asyncio.to_thread(_inspect, transaction_id, bill_line_id)


@mcp.tool()
async def probe_bezala_conditional_fields(transaction_id: str) -> dict:
    """C62 read-only (GET only): try a fixed list of Bezala GET addresses to find
    where a draft's Vehicle / Sträcka (km) / Antal passagerare (conditional_values,
    field_id 1/2/3) can be read. Nothing is written to Bezala."""
    import asyncio

    from app.services.c62_conditional import probe_reads

    return await asyncio.to_thread(probe_reads, str(transaction_id))


@mcp.tool()
async def list_probably_handled_receipts() -> dict:
    """Read-only review list (not the swipe queue): receipts older than 14 days
    without an open company card line and without a match in Bezala - probably
    handled elsewhere. Also counts receipts marked already_handled_in_bezala."""
    from app.models import ProcessedMessage
    from app.services.c50_handled import ALREADY_HANDLED, list_probably_handled

    def work(db):
        items = list_probably_handled(db)
        already = (db.query(ProcessedMessage)
                   .filter(ProcessedMessage.waiting_status == ALREADY_HANDLED).count())
        return {"probably_handled_elsewhere": items, "count": len(items),
                "already_handled_in_bezala": already}

    return await _run(work)


@mcp.tool()
async def get_c50_rerun_report(run_again: bool = False) -> dict:
    """Before/after report of the C50 re-run (Apple card lines, already handled
    in Bezala). run_again=True runs it once more (changes only Bezala Bot's own
    receipt statuses - nothing is written to Bezala)."""
    from app.services.c50_handled import _counts, rerun
    from app.services.cleanup_log import report

    def work(db):
        if run_again:
            return rerun(db, force=True)
        return {"now": _counts(db),
                "log": {r: report(db, r) for r in
                        ("back_to_waiting", "already_handled_in_bezala",
                         "probably_handled_elsewhere")}}

    return await _run(work)


# ---------- C52 ----------


@mcp.tool()
async def run_bolt_description_test() -> dict:
    """C52 Del 1 - the ONE description test Mikko approved on 2026-11-22 12:30:
    PUT Bezala transaction 5579365 with only
    {"description": "Bolt.new Pro subscription, 16 November 2026"}, through the
    write guard (read before/after, kill switch if the status changes). No
    `state`, no other field, no other transaction. Runs at most once; returns
    state, approvers and description before and after."""
    from app.services.c52 import run_bolt_test

    return await _run(run_bolt_test)


@mcp.tool()
async def get_vat_read_report(message_ids: list[str] | None = None,
                              reread: bool = False) -> dict:
    """C52 Del 3 - read-only report of VAT reading from receipts (last 90 days):
    how many receipts have VAT read, how many were read without VAT found, and
    which are not read yet and why. message_ids shows those receipts in detail.
    reread=True reads the VAT again for receipts without it (changes only Bezala
    Bot's own vat_info - nothing is written to Bezala) and logs each result."""
    from app.services.c52 import reread_vat, vat_report

    def work(db):
        out = {}
        if reread:
            out["reread"] = reread_vat(db)
        out.update(vat_report(db, message_ids))
        return out

    return await _run(work)


@mcp.tool()
async def list_already_handled_candidates() -> dict:
    """C52 Del 4 - DRY RUN, changes nothing: receipts with status 'saved', no
    waiting status and no Bezala transaction (shown in no list) matched against
    Mikko's expenses in Bezala (date +-1 day, amount incl. NOK/SEK vs EUR with
    tolerance, vendor). 'matches' = date+amount+vendor, 'amount_and_date_only' =
    vendor not found in the description. Show the list to Mikko before anything
    is applied with mark_already_handled."""
    from app.services.c52 import find_matches

    return await _run(find_matches)


@mcp.tool()
async def mark_already_handled(message_ids: list[str]) -> dict:
    """C52 Del 4 - ONLY after Mikko said yes to the dry-run list: sets Bezala
    Bot's own status 'already_handled_in_bezala' on these receipts (must be in
    the current list_already_handled_candidates result). Nothing is written to
    Bezala."""
    from app.services.c52 import apply_already_handled

    return await _run(apply_already_handled, message_ids)


# ---------- C48 ----------


@mcp.tool()
async def list_bezala_accounts() -> dict:
    """Read-only: Bezala's account list (id, name) and the accounts Mikko chose
    earlier per vendor. Use it before set_subscription_account."""
    from app.services.subscriptions import bezala_accounts

    return await _run(bezala_accounts)


@mcp.tool()
async def set_subscription_account(subscription_id: int, account: int | str | None) -> dict:
    """Set the Bezala account (numeric id, or the exact account name as Bezala
    shows it, e.g. 'AI työkalut', from list_bezala_accounts) for a
    subscription. ONLY with the account Mikko names - never guess. A
    subscription without account cannot be activated. account=None clears it."""
    from app.services.subscriptions import bezala_accounts, set_account

    def work(db):
        accounts = bezala_accounts(db).get("accounts") if account is not None else None
        return set_account(db, subscription_id, account, accounts=accounts)

    return await _run(work)


@mcp.tool()
async def get_receipt_inbox_info() -> dict:
    """Read-only: the +kvitto address receipts can be mailed to, which senders
    are accepted, and the latest mails to it (time, attachments, accepted /
    rejected). The addresses are not secret."""
    from app.services.kvitto_mail import inbox_info

    return await _run(inbox_info)


@mcp.tool()
async def list_trip_suggestions() -> dict:
    """C56, read-only: every receipt-based AI suggestion (the 'Suggestions' list
    on /trips) with title, dates, amount, receipt count, whether a calendar trip
    already covers it (covered_by = overlapping dates + same city), whether it is
    before 2026 (old), empty, a duplicate of another suggestion (duplicate_of),
    and receipt_warnings (same amount twice, tiny amounts). merge_plan shows what
    merge_duplicate_trip_suggestions would do. Nothing is changed."""
    from app.services.c56_trip_suggestions import analyse

    def slim(db):
        data = analyse(db)
        for r in data["suggestions"]:
            r["receipts"] = [
                {"vendor": m["vendor"], "amount": m["amount"], "currency": m["currency"],
                 "receipt_date": m["receipt_date"]} for m in r.pop("messages")]
        return data

    return await _run(slim)


@mcp.tool()
async def compare_trip_amounts() -> dict:
    """C56, read-only: amount per calendar trip before (every receipt of every
    overlapping AI suggestion) and after (each receipt on exactly one trip by
    checklist/date/city; the same ticket captured twice counted once).
    unassigned = receipts that fit no trip or several equally (not guessed).
    Nothing is changed."""
    from app.services.c56_receipt_allocation import compare

    return await _run(compare)


@mcp.tool()
async def list_cancelled_bookings() -> list[dict]:
    """C57, read-only: bookings bought and refunded (e.g. Finnair 8HZCXX), counted
    as zero. One row per booking: vendor, booking_ref, amount, purchase/refund
    date, the bot's receipts (message_ids), reason, and counted=true if Mikko
    said it should count after all. Nothing in Bezala is touched."""
    from app.services.c57_cancelled import list_cancelled

    return await _run(list_cancelled)


@mcp.tool()
async def set_cancelled_booking(booking: str, cancelled: bool, note: str | None = None) -> dict:
    """C57: undo/redo for a cancelled booking (key 'booking' from
    list_cancelled_bookings, e.g. 'finnair:8HZCXX'). cancelled=false = count it
    again; cancelled=true = back to automatic. Only Bezala Bot's own view
    changes; nothing is deleted or sent to Bezala. Ask Mikko first."""
    from app.services.c57_cancelled import set_cancelled_booking as _set

    return await _run(_set, booking, cancelled, note)


@mcp.tool()
async def merge_duplicate_trip_suggestions(apply: bool = False) -> dict:
    """C56: merge duplicate AI suggestions (overlapping dates + same city) into
    the one with the most receipts; the others' receipts move there and they get
    status 'merged' (no rows deleted). apply=false (default) = dry-run. Only use
    apply=true after Mikko has seen the dry-run list and said yes. Nothing is
    sent to Bezala or Netvisor."""
    from app.services.c56_trip_suggestions import merge_duplicates

    return await _run(merge_duplicates, apply)


@mcp.tool()
async def get_cleanup_report(rule: str | None = None, limit: int = 50) -> dict:
    """Read-only: what the data cleanup did (saved in the database instead of
    only in Railway logs). counts per rule + rows. Rules: empty_error (error rows
    without mail context - reason = the error), pre_2026 (receipts moved to the
    trash), date_corrected, finnair_duplicate, hotel_duplicate, duplicate,
    amount_missing (2026 receipts without amount, re-read result),
    ignored_private_source, purpose_fixed, purpose_report,
    subscription_dismissed, subscription_merged."""
    from app.services.cleanup_log import report

    return await _run(report, rule, limit)


@mcp.tool()
async def investigate_vendor(query: str, limit: int = 50) -> dict:
    """Read-only: where do a vendor's rows come from? Company card lines (all
    statuses), card charges seen on coupled receipts, and receipts with source
    (work/private Gmail, mobile, +kvitto, manual), sender and waiting status."""
    from app.services.c48_migrations import investigate_vendor as _inv

    return await _run(_inv, query, limit)


@mcp.tool()
async def list_receipts_without_amount() -> dict:
    """Read-only: processed 2026 receipts whose amount is missing, and whether
    they can be re-read. Such receipts are never coupled automatically."""
    from app.services.amount_missing import list_without_amount

    return await _run(list_without_amount)


# ---------- ASGI: auth + Streamable HTTP ----------

_manager: StreamableHTTPSessionManager | None = None


@contextlib.asynccontextmanager
async def lifespan():
    """Startas från FastAPI:s lifespan. Ny manager per uppstart (tester
    startar appen flera gånger, och run() får bara köras en gång)."""
    global _manager
    _manager = StreamableHTTPSessionManager(
        app=mcp._mcp_server, stateless=True, json_response=True,
    )
    async with _manager.run():
        try:
            yield
        finally:
            _manager = None


def _check_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        value = ""
    db = SessionLocal()
    try:
        return validate_access_token(db, value.strip(), resource_url(request))
    finally:
        db.close()


class McpEndpoint:
    """Rå ASGI-app för /mcp: validera token, lämna sedan över till SDK:n."""

    async def __call__(self, scope, receive, send):
        request = Request(scope, receive)
        error = await anyio.to_thread.run_sync(_check_token, request)
        if error is not None:
            meta = resource_metadata_url(request)
            response = JSONResponse(
                {"error": "invalid_token", "error_description": error},
                status_code=401,
                headers={
                    "WWW-Authenticate": (
                        f'Bearer error="invalid_token", '
                        f'error_description="{error}", '
                        f'resource_metadata="{meta}"'
                    ),
                },
            )
            await response(scope, receive, send)
            return
        if _manager is None:
            await JSONResponse({"error": "MCP not started"}, status_code=503)(
                scope, receive, send
            )
            return
        await _manager.handle_request(scope, receive, send)
