import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page);
});

test('Settings — öppnas med förifyllda värden från API', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByTestId('settings-view')).toBeVisible();

  // Default från mocken: confidence_threshold = 90
  await expect(page.getByTestId('confidence-value')).toHaveText('90%');
  // scan_interval_minutes = 60
  await expect(page.getByTestId('scan-interval')).toHaveValue('60');
});

test('Settings — ChipEditor lägger till en sender', async ({ page }) => {
  await page.goto('/settings');
  const input = page.getByTestId('include-senders-input');
  await input.fill('finnair.com');
  await input.press('Enter');
  await expect(page.getByText('finnair.com', { exact: true })).toBeVisible();
});

test('Settings — ChipEditor tar bort en sender', async ({ page }) => {
  await page.goto('/settings');
  const input = page.getByTestId('exclude-senders-input');
  await input.fill('newsletter@example.com');
  await input.press('Enter');
  await expect(page.getByText('newsletter@example.com', { exact: true })).toBeVisible();

  await page.getByTestId('exclude-senders-remove-newsletter@example.com').click();
  await expect(page.getByText('newsletter@example.com', { exact: true })).toHaveCount(0);
});

test('Settings — confidence-slider uppdaterar värdet', async ({ page }) => {
  await page.goto('/settings');
  // Aktivera auto-upload först så att slidern inte är disablad
  await page.getByTestId('toggle-auto-upload').check();
  const slider = page.locator('input[type="range"]');
  // Sätt värdet till 80 via fill (fungerar för range-inputs i Playwright)
  await slider.fill('80');
  await expect(page.getByTestId('confidence-value')).toHaveText('80%');
});

test('Settings — scan-interval-dropdown ändrar värde', async ({ page }) => {
  await page.goto('/settings');
  await page.getByTestId('scan-interval').selectOption('15');
  await expect(page.getByTestId('scan-interval')).toHaveValue('15');
});

test('Settings — checkboxes togglar filter-inställningar', async ({ page }) => {
  await page.goto('/settings');
  const promo = page.getByTestId('toggle-exclude_promotions');
  await expect(promo).toBeChecked();
  await promo.uncheck();
  await expect(promo).not.toBeChecked();
});

test('Settings — Spara triggar PUT /api/settings + toast', async ({ page }) => {
  const putRequest = page.waitForRequest(
    (req) => req.url().includes('/api/settings') && req.method() === 'PUT',
  );

  await page.goto('/settings');

  // Gör en ändring så Spara blir aktiv
  await page.getByTestId('scan-interval').selectOption('30');

  await page.getByTestId('save-settings').click();
  const req = await putRequest;
  const body = req.postDataJSON();
  expect(body.scan_interval_minutes).toBe(30);

  await expect(page.getByText(/Inställningar sparade/i)).toBeVisible();
});

test('Settings — båda teman + EN', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByTestId('settings-view')).toBeVisible();

  await page.getByRole('radio', { name: 'Skog' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'B');
  await expect(page.getByTestId('settings-view')).toBeVisible();

  await page.getByRole('radio', { name: 'EN' }).click();
  await expect(page.getByText('Automation', { exact: true })).toBeVisible();
});
