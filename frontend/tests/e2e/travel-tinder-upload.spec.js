import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* C33 / FAS 7a — manuell upload-card i Travel Tinder.
 *
 * Verifierar att upload-kortet renderas med "Företagskort" som default
 * och att "Privat kort" är disabled (FAS 7b-stub). Att en fil kan väljas
 * och submit-knappen aktiveras. Mockar /api/messages/upload-anropet och
 * verifierar att payment_method='company_card' och bill_line_id skickas
 * när en korttransaktion är vald. */

const MISSING_ID = 42001;
const NOW_ISO = new Date().toISOString();

function sampleSuggestions() {
  return {
    missing_receipts: [
      {
        missing_receipt: {
          id: MISSING_ID,
          description: 'PORVOON AUTOPESU, PORVOO, FI 83.11 EUR',
          amount: 83.11,
          currency: 'EUR',
          date: '2026-05-12',
        },
        suggestions: [],
      },
    ],
    all_messages: [],
  };
}

async function setupTinderMocks(page) {
  await setupApiMocks(page);

  await page.route('**/api/bezala/match-suggestions**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(sampleSuggestions()),
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
}

test('upload-card renderas med företagskort som default + private stub disabled', async ({ page }) => {
  await setupTinderMocks(page);

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-payments')).toBeVisible();

  // Korttransaktionen är auto-vald → upload-kortet visas under "Andra kvitton"
  const card = page.getByTestId('tt-upload-card');
  await expect(card).toBeVisible();

  // Företagskort är aktivt
  const company = page.getByTestId('tt-upload-method-company');
  await expect(company).toHaveAttribute('aria-checked', 'true');

  // Privat kort är disabled (FAS 7b-stub)
  const priv = page.getByTestId('tt-upload-method-private');
  await expect(priv).toBeDisabled();

  // Submit är disabled tills fil väljs
  await expect(page.getByTestId('tt-upload-submit')).toBeDisabled();
});

test('upload skickar payment_method=company_card och bill_line_id', async ({ page }) => {
  await setupTinderMocks(page);

  let capturedForm = null;
  await page.route('**/api/messages/upload', async (route) => {
    // Plocka multipart-fälten manuellt — Playwright postData() ger råa bytes.
    const buffer = route.request().postDataBuffer();
    capturedForm = buffer ? buffer.toString('utf-8') : '';
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        message: {
          id: 999,
          message_id: 'manual:abc',
          vendor: 'Porvoon Autopesu',
          file_name: 'biltvatt.pdf',
          upload_source: 'manual_upload',
          payment_method: 'company_card',
          bezala_upload_status: 'success',
        },
        coupling: {
          ok: true,
          bill_line_id: String(MISSING_ID),
          transaction_id: 'tx-101',
        },
      }),
    });
  });

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-upload-card')).toBeVisible();

  // Sätt en fil på input
  const input = page.getByTestId('tt-upload-file-input');
  await input.setInputFiles({
    name: 'biltvatt.pdf',
    mimeType: 'application/pdf',
    buffer: Buffer.from('%PDF-1.4\n%fake test pdf body'),
  });

  await expect(page.getByTestId('tt-upload-file-row')).toBeVisible();
  await expect(page.getByTestId('tt-upload-submit')).toBeEnabled();

  await page.getByTestId('tt-upload-submit').click();

  await expect.poll(() => capturedForm).not.toBeNull();
  expect(capturedForm).toContain('name="payment_method"');
  expect(capturedForm).toContain('company_card');
  expect(capturedForm).toContain('name="bill_line_id"');
  expect(capturedForm).toContain(String(MISSING_ID));
  expect(capturedForm).toContain('biltvatt.pdf');
});

// C43 — mobil: kameraknappen laddar upp direkt utan extra klick.
test('C43 — kameraknapp laddar upp och kopplar direkt', async ({ page }) => {
  await setupTinderMocks(page);
  let capturedForm = null;
  await page.route('**/api/messages/upload', async (route) => {
    capturedForm = route.request().postDataBuffer()?.toString('utf-8') || '';
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        message: { id: 1000, vendor: 'Porvoon Autopesu', file_name: 'foto.jpg' },
        coupling: { ok: true, bill_line_id: String(MISSING_ID) },
      }),
    });
  });

  await page.goto('/travel-tinder');
  const camera = page.getByTestId('tt-upload-camera-input');
  await expect(camera).toHaveAttribute('capture', 'environment');
  await expect(page.getByTestId('tt-upload-camera')).toBeVisible();
  await camera.setInputFiles({
    name: 'foto.jpg',
    mimeType: 'image/jpeg',
    buffer: Buffer.from([0xff, 0xd8, 0xff, 0xe0]),
  });
  await expect.poll(() => capturedForm).not.toBeNull();
  expect(capturedForm).toContain('name="bill_line_id"');
  expect(capturedForm).toContain(String(MISSING_ID));
  expect(capturedForm).toContain('foto.jpg');
});

// C43 — privat utlägg markeras via knappen (inget skickas för attest).
test('C43 — markera kortrad som privat utlägg', async ({ page }) => {
  await setupTinderMocks(page);
  let body = null;
  await page.route(`**/api/bezala/bill-lines/${MISSING_ID}/private-expense`, async (route) => {
    body = route.request().postDataJSON();
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ bill_line_id: String(MISSING_ID), status: 'private_expense' }),
    });
  });

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-private-expense')).toBeVisible();
  await page.getByTestId('tt-private-mark').click();
  await expect.poll(() => body).not.toBeNull();
  expect(body).toEqual({ note: null, create_draft: false });
});
