import { expect, test } from '@playwright/test';
import { buildMessages, setupApiMocks } from './fixtures.js';

/* FAS 5.4 — Kortmatchning. */

const SAMPLE_SUGGESTIONS = [
  {
    missing_receipt: {
      id: 12345,
      description: 'CLAUDE.AI SUBSCRIPTION',
      amount: 112.95,
      currency: 'EUR',
      date: '2026-04-14',
    },
    suggestions: [
      {
        message: {
          id: 7,
          message_id: 'm-7',
          sender: 'invoice@anthropic.com',
          subject: 'Anthropic API',
          file_name: '20260414 Anthropic API.pdf',
          drive_file_id: 'drv-7',
          drive_link: 'https://drive/drv-7',
          status: 'saved',
          vendor: 'Anthropic',
          amount: 112.95,
          currency: 'EUR',
          receipt_date: '2026-04-14',
          ai_confidence: 95,
          bezala_upload_status: 'pending',
          bezala_transaction_id: null,
          bezala_error_message: null,
          deleted_at: null,
          delete_reason: null,
          pending_link: null,
        },
        score: 95,
        score_breakdown: { amount: 50, date: 30, vendor: 15 },
      },
    ],
  },
  {
    missing_receipt: {
      id: 12346,
      description: 'AIRPORT LRS',
      amount: 69.0,
      currency: 'USD',
      date: '2026-04-14',
    },
    suggestions: [],
  },
];

async function setupMatchMocks(page, suggestions = SAMPLE_SUGGESTIONS) {
  await setupApiMocks(page);
  await page.route('**/api/bezala/match-suggestions', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(suggestions),
    }),
  );
}

test('Match-vy — sidebar-länk navigerar till /match', async ({ page }) => {
  await setupMatchMocks(page);
  await page.goto('/');
  await page.getByTestId('nav-match').click();
  await expect(page).toHaveURL(/\/match$/);
  await expect(page.getByTestId('match-grid')).toBeVisible();
});

test('Match-vy — visar lista av saknade kvitton + suggestions', async ({
  page,
}) => {
  await setupMatchMocks(page);
  await page.goto('/match');

  // Lista i vänster kolumn
  await expect(page.getByTestId('missing-item-12345')).toBeVisible();
  await expect(page.getByTestId('missing-item-12346')).toBeVisible();

  // Första valt automatiskt → suggestion-listan visar Anthropic-förslaget
  await expect(page.getByTestId('suggestion-7')).toBeVisible();
  // Score-badgen visar 95
  await expect(page.getByTestId('score-95')).toBeVisible();
});

test('Match-vy — klick på kvitto utan förslag visar tomt-meddelande', async ({
  page,
}) => {
  await setupMatchMocks(page);
  await page.goto('/match');
  await page.getByTestId('missing-item-12346').click();
  await expect(page.getByTestId('no-suggestions')).toBeVisible();
});

test('Match-vy — Koppla ihop triggar POST + success-toast', async ({
  page,
}) => {
  await setupMatchMocks(page);
  let captured = null;
  await page.route(
    '**/api/messages/7/match-to-bezala',
    async (route) => {
      const req = route.request();
      captured = req.postDataJSON();
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: 7,
          bezala_upload_status: 'success',
          bezala_transaction_id: '12345',
        }),
      });
    },
  );

  await page.goto('/match');
  await page.getByTestId('match-btn-7').click();

  await expect(page.getByText(/kopplat till Bezala/i)).toBeVisible();
  expect(captured).toEqual({ missing_receipt_id: 12345 });
});

test('Match-vy — tom-state när inga saknade kvitton', async ({ page }) => {
  await setupMatchMocks(page, []);
  await page.goto('/match');
  await expect(page.getByTestId('match-empty')).toBeVisible();
});
