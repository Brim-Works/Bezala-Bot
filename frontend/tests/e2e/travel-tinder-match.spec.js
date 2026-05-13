import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* FAS 5.16 — Travel Tinder candidate-select flow.
 *
 * Klick på en rad i "Other Receipts" får INTE öppna bekräftelsemodal.
 * Den ska markera kvittot som "ditt val" (Card B) i höger panel. Couple
 * sker bara via explicit "Couple →"-knapp i Card A (AI) eller Card B.
 *
 * Regressions-skydd från tidigare PR (#13) — i18n-skuggning som fick
 * matchSuccess.replace att krascha — kvar i Couple-toast-assertionen. */

const MISSING_ID = 12345;
const AI_MESSAGE_ID = 7;
const OTHER_MESSAGE_ID = 8;

const NOW_ISO = new Date().toISOString();

function aiMessage() {
  return {
    id: AI_MESSAGE_ID,
    message_id: 'm-7',
    sender: 'kvitto@moovy.fi',
    subject: 'Parkering',
    file_name: '20260420 Moovy Parkering.pdf',
    drive_file_id: 'drv-7',
    drive_link: 'https://drive/drv-7',
    status: 'saved',
    vendor: 'Moovy',
    amount: 12.5,
    currency: 'EUR',
    receipt_date: '2026-04-20',
    received_at: NOW_ISO,
    processed_at: NOW_ISO,
    category: 'Taxi',
    summary: 'Parkering i Helsingfors.',
    ai_description_en: 'Helsinki parking fee.',
    ai_confidence: 92,
    bezala_upload_status: 'pending',
    bezala_transaction_id: null,
    bezala_error_message: null,
    deleted_at: null,
    delete_reason: null,
    pending_link: null,
    coupled: false,
    matched_bill_line_id: null,
  };
}

function otherMessage() {
  return {
    id: OTHER_MESSAGE_ID,
    message_id: 'm-8',
    sender: 'kvitto@uber.com',
    subject: 'Uber',
    file_name: '20260419 Uber HEL.pdf',
    drive_file_id: 'drv-8',
    drive_link: 'https://drive/drv-8',
    status: 'saved',
    vendor: 'Uber',
    amount: 15.9,
    currency: 'EUR',
    receipt_date: '2026-04-19',
    received_at: NOW_ISO,
    processed_at: NOW_ISO,
    category: 'Taxi',
    summary: 'Taxi i Helsingfors.',
    ai_description_en: 'Helsinki ride.',
    ai_confidence: 80,
    bezala_upload_status: 'pending',
    bezala_transaction_id: null,
    bezala_error_message: null,
    deleted_at: null,
    delete_reason: null,
    pending_link: null,
    coupled: false,
    matched_bill_line_id: null,
  };
}

function sampleSuggestions() {
  return {
    missing_receipts: [
      {
        missing_receipt: {
          id: MISSING_ID,
          description: 'MOOVY OY, HELSINKI',
          amount: 12.5,
          currency: 'EUR',
          date: '2026-04-20',
        },
        suggestions: [
          {
            message: aiMessage(),
            score: 92,
            score_breakdown: { amount: 50, date: 30, vendor: 12 },
          },
        ],
      },
    ],
    all_messages: [aiMessage(), otherMessage()],
  };
}

async function setupTinderMocks(page, suggestions = sampleSuggestions()) {
  await setupApiMocks(page);

  await page.route('**/api/bezala/match-suggestions**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(suggestions),
    }),
  );

  await page.route('**/api/bezala/matched-pairs**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        pairs: [],
        total: 0,
        stats: { total_all_time: 0, this_week: 0, estimated_minutes_saved: 0 },
      }),
    }),
  );

  await page.route('**/api/feedback/match-result', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  );
}

test('Klick på rad i Other Receipts visar Card B — ingen modal', async ({ page }) => {
  await setupTinderMocks(page);

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-payments')).toBeVisible();
  await expect(page.getByTestId('tt-candidate-ai')).toBeVisible();

  await page.getByTestId(`tt-receipt-${OTHER_MESSAGE_ID}`).click();

  await expect(page.getByTestId('tt-candidate-user')).toBeVisible();
  await expect(page.getByTestId('tt-modal')).toHaveCount(0);
  // Raden i listan får "Valt"-badge / selected-stil
  await expect(page.getByTestId(`tt-receipt-${OTHER_MESSAGE_ID}`)).toHaveAttribute(
    'data-selected',
    'true',
  );
});

test('Klick på Couple-knapp i Card A kopplar AI-förslaget', async ({ page }) => {
  await setupTinderMocks(page);

  const consoleErrors = [];
  page.on('pageerror', (err) => consoleErrors.push(err.message));
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    // Filtrera bort browser-resource-noise (CDN-cert etc) — vi vill bara
    // fånga riktiga JS-fel som regressions-signal.
    if (text.includes('Failed to load resource')) return;
    consoleErrors.push(text);
  });

  let captured = null;
  await page.route(
    `**/api/messages/${AI_MESSAGE_ID}/match-to-bezala`,
    (route) => {
      captured = route.request().postDataJSON();
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: AI_MESSAGE_ID,
          bezala_upload_status: 'success',
          bezala_transaction_id: String(MISSING_ID),
        }),
      });
    },
  );

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-candidate-ai')).toBeVisible();

  const reqPromise = page.waitForRequest((req) =>
    req.url().includes(`/api/messages/${AI_MESSAGE_ID}/match-to-bezala`) &&
    req.method() === 'POST',
  );

  // Couple-knappen finns både i Card A (AI) — klicka den
  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-couple')
    .click();
  await reqPromise;

  await expect(page.getByText(/Matchat:\s*Moovy/i)).toBeVisible();
  expect(captured).toEqual({ missing_receipt_id: MISSING_ID });
  expect(consoleErrors).toEqual([]);
});

test('Couple via Card B kopplar användarens val', async ({ page }) => {
  await setupTinderMocks(page);

  let captured = null;
  await page.route(
    `**/api/messages/${OTHER_MESSAGE_ID}/match-to-bezala`,
    (route) => {
      captured = route.request().postDataJSON();
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: OTHER_MESSAGE_ID,
          bezala_upload_status: 'success',
          bezala_transaction_id: String(MISSING_ID),
        }),
      });
    },
  );

  await page.goto('/travel-tinder');
  await page.getByTestId(`tt-receipt-${OTHER_MESSAGE_ID}`).click();
  await expect(page.getByTestId('tt-candidate-user')).toBeVisible();

  const reqPromise = page.waitForRequest((req) =>
    req.url().includes(`/api/messages/${OTHER_MESSAGE_ID}/match-to-bezala`) &&
    req.method() === 'POST',
  );
  await page
    .getByTestId('tt-candidate-user')
    .getByTestId('tt-candidate-couple')
    .click();
  await reqPromise;

  await expect(page.getByText(/Matchat:\s*Uber/i)).toBeVisible();
  expect(captured).toEqual({ missing_receipt_id: MISSING_ID });
});

test('× på Card B rensar valet', async ({ page }) => {
  await setupTinderMocks(page);

  await page.goto('/travel-tinder');
  await page.getByTestId(`tt-receipt-${OTHER_MESSAGE_ID}`).click();
  await expect(page.getByTestId('tt-candidate-user')).toBeVisible();

  await page.getByTestId('tt-candidate-clear').click();
  await expect(page.getByTestId('tt-candidate-user')).toHaveCount(0);
});

test('Klick på samma rad igen avmarkerar', async ({ page }) => {
  await setupTinderMocks(page);

  await page.goto('/travel-tinder');
  await page.getByTestId(`tt-receipt-${OTHER_MESSAGE_ID}`).click();
  await expect(page.getByTestId('tt-candidate-user')).toBeVisible();

  await page.getByTestId(`tt-receipt-${OTHER_MESSAGE_ID}`).click();
  await expect(page.getByTestId('tt-candidate-user')).toHaveCount(0);
});

test('Open in Drawer öppnar Drawer', async ({ page }) => {
  await setupTinderMocks(page);

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-candidate-ai')).toBeVisible();

  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-open-drawer')
    .click();

  await expect(page.getByTestId('drawer')).toBeVisible({ timeout: 5000 });
});
