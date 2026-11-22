# Bezala transaction states (C51)

Read-only investigation, 2026-11-22. Only `GET` was used. Nothing was written to Bezala.

## Findings

| Bezala UI | `state` value | Source |
|---|---|---|
| Utkast (draft, not sent for approval) | `draft` | `GET /transactions` (all 421 expenses, read via `get_card_line` after the C51 deploy): the only states are `draft`, `reviewing`, `accounted` |
| Väntar på attest (waiting for approval) | `reviewing` | GET on 5579319 (Arlanda Express), 5579032 (Scandic), 5579031 (Anthropic), 5575542, 5575527, 5575526, 5575521, 5575507 - all `state: "reviewing"` with `approvers: ["Kytömäki Riikka"]` |
| Bokförd (accounted) | `accounted` | same list |

**`unapproved` never occurs.** Old code (C18-C21) assumed `unapproved` = draft and *sent*
`state: "unapproved"`. That value is not a Bezala state; the expenses that got it ended in
`reviewing`.

Fields in a transaction (from the list): `state`, `type`, `date`, `description`,
`billed_amount`, `billed_amount_currency`, `original_amount`, `original_amount_currency`,
`credit_account_id`, `credit_account_name`, `debit_account_id`, `cost_center_id(s)`,
`vat_lines`, `tax_percentage`, `approvers`, `current_approvers_ids`, `transaction_approvers`,
`approved_by`, `bill_id`, `connected_to_bill_line`, `not_connected_to_card`, `paid_at`, ...

Other fields seen on an expense waiting for approval: `approvers` (list of names). A draft
has no approver.

### What sent the 8 expenses for approval

All 8 were written by the old coupling flow: `POST /attachments` with `draft=1, state=unapproved`
followed by `PUT /transactions/{id}` with `state: "unapproved"` + `date` + `bill_id/bill_line_id/
connected_to_bill_line`. All 8 ended in `reviewing`. The Bolt draft, coupled without the PUT,
stayed a draft. Sending `state` (even `"unapproved"`) is therefore treated as the trigger.

## Bezala API documentation

No public API documentation was found (`app.bezala.com/api/docs`, `/apidocs`, `/api-docs`,
`www.bezala.com/api` all return 404). Everything we know comes from earlier notes in this repo
(`app/services/bezala_client.py`, FAS 5.24-5.28, C18-C21, C50) and from GET responses.

Known endpoints (base `https://app.bezala.com/api`):

- `GET /transactions`, `GET /transactions/{id}` - read (safe)
- `GET /missing_receipts`, `/accounts`, `/cost_centers`, `/vat_rates` - read (safe)
- `POST /attachments` (multipart `file`, `draft=1`, `bill_line_id`) - creates a draft coupled to a card line
- `PUT /transactions/{id}` with body `{"transaction": {...}}` - update
- `DELETE /transactions/{id}` - delete

## Rules built into the bot (C51)

- **`state` is never sent** in any Bezala write (POST form or PUT body). `bezala_write_guard`
  refuses the field everywhere (`FORBIDDEN_WRITE_FIELDS`).
- Before a PUT the transaction is read. The write is allowed **only** when `state` is in
  `DRAFT_STATES = {"draft"}`. Missing `state`, `unapproved`, `reviewing`, `accounted` or
  anything else → refused.
- After the PUT the transaction is read again. Any change in the status fields, or a state
  outside `DRAFT_STATES` → CRITICAL, kill switch in the database, all Bezala writes stopped.
- After `POST /attachments`, a new transaction with a state outside `DRAFT_STATES` trips the
  kill switch.
- `AUTO_COUPLE_ENABLED` and `BEZALA_METADATA_PUT_ENABLED` stay off.

## Verification of the draft state (read-only)

After the C51 deploy, `get_card_line("2513372")` (MCP, read-only) and the new
`inspect_bezala_transaction(bill_line_id="2513372")` show the Bolt draft's `state`.

## Proposed test - NOT executed, waits for approval by Mikko or C7

Goal: see whether a PUT with only `description` (no `state`) keeps the Bolt draft a draft.

Preconditions (checked by the guard, stops otherwise):
1. `GET /transactions/<BOLT_TX_ID>` returns `state: "draft"`.
2. Kill switch is off (`writes_halted=false`).

Exact call (one request, body has only `description`):

```
PUT https://app.bezala.com/api/transactions/<BOLT_TX_ID>
Content-Type: application/json

{"transaction": {"description": "Bolt.new Pro subscription, 16 November 2026"}}
```

followed by

```
GET https://app.bezala.com/api/transactions/<BOLT_TX_ID>
```

Expected: `state` still `"draft"`, no `approvers`, description updated.
If `state` is anything else: the guard trips the kill switch and Mikko withdraws the expense
in Bezala by hand.

How it would be run once approved: a one-off call to `guard.guarded_put(client, <BOLT_TX_ID>,
{"description": ...})` (does GET before + PUT + GET after). `BEZALA_METADATA_PUT_ENABLED` is
not changed for this - the guarded call is made directly, once.
