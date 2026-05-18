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

/* C14 — Travel Tinder Couple visual feedback + immediate row removal.
 *
 * Verifierar att klick på Couple ger omedelbar visuell respons:
 *  - payment-raden i "Att matcha"-listan deemfasas + visar "kopplar…"
 *  - vid lyckat svar försvinner raden direkt (innan refresh) och en
 *    success-toast visas
 *  - vid 409 visas "Redan kopplad — uppdaterar" och raden tas bort
 *  - vid generellt fel återställs UI:t så användaren kan försöka igen */

function suggestionsWithCoupledFlag(coupled) {
  const s = sampleSuggestions();
  return {
    missing_receipts: coupled ? [] : s.missing_receipts,
    all_messages: s.all_messages,
  };
}

test('C14 — payment-rad deemfasas med "kopplar…"-indikator under POST', async ({ page }) => {
  await setupTinderMocks(page);

  // Försena POST så vi hinner observera in-flight-state.
  await page.route(
    `**/api/messages/${AI_MESSAGE_ID}/match-to-bezala`,
    async (route) => {
      await new Promise((r) => setTimeout(r, 600));
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

  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-couple')
    .click();

  // Deemfas-indikatorn på raden ska visas direkt medan POST är pending.
  await expect(
    page.getByTestId(`tt-payment-matching-${MISSING_ID}`),
  ).toBeVisible();
  await expect(page.getByTestId(`tt-payment-${MISSING_ID}`)).toHaveAttribute(
    'data-matching',
    'true',
  );

  // Couple-spinner inuti Card A
  await expect(
    page.getByTestId('tt-candidate-couple-spinner').first(),
  ).toBeVisible();

  // Efter POST: success-toast
  await expect(page.getByText(/Matchat:\s*Moovy/i)).toBeVisible();
});

test('C14 — payment-raden tas bort omedelbart vid lyckad match', async ({ page }) => {
  await setupApiMocks(page);

  // Stateful mock — efter en lyckad POST tar backend bort bill_line
  // ur missing_receipts (det är så servern beter sig i prod).
  let coupled = false;
  await page.route('**/api/bezala/match-suggestions**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(suggestionsWithCoupledFlag(coupled)),
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

  await page.route(
    `**/api/messages/${AI_MESSAGE_ID}/match-to-bezala`,
    (route) => {
      coupled = true;
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
  await expect(page.getByTestId(`tt-payment-${MISSING_ID}`)).toBeVisible();

  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-couple')
    .click();

  await expect(page.getByText(/Matchat:\s*Moovy/i)).toBeVisible();
  await expect(page.getByTestId(`tt-payment-${MISSING_ID}`)).toHaveCount(0);
});

test('C14 — 409 Conflict visar "Redan kopplad" och tar bort raden', async ({ page }) => {
  await setupApiMocks(page);

  let alreadyCoupled = false;
  await page.route('**/api/bezala/match-suggestions**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(suggestionsWithCoupledFlag(alreadyCoupled)),
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

  await page.route(
    `**/api/messages/${AI_MESSAGE_ID}/match-to-bezala`,
    (route) => {
      alreadyCoupled = true;
      route.fulfill({
        status: 409,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Redan kopplad' }),
      });
    },
  );

  await page.goto('/travel-tinder');
  await expect(page.getByTestId(`tt-payment-${MISSING_ID}`)).toBeVisible();
  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-couple')
    .click();

  await expect(page.getByText(/Redan kopplad — uppdaterar/i)).toBeVisible();
  await expect(page.getByTestId(`tt-payment-${MISSING_ID}`)).toHaveCount(0);
});

test('C20 — Couple skickar bill_line_id för aktuellt vald payment, inte stale', async ({ page }) => {
  /* Reproducerar Lovable→Finnair-race: två missing_receipts i listan.
   * Användaren börjar med första (auto-vald), byter sedan till andra,
   * och klickar Couple på AI-förslaget. Request-body MÅSTE referera
   * den senast valda raden — inte den ursprungligen auto-valda. */
  const MISSING_LOVABLE = 2197448;
  const MISSING_FINNAIR = 2210736;
  const LOVABLE_RECEIPT_ID = 615;

  function lovableReceipt() {
    return {
      ...aiMessage(),
      id: LOVABLE_RECEIPT_ID,
      message_id: 'm-615',
      vendor: 'Lovable',
      amount: 100,
      currency: 'EUR',
      receipt_date: '2026-05-05',
      file_name: '20260505 Lovable.pdf',
    };
  }

  const twoPayments = {
    missing_receipts: [
      {
        missing_receipt: {
          id: MISSING_FINNAIR,
          description: 'Finnair O9VAZGJ',
          amount: 554.5,
          currency: 'EUR',
          date: '2026-05-13',
        },
        suggestions: [],
      },
      {
        missing_receipt: {
          id: MISSING_LOVABLE,
          description: 'Lovable Labs',
          amount: 100,
          currency: 'EUR',
          date: '2026-05-05',
        },
        suggestions: [
          {
            message: lovableReceipt(),
            score: 95,
            score_breakdown: { amount: 50, date: 30, vendor: 15 },
          },
        ],
      },
    ],
    all_messages: [lovableReceipt()],
  };

  await setupTinderMocks(page, twoPayments);

  let captured = null;
  await page.route(
    `**/api/messages/${LOVABLE_RECEIPT_ID}/match-to-bezala`,
    (route) => {
      captured = route.request().postDataJSON();
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: LOVABLE_RECEIPT_ID,
          bezala_upload_status: 'success',
          bezala_transaction_id: String(MISSING_LOVABLE),
        }),
      });
    },
  );

  await page.goto('/travel-tinder');
  // Default-auto-välj = första raden = Finnair
  await expect(
    page.getByTestId(`tt-payment-${MISSING_FINNAIR}`),
  ).toHaveAttribute('aria-pressed', 'true');

  // Byt urval till Lovable
  await page.getByTestId(`tt-payment-${MISSING_LOVABLE}`).click();
  await expect(
    page.getByTestId(`tt-payment-${MISSING_LOVABLE}`),
  ).toHaveAttribute('aria-pressed', 'true');
  // AI-kortet ska nu visa Lovable-kandidaten
  await expect(page.getByTestId('tt-candidate-ai')).toBeVisible();

  const reqPromise = page.waitForRequest((req) =>
    req.url().includes(`/api/messages/${LOVABLE_RECEIPT_ID}/match-to-bezala`) &&
    req.method() === 'POST',
  );
  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-couple')
    .click();
  await reqPromise;

  // Wire-format-assertion: bill_line_id = Lovable, INTE den
  // auto-valda Finnair-raden.
  expect(captured).toEqual({ missing_receipt_id: MISSING_LOVABLE });
});

test('C14 — fel återställer UI så användaren kan försöka igen', async ({ page }) => {
  await setupTinderMocks(page);

  let calls = 0;
  await page.route(
    `**/api/messages/${AI_MESSAGE_ID}/match-to-bezala`,
    (route) => {
      calls += 1;
      if (calls === 1) {
        route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Bezala nere' }),
        });
      } else {
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            id: AI_MESSAGE_ID,
            bezala_upload_status: 'success',
            bezala_transaction_id: String(MISSING_ID),
          }),
        });
      }
    },
  );

  await page.goto('/travel-tinder');
  await expect(page.getByTestId('tt-candidate-ai')).toBeVisible();

  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-couple')
    .click();

  await expect(page.getByText(/Kunde inte koppla/i)).toBeVisible();

  // Raden ska INTE vara borta, och Couple-knappen ska vara tillgänglig
  // igen för retry.
  await expect(page.getByTestId(`tt-payment-${MISSING_ID}`)).toBeVisible();
  await expect(
    page.getByTestId('tt-candidate-ai').getByTestId('tt-candidate-couple'),
  ).toBeEnabled();

  // Retry → andra POST:en lyckas
  await page
    .getByTestId('tt-candidate-ai')
    .getByTestId('tt-candidate-couple')
    .click();
  await expect(page.getByText(/Matchat:\s*Moovy/i)).toBeVisible();
  expect(calls).toBe(2);
});
