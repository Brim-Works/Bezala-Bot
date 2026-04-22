import { expect, test } from '@playwright/test';
import { buildMessages, setupApiMocks } from './fixtures.js';

/* Gate 3: sortering + datumfiltrering i Dashboard + Granska-kö. */

test.beforeEach(async ({ page }) => {
  // Playwright ger varje test egen browser-context, så localStorage är
  // redan tom. Bara mocks behöver sättas upp.
  await setupApiMocks(page);
});

test('Gate 3 — Dashboard default sort = receipt_date DESC (senaste överst)', async ({
  page,
}) => {
  await page.goto('/');
  const rows = page.locator('table.tbl tbody tr[data-row-id]');
  await expect(rows.first()).toHaveCount(1); // exists
  // Fixture receipt_dates: id1=2026-04-20, id2=2026-04-15, id3=2026-04-10,
  // id4=2026-04-21, id5=2026-04-20.
  // Senaste är id 4 (2026-04-21) → ska vara först.
  const firstRowId = await rows.first().getAttribute('data-row-id');
  expect(firstRowId).toBe('4');
});

test('Gate 3 — klick på Belopp-rubrik sorterar efter amount DESC → ASC', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByTestId('sort-amount').click();
  // Default-klick → DESC. Största amount = 1850 (id 3 Scandic).
  const rows = page.locator('table.tbl tbody tr[data-row-id]');
  const firstId = await rows.first().getAttribute('data-row-id');
  expect(firstId).toBe('3');

  // Klicka igen → ASC. Minsta amount = 23.5 (id 5 Uber).
  await page.getByTestId('sort-amount').click();
  const firstIdAsc = await rows.first().getAttribute('data-row-id');
  expect(firstIdAsc).toBe('5');
});

test('Gate 3 — klick på Leverantör-rubrik sorterar alfabetiskt', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByTestId('sort-vendor').click();
  // DESC alphabetic → Uber (U) överst bland [Clas Ohlson, Finnair, SL, Scandic, Uber]
  const rows = page.locator('table.tbl tbody tr[data-row-id]');
  const firstId = await rows.first().getAttribute('data-row-id');
  expect(firstId).toBe('5'); // Uber

  await page.getByTestId('sort-vendor').click();
  // ASC → Clas Ohlson först
  const firstIdAsc = await rows.first().getAttribute('data-row-id');
  expect(firstIdAsc).toBe('4'); // Clas Ohlson
});

test('Gate 3 — sort-val persisterar i localStorage efter F5', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByTestId('sort-amount').click();
  await page.getByTestId('sort-amount').click(); // ASC

  // Verifiera localStorage
  const stored = await page.evaluate(() => ({
    col: window.localStorage.getItem('bb_sort_col'),
    dir: window.localStorage.getItem('bb_sort_dir'),
  }));
  expect(stored).toEqual({ col: 'amount', dir: 'asc' });

  // Reload sidan — sort-val ska återställas från localStorage
  await page.reload();
  const rows = page.locator('table.tbl tbody tr[data-row-id]');
  const firstId = await rows.first().getAttribute('data-row-id');
  expect(firstId).toBe('5'); // Uber (lägsta belopp, ASC)
});

test('Gate 3 — datumfilter "Senaste månaden" tar bort äldre rader', async ({
  page,
}) => {
  // Skapa en uppsättning meddelanden där några har receipt_date > 60 dagar
  // (2026-02-15) och resten är färska (2026-04-xx — ligger inom 30d cutoff).
  // "Today" i testet = 2026-04-22 per context.
  const messages = buildMessages();
  messages.push({
    ...messages[0],
    id: 99,
    message_id: 'gm-old-99',
    receipt_date: '2026-02-15',
    processed_at: '2026-02-15T12:00:00Z',
    vendor: 'GammaOld',
  });
  await setupApiMocks(page, { messages });

  await page.goto('/');
  // Default "all" → 99 finns
  await expect(
    page.locator('table.tbl tbody tr[data-row-id="99"]'),
  ).toHaveCount(1);

  // Välj "Senaste månaden" (30 dagar) → 99 försvinner
  await page.getByTestId('date-filter').selectOption('last30d');
  await expect(
    page.locator('table.tbl tbody tr[data-row-id="99"]'),
  ).toHaveCount(0);
});

test('Gate 3 — datumfilter-val persisterar i localStorage efter F5', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByTestId('date-filter').selectOption('last90d');

  const stored = await page.evaluate(() =>
    window.localStorage.getItem('bb_date_filter'),
  );
  expect(stored).toBe('last90d');

  await page.reload();
  await expect(page.getByTestId('date-filter')).toHaveValue('last90d');
});

test('Gate 3 — pil visas på aktiv kolumnrubrik', async ({ page }) => {
  await page.goto('/');
  // receipt_date är default-sort (DESC) → dess knapp ska ha is-active
  await expect(page.getByTestId('sort-receipt_date')).toHaveClass(/is-active/);
  // Arrow inuti ska vara "↓" för DESC
  const activeArrow = page
    .getByTestId('sort-receipt_date')
    .locator('.tbl__sort-arrow--active');
  await expect(activeArrow).toHaveText('↓');
});

test('Gate 3 — Granska-kö default sort = receipt_date DESC', async ({
  page,
}) => {
  await page.goto('/review');
  // id 1 har receipt_date 2026-04-20 — senaste bland pending (1,2,3)
  // → ska vara först i kön
  const items = page
    .getByTestId('review-queue')
    .locator('[data-testid^="queue-item-"]');
  const firstId = await items.first().getAttribute('data-testid');
  expect(firstId).toBe('queue-item-1');
});
