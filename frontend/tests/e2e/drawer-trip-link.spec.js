import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

let apiState;

test.beforeEach(async ({ page }) => {
  apiState = await setupApiMocks(page, {
    availableTripsForMessage: {
      'gmail-msg-1': [
        {
          id: 11,
          title: 'Stockholm 30 apr - 2 maj',
          destination: 'Stockholm',
          start_date: '2026-04-30',
          end_date: '2026-05-02',
          status: 'active',
          is_linked: false,
          added_by: null,
        },
        {
          id: 12,
          title: 'Berlin 15 jun - 18 jun',
          destination: 'Berlin',
          start_date: '2026-06-15',
          end_date: '2026-06-18',
          status: 'active',
          is_linked: false,
          added_by: null,
        },
      ],
      __default: [],
    },
  });
});

async function openDrawerForFinnair(page) {
  await page.goto('/');
  await expect(page.getByText('Finnair', { exact: false }).first()).toBeVisible();
  await page.locator('tr[data-row-id="1"]').click();
  await expect(page.getByTestId('drawer')).toBeVisible();
}

test('Drawer visar Resor-sektion under flikarna', async ({ page }) => {
  await openDrawerForFinnair(page);
  await expect(page.getByTestId('drawer-trip-link')).toBeVisible();
  await expect(page.getByTestId('drawer-trip-available-11')).toBeVisible();
  await expect(page.getByTestId('drawer-trip-available-12')).toBeVisible();
});

test('Drawer: koppla kvitto till resa via checkbox → POST', async ({ page }) => {
  await openDrawerForFinnair(page);
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/messages/gmail-msg-1/link-to-trip') &&
      req.method() === 'POST',
  );
  // Klicka — check() väntar tills checkboxen är checked, men efter
  // POST flyttas raden till linked-sektionen och checkboxen unmountas.
  await page.getByTestId('drawer-trip-toggle-11').click();
  const req = await reqPromise;
  expect(req.postDataJSON()).toEqual({ trip_id: 11 });

  await expect(page.getByTestId('drawer-trip-linked-11')).toBeVisible();
  await expect(page.getByTestId('drawer-trip-source-11')).toContainText(
    /Manuellt/i,
  );
});

test('Drawer: koppla bort via uncheck → DELETE', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    availableTripsForMessage: {
      'gmail-msg-1': [
        {
          id: 11,
          title: 'Stockholm 30 apr - 2 maj',
          destination: 'Stockholm',
          start_date: '2026-04-30',
          end_date: '2026-05-02',
          status: 'active',
          is_linked: true,
          added_by: 'manual',
        },
      ],
      __default: [],
    },
  });
  await openDrawerForFinnair(page);

  await expect(page.getByTestId('drawer-trip-linked-11')).toBeVisible();

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/messages/gmail-msg-1/unlink-from-trip/11') &&
      req.method() === 'DELETE',
  );
  // Klicka istället för uncheck() — efter unlink unmountas checkboxen
  // (raden flyttas till available-sektionen) så uncheck väntar förgäves.
  await page.getByTestId('drawer-trip-toggle-11').click();
  await reqPromise;

  // Efter unlink hamnar resan i "available"-listan
  await expect(page.getByTestId('drawer-trip-available-11')).toBeVisible();
});

test('Drawer visar tom-state när inga resor matchar', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    availableTripsForMessage: { __default: [] },
  });
  await openDrawerForFinnair(page);
  await expect(page.getByTestId('drawer-trip-empty')).toBeVisible();
});
