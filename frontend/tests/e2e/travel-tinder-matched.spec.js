import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* Travel Tinder Matchade-vyn — toggle, filter, klick→drawer, frikoppla. */

const SAMPLE_PAIRS = [
  {
    message_id: 'm-finnair',
    id: 101,
    receipt: {
      vendor: 'Finnair',
      file_name: '20260430 Finnair HEL-ARN.pdf',
      amount: 503.0,
      currency: 'EUR',
      receipt_date: '2026-04-30',
      drive_file_id: 'drv-101',
      drive_link: 'https://drive/drv-101',
      subject: 'Eticket',
      sender: 'noreply@finnair.com',
    },
    payment: {
      id: 'bz-101',
      merchant: 'MIKKO: FINNAIR HEL-ARN, VANTAA, FI',
      amount: 503.0,
      currency: 'EUR',
      date: '2026-04-30',
    },
    bezala_transaction_id: 'bz-101',
    matched_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),
  },
  {
    message_id: 'm-moovy',
    id: 102,
    receipt: {
      vendor: 'Moovy',
      file_name: '20260420 Moovy Parkering.pdf',
      amount: 12.5,
      currency: 'EUR',
      receipt_date: '2026-04-20',
      drive_file_id: 'drv-102',
      drive_link: 'https://drive/drv-102',
      subject: 'Parking',
      sender: 'kvitto@moovy.fi',
    },
    payment: {
      id: 'bz-102',
      merchant: 'MOOVY OY, HELSINKI',
      amount: 12.5,
      currency: 'EUR',
      date: '2026-04-20',
    },
    bezala_transaction_id: 'bz-102',
    matched_at: new Date(Date.now() - 24 * 3600 * 1000).toISOString(),
  },
];

const STATS_DEFAULT = {
  total_all_time: 12,
  this_week: 3,
  estimated_minutes_saved: 120,
};

function makePairsState(pairs) {
  return JSON.parse(JSON.stringify(pairs));
}

async function setupMatchedMocks(page, { pairs = SAMPLE_PAIRS, stats = STATS_DEFAULT } = {}) {
  await setupApiMocks(page);

  // /api/bezala/match-suggestions(?include_all_messages=true) — för
  // unmatched-läget. Returnerar tom missing_receipts så TinderCard
  // göms tyst när vi sitter i "att matcha"-läget.
  await page.route('**/api/bezala/match-suggestions**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        missing_receipts: [],
        all_messages: [],
      }),
    }),
  );

  let liveState = makePairsState(pairs);

  await page.route('**/api/bezala/matched-pairs**', (route) => {
    const url = new URL(route.request().url());
    const search = (url.searchParams.get('search') || '').toLowerCase();
    const filtered = search
      ? liveState.filter((p) =>
          (p.receipt?.vendor || '').toLowerCase().includes(search) ||
          (p.payment?.merchant || '').toLowerCase().includes(search),
        )
      : liveState;
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pairs: filtered,
        total: filtered.length,
        stats,
      }),
    });
  });

  await page.route('**/api/bezala/unmatch/**', (route) => {
    const messageId = decodeURIComponent(
      route.request().url().split('/api/bezala/unmatch/')[1].split('?')[0],
    );
    liveState = liveState.filter((p) => p.message_id !== messageId);
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        success: true,
        old_bezala_transaction_id: 'bz',
      }),
    });
  });
}

async function gotoTravelTinder(page) {
  // Playwright kör varje test i en fräsch context → localStorage är
  // tomt så vi behöver inte rensa explicit.
  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-payments')).toBeVisible();
}

test('TT Matchade — toggle byter läge och visar matchade par', async ({ page }) => {
  await setupMatchedMocks(page);
  await gotoTravelTinder(page);

  // Default-läge: "Att matcha"
  await expect(page.getByTestId('tt-mode-unmatched')).toHaveAttribute(
    'aria-selected', 'true',
  );

  await page.getByTestId('tt-mode-matched').click();
  await expect(page.getByTestId('tt-mode-matched')).toHaveAttribute(
    'aria-selected', 'true',
  );
  await expect(page.getByTestId('tt-matched')).toBeVisible();
  await expect(page.getByTestId('tt-matched-row-m-finnair')).toBeVisible();
  await expect(page.getByTestId('tt-matched-row-m-moovy')).toBeVisible();
});

test('TT Matchade — stats-banner visar siffror', async ({ page }) => {
  await setupMatchedMocks(page);
  await gotoTravelTinder(page);
  await page.getByTestId('tt-mode-matched').click();

  const stats = page.getByTestId('tt-matched-stats');
  await expect(stats).toBeVisible();
  await expect(stats).toContainText('12');
  await expect(stats).toContainText('3');
  // 120 min → 2.0 timmar
  await expect(stats).toContainText('2.0');
});

test('TT Matchade — payment-snapshot visas i raden', async ({ page }) => {
  await setupMatchedMocks(page);
  await gotoTravelTinder(page);
  await page.getByTestId('tt-mode-matched').click();

  const paymentLine = page.getByTestId('tt-matched-payment-m-finnair');
  await expect(paymentLine).toBeVisible();
  await expect(paymentLine).toContainText('FINNAIR');
});

test('TT Matchade — sökning filtrerar listan', async ({ page }) => {
  await setupMatchedMocks(page);
  await gotoTravelTinder(page);
  await page.getByTestId('tt-mode-matched').click();

  await page.getByTestId('tt-matched-search').fill('moovy');
  // Vänta tills nätverket settle:as och listan uppdateras
  await expect(page.getByTestId('tt-matched-row-m-moovy')).toBeVisible();
  await expect(page.getByTestId('tt-matched-row-m-finnair')).toHaveCount(0);
});

test('TT Matchade — period-dropdown skickar period i URL:en', async ({ page }) => {
  await setupMatchedMocks(page);
  await gotoTravelTinder(page);
  await page.getByTestId('tt-mode-matched').click();

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/bezala/matched-pairs') &&
      req.url().includes('period=7d'),
  );
  await page.getByTestId('tt-matched-period').selectOption('7d');
  await reqPromise;
});

test('TT Matchade — frikoppla via modal tar bort raden', async ({ page }) => {
  await setupMatchedMocks(page);
  await gotoTravelTinder(page);
  await page.getByTestId('tt-mode-matched').click();
  await expect(page.getByTestId('tt-matched-row-m-finnair')).toBeVisible();

  await page.getByTestId('tt-matched-unmatch-m-finnair').click();
  await expect(page.getByTestId('tt-matched-confirm')).toBeVisible();

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/bezala/unmatch/m-finnair') &&
      req.method() === 'POST',
  );
  await page.getByTestId('tt-matched-confirm-btn').click();
  await reqPromise;

  await expect(page.getByTestId('tt-matched-row-m-finnair')).toHaveCount(0);
});

test('TT Matchade — tom-state när inga par', async ({ page }) => {
  await setupMatchedMocks(page, { pairs: [], stats: {
    total_all_time: 0, this_week: 0, estimated_minutes_saved: 0,
  } });
  await gotoTravelTinder(page);
  await page.getByTestId('tt-mode-matched').click();
  await expect(page.getByTestId('tt-matched-empty')).toBeVisible();
});

test('TT Matchade — mode-state persistas i localStorage', async ({ page }) => {
  await setupMatchedMocks(page);
  await gotoTravelTinder(page);
  await page.getByTestId('tt-mode-matched').click();
  await expect(page.getByTestId('tt-matched')).toBeVisible();

  // Reload — läget ska kvarstå
  await page.reload();
  await expect(page.getByTestId('tt-matched')).toBeVisible();
  await expect(page.getByTestId('tt-mode-matched')).toHaveAttribute(
    'aria-selected', 'true',
  );
});
