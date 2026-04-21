import { expect, test } from '@playwright/test';
import { buildMessages, setupApiMocks } from './fixtures.js';

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page);
});

test('Drawer — Dashboard-rad-klick öppnar drawer på Gmail-fliken', async ({
  page,
}) => {
  await page.goto('/');
  // Vänta tills tabellen renderats med data
  await expect(page.getByText('Finnair', { exact: false }).first()).toBeVisible();

  const firstRow = page.locator('tr[data-row-id]').first();
  await firstRow.click();

  await expect(page.getByTestId('drawer')).toBeVisible();
  await expect(page.getByTestId('drawer-tab-gmail')).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByTestId('drawer-tab-gmail-content')).toBeVisible();
});

test('Drawer — 4 flikar + byte av innehåll', async ({ page }) => {
  await page.goto('/');
  await page.locator('tr[data-row-id]').first().click();

  for (const tab of ['gmail', 'ai', 'drive', 'bezala']) {
    await page.getByTestId(`drawer-tab-${tab}`).click();
    await expect(page.getByTestId(`drawer-tab-${tab}-content`)).toBeVisible();
  }
});

test('Drawer — Esc stänger', async ({ page }) => {
  await page.goto('/');
  await page.locator('tr[data-row-id]').first().click();
  await expect(page.getByTestId('drawer')).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(page.getByTestId('drawer')).toHaveCount(0);
});

test('Drawer — overlay-klick stänger', async ({ page }) => {
  await page.goto('/');
  await page.locator('tr[data-row-id]').first().click();
  await expect(page.getByTestId('drawer-overlay')).toBeVisible();
  await page.getByTestId('drawer-overlay').click();
  await expect(page.getByTestId('drawer')).toHaveCount(0);
});

test('Drawer — pil-höger/vänster byter flik', async ({ page }) => {
  await page.goto('/');
  await page.locator('tr[data-row-id]').first().click();

  // Fokus måste ligga på aktiv tab för att key-handlern ska fyra.
  await page.getByTestId('drawer-tab-gmail').focus();

  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('drawer-tab-ai')).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('drawer-tab-drive')).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByTestId('drawer-tab-ai')).toHaveAttribute(
    'aria-selected',
    'true',
  );
});

test('Drawer Bezala-tab — pending-raden visar gul banner + CTA', async ({
  page,
}) => {
  await page.goto('/');
  // Rad id=1 har bezala_upload_status='pending'
  await page.locator('tr[data-row-id="1"]').click();
  await page.getByTestId('drawer-tab-bezala').click();
  await expect(page.getByTestId('bezala-banner-pending')).toBeVisible();
});

test('Drawer Bezala-tab — transferred-raden visar grön banner + txn-ID', async ({
  page,
}) => {
  await page.goto('/');
  // Rad id=4 har bezala_upload_status='success' → transferred
  await page.locator('tr[data-row-id="4"]').click();
  await page.getByTestId('drawer-tab-bezala').click();
  await expect(page.getByTestId('bezala-banner-transferred')).toBeVisible();
  await expect(page.getByText('bez-txn-004')).toBeVisible();
});

test('Drawer Bezala-tab — failed-raden har röd banner + retry-knapp', async ({
  page,
}) => {
  await page.goto('/');
  // Rad id=5 har bezala_upload_status='failed' → error
  await page.locator('tr[data-row-id="5"]').click();
  await page.getByTestId('drawer-tab-bezala').click();
  await expect(page.getByTestId('bezala-banner-error')).toBeVisible();

  const uploadReq = page.waitForRequest(
    (req) =>
      req.url().includes('/api/messages/5/upload-to-bezala') &&
      req.method() === 'POST',
  );
  await page.getByTestId('bezala-retry').click();
  await uploadReq;
  await expect(page.getByText(/Bezala-överföring klar/i)).toBeVisible();
});

test('Drawer — Sidebar-pipeline-ikon öppnar drawer på rätt flik', async ({
  page,
}) => {
  await page.goto('/');
  // Välj en rad först (utan att öppna drawer: klick öppnar drawer i nuvarande
  // design. Vi stänger drawern direkt efter och klickar pipeline-ikonen.)
  await page.locator('tr[data-row-id="2"]').click();
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('drawer')).toHaveCount(0);

  await page.getByTestId('pipeline-nav-sidebar-drive').click();
  await expect(page.getByTestId('drawer')).toBeVisible();
  await expect(page.getByTestId('drawer-tab-drive')).toHaveAttribute(
    'aria-selected',
    'true',
  );
});
