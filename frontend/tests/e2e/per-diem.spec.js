import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

let apiState;

test.beforeEach(async ({ page }) => {
  apiState = await setupApiMocks(page);
});

async function openPerDiemModal(page) {
  await page.goto('/trips');
  await page.getByTestId('trip-active-2').click();
  await expect(page.getByTestId('trip-drawer-2')).toBeVisible();
  await page.getByTestId('trip-drawer-per-diem').click();
  await expect(page.getByTestId('per-diem-modal-2')).toBeVisible();
}

test('Drawer på aktiv resa visar Beräkna traktamente-knapp', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-active-2').click();
  await expect(page.getByTestId('trip-drawer-2')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-per-diem')).toBeVisible();
});

test('Klick på Beräkna traktamente öppnar modal', async ({ page }) => {
  await openPerDiemModal(page);
  await expect(page.getByTestId('per-diem-step-1')).toBeVisible();
  await expect(page.getByTestId('per-diem-step-2')).toBeVisible();
  await expect(page.getByTestId('per-diem-step-3')).toBeVisible();
});

test('Modal auto-extraherar flygtider och fyller i fälten', async ({ page }) => {
  const extractPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/trips/2/extract-flight-times') &&
      req.method() === 'POST',
  );
  await openPerDiemModal(page);
  await extractPromise;
  await expect(page.getByTestId('per-diem-ai-suggestion')).toContainText('Sverige');
  await expect(page.getByTestId('per-diem-route')).toContainText(
    'Helsinki - Stockholm - Helsinki',
  );
  await expect(page.getByTestId('per-diem-country')).toHaveValue('SE');
});

async function waitForExtract(page) {
  // Vänta tills extract-anropet är klart (AI-suggestion visas)
  await expect(page.getByTestId('per-diem-ai-suggestion')).toBeVisible();
}

test('Beräkna-knappen visar dygnet-listan och total', async ({ page }) => {
  await openPerDiemModal(page);
  await waitForExtract(page);
  const calcPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/trips/2/calculate-per-diem') &&
      req.method() === 'POST',
  );
  await page.getByTestId('per-diem-calculate').click();
  await calcPromise;
  await expect(page.getByTestId('per-diem-dygn-1')).toBeVisible();
  await expect(page.getByTestId('per-diem-dygn-2')).toBeVisible();
  await expect(page.getByTestId('per-diem-total')).toContainText('140');
});

test('Mat-toggle på dygn 1 halverar beloppet via PATCH', async ({ page }) => {
  await openPerDiemModal(page);
  await waitForExtract(page);
  const calcPromise = page.waitForRequest((req) =>
    req.url().includes('/api/trips/2/calculate-per-diem'),
  );
  await page.getByTestId('per-diem-calculate').click();
  await calcPromise;
  await expect(page.getByTestId('per-diem-dygn-1')).toBeVisible();
  const patchPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/trips/2/per-diem') && req.method() === 'PATCH',
  );
  await page.getByTestId('per-diem-meal-1').check();
  const patchReq = await patchPromise;
  expect(patchReq.postDataJSON().meal_toggles['1']).toBe(true);
  await expect(page.getByTestId('per-diem-total')).toContainText('105');
});

test('Spara stänger modalen', async ({ page }) => {
  await openPerDiemModal(page);
  await waitForExtract(page);
  const calcPromise = page.waitForRequest((req) =>
    req.url().includes('/api/trips/2/calculate-per-diem'),
  );
  await page.getByTestId('per-diem-calculate').click();
  await calcPromise;
  await expect(page.getByTestId('per-diem-dygn-1')).toBeVisible();
  await page.getByTestId('per-diem-save').click();
  await expect(page.getByTestId('per-diem-modal-2')).toHaveCount(0);
});

test('Land-dropdown kan ändras till Norge', async ({ page }) => {
  await openPerDiemModal(page);
  await waitForExtract(page);
  const select = page.getByTestId('per-diem-country');
  await select.selectOption('NO');
  await expect(select).toHaveValue('NO');
});

test('Avbryt-knappen stänger modalen utan att spara', async ({ page }) => {
  await openPerDiemModal(page);
  await page.getByTestId('per-diem-cancel').click();
  await expect(page.getByTestId('per-diem-modal-2')).toHaveCount(0);
});
