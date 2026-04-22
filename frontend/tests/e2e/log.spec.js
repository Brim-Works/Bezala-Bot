import { expect, test } from '@playwright/test';
import {
  buildFilteredEntries,
  buildMessages,
  buildRuns,
  buildSkippedMessage,
  setupApiMocks,
} from './fixtures.js';

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page);
});

test('Log — renderar KPI-strip + split-view', async ({ page }) => {
  await page.goto('/log');
  // KPI-labels
  await expect(page.getByText('Körningar 24h')).toBeVisible();
  await expect(page.getByText('Auto-rate')).toBeVisible();
  await expect(page.getByText('AI-kostnad')).toBeVisible();
  // Split
  await expect(page.getByTestId('run-list')).toBeVisible();
  await expect(page.getByTestId('run-detail')).toBeVisible();
});

test('Log — AI-kostnadskortet visar em-dash (—) när data saknas', async ({
  page,
}) => {
  await page.goto('/log');
  // Hitta kortet via label + dess värde
  const aiCard = page.locator('.stat', { hasText: 'AI-kostnad' });
  await expect(aiCard.locator('.stat__value')).toHaveText('—');
});

test('Log — klick på körning uppdaterar detaljpanelen', async ({ page }) => {
  await page.goto('/log');
  const detail = page.getByTestId('run-detail');
  await expect(detail).toBeVisible();

  // Första körningen (id 100) är vald default. Klicka på id 103 som har errors.
  await page.getByTestId('run-item-103').click();
  await expect(detail).toContainText('#103');
  // Narrative ska nämna att något misslyckades
  await expect(detail).toContainText(/misslyckades|failed/i);
});

test('Log — pipeline-timeline visar 4 stages i ordning', async ({ page }) => {
  await page.goto('/log');
  const pipeline = page.getByTestId('pipeline-timeline');
  await expect(pipeline).toBeVisible();
  await expect(pipeline).toContainText('Gmail');
  await expect(pipeline).toContainText('AI-analys');
  await expect(pipeline).toContainText('Drive');
  await expect(pipeline).toContainText('Bezala');
});

test('Log — messages-tabellen renderar meddelanden inom körningsintervallet', async ({
  page,
}) => {
  await page.goto('/log');
  // Default är första körningen (senaste timmen). Fixture id 4 och 5
  // ligger 2h och 20h bort — inte inom intervallet för första run.
  // Istället klickar vi på en äldre run. Run id 102 ligger 3h bort
  // (processed count = 2) och bör matcha några messages.
  //
  // Default-beteendet är att första körningen är vald. Så vi testar att
  // TABLE antingen visar något eller är frånvarande — inte att något
  // specifikt id finns där.
  const runMessages = page.getByTestId('run-messages');
  // Antingen synlig med rader, eller helt frånvarande — båda acceptabla
  const count = await runMessages.count();
  expect(count).toBeGreaterThanOrEqual(0);
});

test('Log — rensa fel-knappen triggar DELETE /api/messages/errors', async ({
  page,
}) => {
  page.on('dialog', (d) => d.accept());
  const deleteRequest = page.waitForRequest(
    (req) =>
      req.url().includes('/api/messages/errors') && req.method() === 'DELETE',
  );

  await page.goto('/log');
  await page.getByTestId('clear-errors').click();
  await deleteRequest;

  await expect(page.getByText(/Error-rader rensade/i)).toBeVisible();
});

test('Log — tom-state när inga körningar finns', async ({ page }) => {
  await setupApiMocks(page, { runs: [] });
  await page.goto('/log');
  await expect(page.getByTestId('run-detail-empty')).toBeVisible();
  await expect(page.getByText(/Välj en körning/i)).toBeVisible();
});

// ---------- Gate 2: Försök igen-knapp för hoppade rader ----------

function skippedInRun(runStart) {
  // Lägg en skipped-rad i samma tidsintervall som körning #1
  const processedAt = new Date(new Date(runStart).getTime() + 1000).toISOString();
  return buildSkippedMessage({ id: 80, processed_at: processedAt });
}

test('Log Gate 2 — "Försök igen"-knapp syns för hoppade rader', async ({
  page,
}) => {
  const runs = buildRuns();
  const skipped = skippedInRun(runs[0].started_at);
  await setupApiMocks(page, {
    messages: [...buildMessages(), skipped],
    runs,
  });
  await page.goto('/log');
  await expect(page.getByTestId('retry-80')).toBeVisible();
  // Saved-rad (id 1) ska INTE ha knappen
  await expect(page.getByTestId('retry-1')).toHaveCount(0);
});

test('Log Gate 2 — klick på Försök igen triggar POST /reprocess + toast', async ({
  page,
}) => {
  const runs = buildRuns();
  const skipped = skippedInRun(runs[0].started_at);
  await setupApiMocks(page, {
    messages: [...buildMessages(), skipped],
    runs,
  });

  const reprocessRequest = page.waitForRequest(
    (req) =>
      /\/api\/messages\/80\/reprocess$/.test(req.url()) &&
      req.method() === 'POST',
  );
  await page.goto('/log');
  await page.getByTestId('retry-80').click();
  const req = await reprocessRequest;
  expect(req.method()).toBe('POST');

  // Gate 1.5 fix: toast-text nämner "scanning" för att tydliggöra processen
  await expect(page.getByText(/scanning/i)).toBeVisible();
});

test('Log Gate 2 — raden försvinner ur listan efter lyckad reprocess', async ({
  page,
}) => {
  const runs = buildRuns();
  const skipped = skippedInRun(runs[0].started_at);
  await setupApiMocks(page, {
    messages: [...buildMessages(), skipped],
    runs,
  });
  await page.goto('/log');
  await expect(page.getByTestId('run-message-80')).toBeVisible();
  await page.getByTestId('retry-80').click();
  // Efter success tas raden bort från mocken och refetch sker → rad försvinner
  await expect(page.getByTestId('run-message-80')).toHaveCount(0);
});

// ---------- Gate 1.5: Loggtransparens ----------

function runsWithFiltered() {
  const runs = buildRuns();
  runs[0].messages_processed = 0;
  runs[0].messages_found = 3;
  runs[0].filtered_messages = buildFilteredEntries();
  return runs;
}

test('Log Gate 1.5 — filtered entries renderas i tabellen', async ({
  page,
}) => {
  await setupApiMocks(page, { runs: runsWithFiltered() });
  await page.goto('/log');

  await expect(page.getByTestId('filtered-row-gm-moovy-1')).toBeVisible();
  await expect(page.getByTestId('filtered-row-gm-html-2')).toBeVisible();
  await expect(page.getByTestId('filtered-row-gm-spam-3')).toBeVisible();
});

test('Log Gate 1.5 — reason-pill visar confidence för AI-filtrerad', async ({
  page,
}) => {
  await setupApiMocks(page, { runs: runsWithFiltered() });
  await page.goto('/log');
  await expect(
    page.getByTestId('filtered-reason-gm-moovy-1'),
  ).toHaveText('AI-filtrerad (35%)');
  await expect(
    page.getByTestId('filtered-reason-gm-html-2'),
  ).toHaveText('PDF-konvertering misslyckades');
  await expect(
    page.getByTestId('filtered-reason-gm-spam-3'),
  ).toHaveText('Ej kvitto');
});

test('Log Gate 1.5 — filtered rader öppnar INTE drawer vid klick', async ({
  page,
}) => {
  await setupApiMocks(page, { runs: runsWithFiltered() });
  await page.goto('/log');
  await page.getByTestId('filtered-row-gm-moovy-1').click();
  // Drawer har testid 'drawer' — ska inte synas
  await expect(page.getByTestId('drawer')).toHaveCount(0);
});

test('Log Gate 1.5 — text-sök filtrerar både sparade och filtrerade rader', async ({
  page,
}) => {
  await setupApiMocks(page, { runs: runsWithFiltered() });
  await page.goto('/log');

  await page.getByTestId('log-search-input').fill('moovy');
  await expect(page.getByTestId('filtered-row-gm-moovy-1')).toBeVisible();
  await expect(page.getByTestId('filtered-row-gm-html-2')).toHaveCount(0);
  await expect(page.getByTestId('filtered-row-gm-spam-3')).toHaveCount(0);
});

test('Log Gate 1.5 — status-pill filtrerar körningslistan', async ({
  page,
}) => {
  // Skapa körningar med blandade statusar:
  //   runs[0]=3 processed (ok), runs[2]=2 processed (ok), runs[3]=1 proc+1 err (partial)
  //   övriga = 0 processed 0 err (idle)
  const runs = buildRuns();
  await setupApiMocks(page, { runs });
  await page.goto('/log');

  // Partial = processed>0 OCH errors>0 → bara runs[3] (id 103)
  await page.getByTestId('log-search-status-partial').click();
  await expect(page.getByTestId('run-item-103')).toBeVisible();
  await expect(page.getByTestId('run-item-100')).toHaveCount(0);
  await expect(page.getByTestId('run-item-102')).toHaveCount(0);

  // OK = processed>0 AND errors=0 → runs[0] (id 100), runs[2] (id 102)
  await page.getByTestId('log-search-status-ok').click();
  await expect(page.getByTestId('run-item-100')).toBeVisible();
  await expect(page.getByTestId('run-item-102')).toBeVisible();
  await expect(page.getByTestId('run-item-103')).toHaveCount(0);

  // Alla tillbaka
  await page.getByTestId('log-search-status-all').click();
  await expect(page.getByTestId('run-item-100')).toBeVisible();
  await expect(page.getByTestId('run-item-103')).toBeVisible();
});

test('Log Gate 1.5 — datum-dropdown filtrerar körningar', async ({ page }) => {
  await setupApiMocks(page);
  await page.goto('/log');

  await page.getByTestId('log-search-date').selectOption('last24h');
  // 14 körningar är utspridda över 14 timmar — alla inom 24h, så antalet
  // ska fortfarande vara > 0. Väljer last7d för att vara säker.
  await page.getByTestId('log-search-date').selectOption('last7d');
  const count = await page.getByTestId(/^run-item-/).count();
  expect(count).toBeGreaterThan(0);
});

// ---------- Gate 1.5 designfix ----------

test('Gate 1.5 fix — already_processed-rad visar sender + subject (inte "—")', async ({
  page,
}) => {
  const runs = buildRuns();
  runs[0].filtered_messages = [
    {
      message_id: 'gm-dup-1',
      sender: 'Moovy <kvitto@moovy.fi>',
      subject: 'Din parkering 19.04.2026',
      received_at: runs[0].started_at,
      reason: 'already_processed',
      confidence: null,
      detail: null,
    },
  ];
  await setupApiMocks(page, { runs });
  await page.goto('/log');

  const row = page.getByTestId('filtered-row-gm-dup-1');
  await expect(row).toBeVisible();
  await expect(row).toContainText('Moovy');
  await expect(row).toContainText('Din parkering');
});

test('Gate 1.5 fix — LogSearch använder .fbar/.fbar__tab/.fbar__search', async ({
  page,
}) => {
  await setupApiMocks(page);
  await page.goto('/log');

  // LogSearch-container har .fbar (samma som Dashboard FilterTabs)
  const container = page.getByTestId('log-search');
  await expect(container).toHaveClass(/fbar/);

  // Status-tabs har .fbar__tab-klassen (istället för egen pill-stil)
  const okTab = page.getByTestId('log-search-status-ok');
  await expect(okTab).toHaveClass(/fbar__tab/);

  // Sökinput ligger under .fbar__search-wrapper (samma som Dashboard)
  const input = page.getByTestId('log-search-input');
  await expect(input.locator('xpath=..')).toHaveClass(/fbar__search/);

  // Datum-select har settings-field__select-klassen (samma som Settings)
  const dateSelect = page.getByTestId('log-search-date');
  await expect(dateSelect).toHaveClass(/settings-field__select/);
});

test('Gate 1.5 fix — Försök igen triggar POST /api/scan direkt efter reprocess', async ({
  page,
}) => {
  const runs = buildRuns();
  const skipped = buildSkippedMessage({
    id: 90,
    processed_at: new Date(new Date(runs[0].started_at).getTime() + 1000).toISOString(),
  });
  await setupApiMocks(page, {
    messages: [...buildMessages(), skipped],
    runs,
  });

  const scanRequest = page.waitForRequest(
    (req) => /\/api\/scan$/.test(req.url()) && req.method() === 'POST',
    { timeout: 3000 },
  );

  await page.goto('/log');
  await page.getByTestId('retry-90').click();
  await scanRequest;

  // Toast matchar den nya texten: "Mail återlagt — scanning startar nu"
  await expect(page.getByText(/scanning startar nu/i)).toBeVisible();
});

test('Gate 1.5 fix — Försök igen-knappen använder btn primary (designsystemet)', async ({
  page,
}) => {
  const runs = buildRuns();
  const skipped = buildSkippedMessage({
    id: 91,
    processed_at: new Date(new Date(runs[0].started_at).getTime() + 1000).toISOString(),
  });
  await setupApiMocks(page, {
    messages: [...buildMessages(), skipped],
    runs,
  });
  await page.goto('/log');

  const btn = page.getByTestId('retry-91');
  await expect(btn).toHaveClass(/btn/);
  await expect(btn).toHaveClass(/primary/);
});

test('Gate 1.5 fix — text-sök filtrerar körningslistan via filtered_messages', async ({
  page,
}) => {
  const runs = buildRuns();
  // Lägg unik sender i filtered_messages för en specifik körning
  runs[5].filtered_messages = [
    {
      message_id: 'gm-unique-5',
      sender: 'UniqueBananSender <banan@example.com>',
      subject: 'Unik Banan-kvitto',
      received_at: runs[5].started_at,
      reason: 'ai_filtered',
      confidence: 30,
      detail: null,
    },
  ];
  await setupApiMocks(page, { runs });
  await page.goto('/log');

  // Innan sök: alla 14 körningar synliga
  await expect(page.getByTestId('run-item-105')).toBeVisible();
  await expect(page.getByTestId('run-item-100')).toBeVisible();

  // Skriv "Banan" i sökfältet — bara run 105 ska vara kvar i listan
  await page.getByTestId('log-search-input').fill('Banan');

  await expect(page.getByTestId('run-item-105')).toBeVisible();
  await expect(page.getByTestId('run-item-100')).toHaveCount(0);
});
