import { expect, test } from '@playwright/test';
import { buildMessages, setupApiMocks } from './fixtures.js';

/* Polish-patch 5.1 — fyra fokuserade tester:
 *  1) Tidsstämpel: naiv ISO utan tz-suffix tolkas som UTC, inte local time.
 *  2) "Total denna vecka" exkluderar skipped-rader.
 *  3) Filter-tab "Alla"-räknaren räknar bara ej-skipped + tabellen matchar.
 *  4) Shift-klick väljer range i Dashboard-tabellen (Gmail/Finder range-ADD). */

const ONE_HOUR = 60 * 60 * 1000;

function skippedRow(overrides = {}) {
  return {
    id: 99,
    message_id: 'gmail-msg-skip',
    sender: 'Newsletter <news@vendor.com>',
    subject: 'Nyhetsbrev — inget kvitto',
    received_at: new Date(Date.now() - 2 * ONE_HOUR).toISOString(),
    processed_at: new Date(Date.now() - 2 * ONE_HOUR).toISOString(),
    file_name: null,
    drive_file_id: null,
    drive_link: null,
    // Backend grupperar "skipped:*" → file_status='skipped' (se adapters.js).
    status: 'skipped:no_pdf',
    error_message: null,
    vendor: null,
    amount: null,
    currency: null,
    receipt_date: null,
    category: null,
    summary: null,
    ai_confidence: null,
    bezala_transaction_id: null,
    bezala_upload_status: null,
    bezala_error_message: null,
    deleted_at: null,
    delete_reason: null,
    ...overrides,
  };
}

test('Timestamp — naiv UTC-iso (utan Z) parsas som UTC, inte local time', async ({
  page,
}) => {
  // Backend skickar isoformat() på naiv datetime, dvs utan Z/offset.
  // parseBackendDate måste lägga på Z så browsern inte feltolkar som local.
  const msgs = buildMessages();
  // 90 minuter sedan i UTC, seriserat naivt (ingen Z).
  const ninetyMinAgoUtc = new Date(Date.now() - 90 * 60 * 1000)
    .toISOString()
    .replace(/\.\d+Z$/, '')
    .replace(/Z$/, '');
  msgs[0].processed_at = ninetyMinAgoUtc;
  msgs[0].received_at = ninetyMinAgoUtc;
  await setupApiMocks(page, { messages: msgs });

  await page.goto('/');
  const row = page.locator('tr[data-row-id="1"]');
  await expect(row).toBeVisible();

  // Relativ tid ska vara "timme" eller "minut" — inte "dag", "månad" eller
  // "år" (vilket skulle hända om strängen tolkades som local time och drev
  // iväg flera timmar i negativ riktning).
  const timeCell = row.locator('td.tbl__time');
  const rendered = (await timeCell.innerText()).toLowerCase();
  expect(rendered).not.toMatch(/dag|day|månad|month|år|year/);
});

test('Dashboard — "Total denna vecka" exkluderar skipped-rader', async ({
  page,
}) => {
  const msgs = buildMessages();
  // Fem bearbetade + två skipped inom 7 dagar.
  msgs.push(skippedRow({ id: 100, message_id: 'skip-100' }));
  msgs.push(
    skippedRow({
      id: 101,
      message_id: 'skip-101',
      status: 'skipped:excluded_subject',
    }),
  );
  await setupApiMocks(page, { messages: msgs });

  await page.goto('/');
  await expect(page.locator('tr[data-row-id="1"]')).toBeVisible();

  // "Total denna vecka" ska visa 5, inte 7.
  const card = page
    .locator('.stat', { hasText: 'Total denna vecka' })
    .first();
  await expect(card.locator('.stat__value')).toHaveText('5');
});

test('Dashboard — filter-tab "Alla" räknar ej-skipped + tabellen matchar', async ({
  page,
}) => {
  const msgs = buildMessages();
  msgs.push(skippedRow({ id: 200, message_id: 'skip-200' }));
  msgs.push(skippedRow({ id: 201, message_id: 'skip-201' }));
  await setupApiMocks(page, { messages: msgs });

  await page.goto('/');
  await expect(page.locator('tr[data-row-id="1"]')).toBeVisible();

  // "Alla"-tab visar 5, inte 7.
  const allTab = page.locator('.fbar__tab', { hasText: 'Alla' });
  await expect(allTab.locator('.fbar__count')).toHaveText('5');

  // Tabellen ska inte innehålla skipped-raderna.
  await expect(page.locator('tr[data-row-id="200"]')).toHaveCount(0);
  await expect(page.locator('tr[data-row-id="201"]')).toHaveCount(0);

  // Antalet radrender:ade ska matcha count.
  const renderedRows = page.locator('tbody tr[data-row-id]');
  await expect(renderedRows).toHaveCount(5);
});

test('Selection — shift-klick väljer range i Dashboard (Gmail/Finder range-ADD)', async ({
  page,
}) => {
  await setupApiMocks(page);
  await page.goto('/');
  await expect(page.locator('tr[data-row-id="1"]')).toBeVisible();

  // 1) Klicka checkbox på rad 1 → ankare sätts på id=1.
  const cb1 = page.locator('tr[data-row-id="1"] [data-testid="bulk-checkbox"]');
  await cb1.click();
  await expect(cb1).toBeChecked();

  // 2) Shift+klick på rad 4 → range 1..4 ska bli markerat.
  const cb4 = page.locator('tr[data-row-id="4"] [data-testid="bulk-checkbox"]');
  await cb4.click({ modifiers: ['Shift'] });

  for (const id of [1, 2, 3, 4]) {
    const cb = page.locator(`tr[data-row-id="${id}"] [data-testid="bulk-checkbox"]`);
    await expect(cb).toBeChecked();
  }
  // Rad 5 ska förbli omarkerad.
  const cb5 = page.locator('tr[data-row-id="5"] [data-testid="bulk-checkbox"]');
  await expect(cb5).not.toBeChecked();

  // Bulk-baren visar 4.
  const bulkBar = page.getByTestId('bulk-bar');
  await expect(bulkBar).toBeVisible();
  await expect(bulkBar).toContainText('4');
});
