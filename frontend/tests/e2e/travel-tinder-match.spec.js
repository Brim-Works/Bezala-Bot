import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* Travel Tinder — match-action regressionstest.
 *
 * Bugg som motiverade testet: en PR #13 introducerade en namnkonflikt i
 * i18n (`travelTinder.matched` fanns både som sträng OCH som objekt; objektet
 * skuggade strängen). Klick på Couple ledde till
 *   "TypeError: e.travelTinder.matched.replace is not a function"
 * och matchningen kördes aldrig. Testet säkerställer att Match → Couple
 * triggar POST + visar success-toast utan TypeError. */

const MISSING_ID = 12345;
const MESSAGE_ID = 7;

const SAMPLE_MATCH_SUGGESTIONS = {
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
          message: {
            id: MESSAGE_ID,
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
            ai_confidence: 92,
            bezala_upload_status: 'pending',
            bezala_transaction_id: null,
            bezala_error_message: null,
            deleted_at: null,
            delete_reason: null,
            pending_link: null,
            coupled: false,
            matched_bill_line_id: null,
          },
          score: 92,
          score_breakdown: { amount: 50, date: 30, vendor: 12 },
        },
      ],
    },
  ],
  all_messages: [
    {
      id: MESSAGE_ID,
      message_id: 'm-7',
      vendor: 'Moovy',
      file_name: '20260420 Moovy Parkering.pdf',
      amount: 12.5,
      currency: 'EUR',
      receipt_date: '2026-04-20',
      coupled: false,
      matched_bill_line_id: null,
    },
  ],
};

async function setupMatchActionMocks(page) {
  await setupApiMocks(page);

  await page.route('**/api/bezala/match-suggestions**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(SAMPLE_MATCH_SUGGESTIONS),
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

test('TT match — Couple skickar POST och visar success-toast utan TypeError', async ({ page }) => {
  await setupMatchActionMocks(page);

  const consoleErrors = [];
  page.on('pageerror', (err) => consoleErrors.push(err.message));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  let capturedMatchBody = null;
  await page.route(
    `**/api/messages/${MESSAGE_ID}/match-to-bezala`,
    (route) => {
      capturedMatchBody = route.request().postDataJSON();
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: MESSAGE_ID,
          bezala_upload_status: 'success',
          bezala_transaction_id: String(MISSING_ID),
        }),
      });
    },
  );

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-payments')).toBeVisible();

  // Vänta tills TinderCard:et med vår message renderats — bekräftar att
  // missing_receipt har valts och AI-förslaget visas.
  await expect(page.getByTestId(`tt-card-${MESSAGE_ID}`)).toBeVisible();

  await page.getByTestId('tt-card-match').click();

  // Bekräftelsemodalen
  await expect(page.getByTestId('tt-modal')).toBeVisible();

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes(`/api/messages/${MESSAGE_ID}/match-to-bezala`) &&
      req.method() === 'POST',
  );
  await page.getByTestId('tt-modal-confirm').click();
  await reqPromise;

  // Success-toast: regressionsbeviset. Texten kommer från
  // travelTinder.matchSuccess = '✓ Matchat: {vendor}'. Vendor är "Moovy"
  // från SAMPLE_MATCH_SUGGESTIONS. Om i18n-nyckeln skuggas av objektet
  // igen, kraschar .replace() innan toasten visas och denna assertion
  // fallerar.
  await expect(page.getByText(/Matchat:\s*Moovy/i)).toBeVisible();

  expect(capturedMatchBody).toEqual({ missing_receipt_id: MISSING_ID });

  // Hårdgaranti mot framtida i18n-skuggning: noll page-errors under flödet.
  expect(consoleErrors).toEqual([]);
});
