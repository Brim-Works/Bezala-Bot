import { expect, test } from '@playwright/test';
import {
  buildActiveTrip,
  buildTripSuggestion,
  setupApiMocks,
} from './fixtures.js';

let apiState;

test.beforeEach(async ({ page }) => {
  apiState = await setupApiMocks(page);
});

test('Sidebar har en Resor-flik och navigerar till /trips', async ({ page }) => {
  await page.goto('/');
  const link = page.getByTestId('nav-trips');
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/trips$/);
  await expect(page.getByTestId('trips-view')).toBeVisible();
});

test('Resor-vyn visar förslag och aktiva resor', async ({ page }) => {
  await page.goto('/trips');
  await expect(page.getByTestId('trips-view')).toBeVisible();
  await expect(page.getByTestId('trip-suggestion-1')).toBeVisible();
  await expect(page.getByTestId('trip-active-2')).toBeVisible();
  await expect(
    page.getByText('Stockholm 30 apr - 2 maj 2026', { exact: false }),
  ).toBeVisible();
});

test('Acceptera-knappen flyttar förslag till aktiva resor', async ({ page }) => {
  await page.goto('/trips');
  const accept = page.getByTestId('trip-accept-1');
  await accept.click();
  // Suggestion försvinner, ny aktiv resa dyker upp
  await expect(page.getByTestId('trip-suggestion-1')).toHaveCount(0);
  await expect(page.getByTestId('trip-active-1')).toBeVisible();
});

test('Justera-knappen öppnar edit-modal med förfyllda värden', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-edit-1').click();
  await expect(page.getByTestId('trip-edit-modal-1')).toBeVisible();
  const titleInput = page.getByTestId('trip-edit-title');
  await expect(titleInput).toHaveValue('Stockholm 30 apr - 2 maj 2026');
  await titleInput.fill('Min Stockholm-resa');
  await page.getByTestId('trip-edit-save').click();
  await expect(page.getByTestId('trip-edit-modal-1')).toHaveCount(0);
});

test('Visa-knappen öppnar drawer med kvitton', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-show-1').click();
  await expect(page.getByTestId('trip-drawer-1')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-receipt-gmail-msg-1')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-receipt-gmail-msg-2')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-receipt-gmail-msg-3')).toBeVisible();
});

test('Drawer: ta bort kvitto från resa', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-show-1').click();
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().match(/\/api\/trips\/1$/) && req.method() === 'PATCH',
  );
  await page.getByTestId('trip-drawer-remove-gmail-msg-3').click();
  const req = await reqPromise;
  const body = req.postDataJSON();
  expect(body.remove_message_ids).toContain('gmail-msg-3');
});

test('Drawer: bad-feedback öppnar modal och POST:ar feedback', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-show-1').click();
  await page.getByTestId('trip-drawer-bad-feedback').click();
  await expect(page.getByTestId('trip-feedback-modal-1')).toBeVisible();
  await page.getByTestId('trip-feedback-option-wrong_dates').check();
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/trips/1/feedback') && req.method() === 'POST',
  );
  await page.getByTestId('trip-feedback-submit').click();
  const req = await reqPromise;
  expect(req.postDataJSON().feedback_type).toBe('wrong_dates');
});

test('Refresh-knappen anropar refresh-suggestions', async ({ page }) => {
  await page.goto('/trips');
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/trips/refresh-suggestions') &&
      req.method() === 'POST',
  );
  await page.getByTestId('trips-refresh').click();
  await reqPromise;
});

test('Tom suggestions-lista visar empty-state', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    tripSuggestions: [],
    activeTrips: [buildActiveTrip()],
  });
  await page.goto('/trips');
  await expect(page.getByTestId('trips-suggestions-empty')).toBeVisible();
});

test('Tom active-lista visar empty-state', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    tripSuggestions: [buildTripSuggestion()],
    activeTrips: [],
  });
  await page.goto('/trips');
  await expect(page.getByTestId('trips-active-empty')).toBeVisible();
});

test('Trip-drawer skiljer AI-förslag från manuellt tillagda kvitton', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    tripSuggestions: [],
    activeTrips: [
      buildActiveTrip({
        id: 5,
        title: 'Stockholm 30 apr',
        messages: [
          {
            id: 100,
            message_id: 'ai-1',
            vendor: 'Finnair',
            amount: 200,
            currency: 'EUR',
            receipt_date: '2026-04-30',
            received_at: '2026-04-29T08:00:00Z',
            category: 'Flyg',
            subject: 'Boarding',
            summary: 'Flyg',
            added_by: 'ai_suggestion',
          },
          {
            id: 101,
            message_id: 'manual-1',
            vendor: 'Restaurant',
            amount: 32,
            currency: 'EUR',
            receipt_date: '2026-05-01',
            received_at: '2026-05-01T20:00:00Z',
            category: 'Mat',
            subject: 'Lunch',
            summary: 'Lunch',
            added_by: 'manual',
          },
        ],
      }),
    ],
  });
  await page.goto('/trips');
  await page.getByTestId('trip-active-5').click();
  await expect(page.getByTestId('trip-drawer-ai-label')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-manual-label')).toBeVisible();
  await expect(
    page.getByTestId('trip-drawer-receipt-ai-1'),
  ).toHaveAttribute('data-added-by', 'ai_suggestion');
  await expect(
    page.getByTestId('trip-drawer-receipt-manual-1'),
  ).toHaveAttribute('data-added-by', 'manual');
});

// ---------- Resor R1 — kalenderresor ----------

const CAL_TRIP = {
  id: 7,
  start_date: '2026-04-14',
  end_date: '2026-04-15',
  destination: 'Stockholm',
  open_return: false,
  status: 'suggested',
  flights: [],
  hotels: [{ id: 'h1', name: 'Scandic Klara' }],
  source_event_ids: ['f1', 'h1', 'm1', 'f2'],
  days: [
    {
      date: '2026-04-14', start_time: '16:00', end_time: null,
      destination: 'Stockholm', meetings: [],
      hotel: { id: 'h1', name: 'Scandic Klara' }, source_event_ids: ['f1', 'h1'],
    },
    {
      date: '2026-04-15', start_time: null, end_time: '20:05',
      destination: 'Stockholm',
      meetings: [{ id: 'm1', summary: 'Workshop Visma', start_time: '09:00', end_time: '15:00', location: '' }],
      hotel: null, source_event_ids: ['m1', 'f2'],
    },
  ],
  receipts: [
    { message_id: 'r1', vendor: 'Finnair', amount: 250, currency: 'EUR', receipt_date: '2026-04-10', trip_id: 1 },
  ],
};

test('Kalenderresor visas som kort med tider, hotell, möten och kvitton', async ({ page }) => {
  await setupApiMocks(page, { calendarTrips: [CAL_TRIP] });
  await page.goto('/trips');
  const card = page.getByTestId('calendar-trip-7');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Stockholm');
  await expect(card).toContainText('Avresa 16:00');
  await expect(card).toContainText('Hemkomst 20:05');
  await expect(card).toContainText('Scandic Klara');
  await expect(card).toContainText('Workshop Visma');
  await expect(card).toContainText('Finnair');
});

test('Läs kalendern utan consent visar Återanslut Gmail', async ({ page }) => {
  await setupApiMocks(page, {
    calendarRefreshResponse: { status: 'consent_required', detail: 'x' },
  });
  await page.goto('/trips');
  await expect(page.getByTestId('trips-calendar-empty')).toBeVisible();
  await page.getByTestId('trips-calendar-refresh').click();
  const notice = page.getByTestId('trips-calendar-notice');
  await expect(notice).toBeVisible();
  await expect(notice.getByRole('link', { name: 'Återanslut Gmail' })).toHaveAttribute(
    'href', '/api/auth/gmail/start',
  );
});

test('Läs kalendern visar nya resor och ogrupperade event', async ({ page }) => {
  await setupApiMocks(page, {
    calendarRefreshResponse: {
      status: 'ok',
      stats: { trips: 1 },
      trips: [CAL_TRIP],
      ungrouped: [{ id: 'h9', summary: 'Hotel Kämp', date: '2026-06-03', reason: 'hotel_without_flight' }],
    },
  });
  await page.goto('/trips');
  await page.getByTestId('trips-calendar-refresh').click();
  await expect(page.getByTestId('calendar-trip-7')).toBeVisible();
  await expect(page.getByTestId('trips-calendar-ungrouped')).toContainText('Kunde inte grupperas (1)');
});
