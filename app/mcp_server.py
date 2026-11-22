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
    "list_bot_bezala_writes(days) is a read-only report of what the bot wrote to Bezala."
)

mcp = FastMCP("Bezala Bot", instructions=INSTRUCTIONS)


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
    linked receipts, flight options and the prepared Netvisor line description."""
    return await _run(edit.get_trip, trip_id)


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
    Works on edited trips too. Free meals are added to the Netvisor line in
    English, e.g. '(1 free meal: dinner)'.
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
    expense stays a DRAFT (unapproved) — it is never submitted for approval.
    Only when Mikko explicitly asks. Use list_missing (suggested_receipt) or
    get_card_line to find the message_id first."""
    from app.services.card_line_coupling import couple_receipt as _couple

    return await _run(_couple, bill_line_id, message_id)


@mcp.tool()
async def get_card_line(bill_line_id: str) -> dict:
    """Read-only: a Bezala card line (bill_line) with its status in Bezala Bot,
    the receipt coupled to it (if any), the match score and when/how the coupling
    was made, plus the best suggested receipt if it still has none. May read
    Bezala transactions (GET only) to find the coupled receipt."""
    from app.services.card_line_coupling import get_card_line as _get

    return await _run(_get, bill_line_id)


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


@mcp.tool()
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
async def list_bot_bezala_writes(days: int = 14) -> dict:
    """Read-only: every Bezala transaction Bezala Bot wrote to in the last `days`
    days (id, vendor, amount, what was written, current status in Bezala read
    live) plus the kill switch status. Nothing is changed or withdrawn - Mikko
    decides himself what to do with transactions that left draft state."""
    from app.services.bezala_writes_report import list_bot_bezala_writes as _list

    return await _run(_list, days)


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
