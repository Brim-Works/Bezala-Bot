import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

let apiState;

test.beforeEach(async ({ page }) => {
  apiState = await setupApiMocks(page);
});

test('Settings — Excluded vendors-sektion visar system-listan', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByTestId('excluded-vendors-section')).toBeVisible();
  await expect(page.getByTestId('excluded-vendors-system-list')).toBeVisible();
  await expect(page.getByText('anthropic', { exact: false })).toBeVisible();
});

test('Settings — Lägg till egen vendor öppnar modal + POST:ar', async ({ page }) => {
  await page.goto('/settings');
  await page.getByTestId('excluded-vendors-add').click();
  await expect(page.getByTestId('excluded-vendors-add-modal')).toBeVisible();

  await page.getByTestId('excluded-vendors-pattern-input').fill('mitt-saas');
  await page.getByTestId('excluded-vendors-description-input').fill('Egen tjänst');

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().endsWith('/api/excluded-vendors') &&
      req.method() === 'POST',
  );
  await page.getByTestId('excluded-vendors-confirm').click();
  const req = await reqPromise;
  expect(req.postDataJSON()).toEqual({
    pattern: 'mitt-saas',
    description: 'Egen tjänst',
  });

  await expect(page.getByTestId('excluded-vendors-add-modal')).toHaveCount(0);
  await expect(
    page.getByTestId('excluded-vendors-user-list'),
  ).toContainText('mitt-saas');
});

test('Settings — Ta bort egen vendor', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    excludedVendors: [
      {
        id: 50,
        pattern: 'min-vendor',
        description: 'Test',
        added_by: 'user',
        created_at: new Date().toISOString(),
      },
    ],
  });

  await page.goto('/settings');
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().endsWith('/api/excluded-vendors/50') &&
      req.method() === 'DELETE',
  );
  await page.getByTestId('excluded-vendor-remove-50').click();
  await reqPromise;

  await expect(page.getByTestId('excluded-vendors-user-empty')).toBeVisible();
});
