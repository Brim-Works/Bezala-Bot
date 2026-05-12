import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* Match Health-vyn: nav-länk, tabell, filter, expand, markdown-copy. */

function buildRow({
  id, merchant, vendor, amount, currency = 'EUR', date,
  verdict, bestScore = null, bestVendor = null, gmailCategory = 'no_hits',
  wouldMatchHidden = 0,
  processedReceipts = null, gmailMessages = null,
}) {
  const top3 = bestScore != null ? [{
    score: bestScore,
    score_breakdown: {
      amount: 50, date: 25, vendor: 25,
      amount_diff: 0, amount_diff_pct: 0,
      date_diff_days: 0, date_matched_field: 'receipt_date',
      vendor_match_method: 'substring', vendor_similarity_pct: 100,
    },
    message_id: `msg-${id}`,
    id: id * 10,
    vendor: bestVendor || vendor,
    file_name: `${date} ${bestVendor || vendor}.pdf`,
    amount,
    currency,
    receipt_date: date,
    received_at: null,
  }] : [];

  const procDefault = bestScore != null ? [{
    id: id * 10,
    message_id: `msg-${id}`,
    vendor: bestVendor || vendor,
    file_name: `${date} ${bestVendor || vendor}.pdf`,
    amount,
    currency,
    receipt_date: date,
    drive_link: `https://drive/${id}`,
    ai_confidence: 92,
    ai_summary: 'Mock summary',
    match_score_total: bestScore,
    match_score_breakdown: {
      amount: 50, date: 25, vendor: 25,
      amount_diff: 0, amount_diff_pct: 0,
      date_diff_days: 0, vendor_match_method: 'substring',
      vendor_similarity_pct: 100,
    },
    above_threshold: bestScore >= 80,
    why_not_best: null,
  }] : [];

  const processed = processedReceipts ?? procDefault;
  const messages = gmailMessages ?? [];

  return {
    bill_line: {
      id, merchant, vendor_normalized: vendor, amount, currency, date,
    },
    best_match: top3[0] || null,
    top_3_suggestions: top3,
    fuzzy_candidates: {
      by_amount_window_10pct: 0,
      by_date_window_7d: 0,
      by_vendor_fuzzy: 0,
    },
    gmail_status: {
      category: gmailCategory,
      details: 'mocked',
      search_query_used: `from:${(vendor || '').toLowerCase()} after:${date} before:${date}`,
      would_match_without_attachment_filter: wouldMatchHidden,
      hits_with_attachment: gmailCategory === 'filtered' ? 0 : 1,
      hits_without_attachment: wouldMatchHidden || 1,
    },
    verdict: {
      category: verdict,
      confidence: 'high',
      suggested_action: `Action for ${verdict}`,
    },
    // Match Health 2.0 — nya fält
    processed_receipts: processed,
    gmail_messages: messages,
    diagnostic_summary: {
      gmail_status: gmailCategory,
      gmail_count: messages.length,
      processed_count: messages.filter((m) => m.is_processed).length,
      candidate_count: processed.length,
      above_threshold_count: processed.filter((p) => p.above_threshold).length,
      best_score: bestScore || 0,
      threshold: 80,
      next_action: `Action for ${verdict}`,
    },
  };
}

const SAMPLE_ROWS = [
  buildRow({
    id: 1, merchant: 'MIKKO: ANTHROPIC 112.95 EUR',
    vendor: 'Anthropic', amount: 112.95, date: '2026-04-14',
    verdict: 'matched_correctly', bestScore: 105,
    bestVendor: 'Anthropic', gmailCategory: 'found',
  }),
  buildRow({
    id: 2, merchant: 'MIKKO: SKANETRAFIKEN 50 SEK',
    vendor: 'Skanetrafiken', amount: 50, currency: 'SEK',
    date: '2026-04-20', verdict: 'gmail_miss', bestScore: null,
    gmailCategory: 'filtered', wouldMatchHidden: 3,
  }),
  buildRow({
    id: 3, merchant: 'MIKKO: LOVABLE 100 EUR',
    vendor: 'Lovable', amount: 100, date: '2026-05-05',
    verdict: 'no_receipt_exists', bestScore: null,
    gmailCategory: 'no_hits',
  }),
  buildRow({
    id: 4, merchant: 'MIKKO: VENDORX 300 EUR',
    vendor: 'Vendorx', amount: 300, date: '2026-04-01',
    verdict: 'match_algorithm_failed', bestScore: 65,
    bestVendor: 'OtherVendor', gmailCategory: 'found',
  }),
];

const SAMPLE_STATS = {
  total: 4,
  matched_correctly: 1,
  gmail_miss: 1,
  no_receipt_exists: 1,
  match_algorithm_failed: 1,
  ai_extraction_wrong: 0,
  gmail_error: 0,
};

async function setupMatchHealthMocks(page, {
  rows = SAMPLE_ROWS, stats = SAMPLE_STATS,
} = {}) {
  await setupApiMocks(page);
  await page.route('**/api/debug/match-health*', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        generated_at: new Date().toISOString(),
        cache_age_seconds: 0,
        rows,
        stats,
      }),
    });
  });
}

test('Match Health — nav-länk syns och navigerar', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/');
  const nav = page.getByTestId('nav-matchHealth');
  await expect(nav).toBeVisible();
  await nav.click();
  await expect(page).toHaveURL(/\/match-health$/);
  await expect(page.getByTestId('match-health')).toBeVisible();
});

test('Match Health — tabell renderas med alla rader', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await expect(page.getByTestId('mh-table')).toBeVisible();
  await expect(page.getByTestId('mh-row-1')).toBeVisible();
  await expect(page.getByTestId('mh-row-2')).toBeVisible();
  await expect(page.getByTestId('mh-row-3')).toBeVisible();
  await expect(page.getByTestId('mh-row-4')).toBeVisible();
});

test('Match Health — färgkodning per verdict syns på rader', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await expect(page.getByTestId('mh-row-1')).toHaveAttribute(
    'data-verdict', 'matched_correctly',
  );
  await expect(page.getByTestId('mh-row-2')).toHaveAttribute(
    'data-verdict', 'gmail_miss',
  );
  await expect(page.getByTestId('mh-row-3')).toHaveAttribute(
    'data-verdict', 'no_receipt_exists',
  );
  await expect(page.getByTestId('mh-row-4')).toHaveAttribute(
    'data-verdict', 'match_algorithm_failed',
  );
});

test('Match Health — filter på "Algoritm fel" döljer andra rader',
  async ({ page }) => {
    await setupMatchHealthMocks(page);
    await page.goto('/match-health');
    // Period: alla (för att INTE filtrera bort på datum-fönster)
    await page.getByTestId('mh-filter-period').selectOption('all');
    await page.getByTestId('mh-filter-verdict')
      .selectOption('match_algorithm_failed');
    await expect(page.getByTestId('mh-row-4')).toBeVisible();
    await expect(page.getByTestId('mh-row-1')).toHaveCount(0);
    await expect(page.getByTestId('mh-row-2')).toHaveCount(0);
    await expect(page.getByTestId('mh-row-3')).toHaveCount(0);
  });

test('Match Health — klick på rad expanderar detaljer', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await page.getByTestId('mh-filter-period').selectOption('all');
  await page.getByTestId('mh-row-2').click();
  await expect(page.getByTestId('mh-expanded-2')).toBeVisible();
});

test('Match Health — refresh-knapp triggar omladdning', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  const reqPromise = page.waitForRequest((req) =>
    req.url().includes('/api/debug/match-health?refresh=true'),
  );
  await page.getByTestId('mh-refresh').click();
  await reqPromise;
});

test('Match Health — empty state visas när inga rader finns',
  async ({ page }) => {
    await setupMatchHealthMocks(page, {
      rows: [], stats: { total: 0, matched_correctly: 0, gmail_miss: 0 },
    });
    await page.goto('/match-health');
    await expect(page.getByTestId('mh-empty')).toBeVisible();
  });

test('Match Health — copy-all skriver markdown till clipboard',
  async ({ page, context }) => {
    await setupMatchHealthMocks(page);
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/match-health');
    await page.getByTestId('mh-filter-period').selectOption('all');
    await page.getByTestId('mh-copy-all').click();
    // Toast bekräftar kopiering — letar efter toastens text:
    await expect(page.getByText(/Kopierat|Copied/)).toBeVisible();
    // Verifiera clipboard har markdown
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain('# Match Health Report');
    expect(clip).toContain('Översikt');
  });

test('Match Health — copy-row på expanderad rad skriver enstaka rad',
  async ({ page, context }) => {
    await setupMatchHealthMocks(page);
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await page.goto('/match-health');
    await page.getByTestId('mh-filter-period').selectOption('all');
    await page.getByTestId('mh-row-2').click();
    await page.getByTestId('mh-copy-row-2').click();
    await expect(page.getByText(/Kopierat|Copied/)).toBeVisible();
    const clip = await page.evaluate(() => navigator.clipboard.readText());
    expect(clip).toContain('Match Health — enstaka rad');
    // Bara rad 2:s data ska finnas, INTE rad 1 eller rad 4:
    expect(clip).toContain('Skanetrafiken');
    expect(clip).not.toContain('Anthropic');
    expect(clip).not.toContain('Vendorx');
  });

test('Match Health — error-card visas vid backend-fel', async ({ page }) => {
  await setupApiMocks(page);
  await page.route('**/api/debug/match-health*', (route) =>
    route.fulfill({
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Bezala missing_receipts: timeout' }),
    }),
  );
  await page.goto('/match-health');
  await expect(page.getByTestId('mh-error')).toBeVisible();
  await expect(page.getByTestId('mh-retry')).toBeVisible();
});

/* ----- Match Health 2.0 — expand modes + score bars + flow ----- */

test('Match Health 2.0 — expanderad rad default till Sammanfattning + flow', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await page.getByTestId('mh-filter-period').selectOption('all');
  await page.getByTestId('mh-row-1').click();
  // Default-läge = summary, flow ska synas
  await expect(page.getByTestId('mh-flow-1')).toBeVisible();
  // Mode-toggle ska visas
  await expect(page.getByTestId('mh-mode-summary-1')).toHaveAttribute(
    'aria-selected', 'true',
  );
});

test('Match Health 2.0 — toggle till Detaljerad vy renderar score-bars', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await page.getByTestId('mh-filter-period').selectOption('all');
  await page.getByTestId('mh-row-1').click();
  await page.getByTestId('mh-mode-details-1').click();
  await expect(page.getByTestId('mh-mode-details-1')).toHaveAttribute(
    'aria-selected', 'true',
  );
  // Score bars renderas — Belopp/Datum/Vendor/Total
  await expect(page.locator('.mh-bar-fill').first()).toBeVisible();
});

test('Match Health 2.0 — collapsed-rad visar counts ikoner', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await page.getByTestId('mh-filter-period').selectOption('all');
  // mh-counts ska finnas i raden
  await expect(page.getByTestId('mh-counts-1')).toBeVisible();
});

test('Match Health 2.0 — processed_receipts renderas i Detaljerad vy', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await page.getByTestId('mh-filter-period').selectOption('all');
  await page.getByTestId('mh-row-1').click();
  await page.getByTestId('mh-mode-details-1').click();
  // processed-listan ska finnas
  await expect(page.getByTestId('mh-processed-1')).toBeVisible();
});

test('Match Health 2.0 — Drive-länk har target=_blank', async ({ page }) => {
  await setupMatchHealthMocks(page);
  await page.goto('/match-health');
  await page.getByTestId('mh-filter-period').selectOption('all');
  await page.getByTestId('mh-row-1').click();
  await page.getByTestId('mh-mode-details-1').click();
  const link = page.getByTestId('mh-drive-10');
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute('target', '_blank');
});

test('Match Health 2.0 — markdown-export inkluderar processed_receipts', async ({ page, context }) => {
  await setupMatchHealthMocks(page);
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/match-health');
  await page.getByTestId('mh-filter-period').selectOption('all');
  await page.getByTestId('mh-copy-all').click();
  await expect(page.getByText(/Kopierat|Copied/)).toBeVisible();
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  // Markdown ska innehålla 2.0-sektioner
  expect(clip).toContain('Diagnos:');
  expect(clip).toContain('Kandidater:');
});
