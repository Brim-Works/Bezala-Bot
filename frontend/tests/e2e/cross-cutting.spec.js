import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page);
});

test('Navigation — sidebar navigerar mellan vyerna', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/$/);

  // C47 — Granska/Kortmatchning/Travel Tinder är en sida, Logg+Match Health = Status.
  await page.getByRole('button', { name: 'Travel Tinder', exact: true }).click();
  await expect(page).toHaveURL(/\/travel-tinder$/);

  await page.getByRole('button', { name: 'Status', exact: true }).click();
  await expect(page).toHaveURL(/\/status$/);

  await page.getByRole('button', { name: 'Inställningar', exact: true }).click();
  await expect(page).toHaveURL(/\/settings$/);

  await page.getByRole('button', { name: 'Översikt', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
});

test('Tema-växling persisterar över reload', async ({ page }) => {
  await page.goto('/review');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'A');

  await page.getByRole('radio', { name: 'Skog' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'B');

  // Reload — tema B ska kvarstå
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'B');
});

test('Språk-växling uppdaterar nav + rubriker', async ({ page }) => {
  await page.goto('/review');
  await expect(page.getByRole('heading', { name: /Granska innan överföring/ })).toBeVisible();

  await page.getByRole('radio', { name: 'EN' }).click();
  await expect(page.getByRole('heading', { name: /Review before transfer/ })).toBeVisible();
  // Nav-labels
  await expect(page.getByRole('button', { name: 'Overview' })).toBeVisible();
});

test('SPA-fallback: reload på /review laddar Review-vyn', async ({ page }) => {
  await page.goto('/review');
  await expect(page.getByTestId('review-grid')).toBeVisible();

  await page.reload();
  await expect(page.getByTestId('review-grid')).toBeVisible();
});
