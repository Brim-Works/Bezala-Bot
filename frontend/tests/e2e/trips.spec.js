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
