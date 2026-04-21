import { expect, test } from '@playwright/test';
import { buildMessages, setupApiMocks } from './fixtures.js';

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page);
});

test('Trash — nav-item är synlig i sidebar', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('nav-trash')).toBeVisible();
  await expect(
    page.getByTestId('nav-trash').getByText('Papperskorg'),
  ).toBeVisible();
});

test('Trash — Dashboard-rad soft-deletas via dialog + undo-toast', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.locator('tr[data-row-id="1"]')).toBeVisible();

  await page.getByTestId('row-delete-1').click();
  await expect(page.getByTestId('delete-reason-dialog')).toBeVisible();
  await page.getByTestId('reason-calendar').click();
  await page.getByTestId('confirm-delete').click();

  // Undo-toast
  await expect(page.getByText(/Rad borttagen/i)).toBeVisible();
  await expect(page.getByTestId('toast-action')).toBeVisible();
  // Raden är borta
  await expect(page.locator('tr[data-row-id="1"]')).toHaveCount(0);
});

test('Trash — Ångra återställer raden direkt', async ({ page }) => {
  await page.goto('/');
  await page.getByTestId('row-delete-2').click();
  await page.getByTestId('confirm-delete').click();
  await expect(page.locator('tr[data-row-id="2"]')).toHaveCount(0);

  await page.getByTestId('toast-action').click();
  await expect(page.getByText(/Rad återställd/i)).toBeVisible();
  await expect(page.locator('tr[data-row-id="2"]')).toBeVisible();
});

test('Trash — navigering till /trash visar borttagna rader med reason-pill', async ({
  page,
}) => {
  const msgs = buildMessages();
  msgs[0].deleted_at = new Date(Date.now() - 60000).toISOString();
  msgs[0].delete_reason = 'calendar';
  await setupApiMocks(page, { messages: msgs });

  await page.goto('/trash');
  await expect(page.getByTestId('trash-view')).toBeVisible();
  await expect(page.locator('tr[data-row-id="1"]')).toBeVisible();
  await expect(page.getByText('Kalenderinbjudan')).toBeVisible();
});

test('Trash — restore från papperskorg tar bort raden därifrån', async ({
  page,
}) => {
  const msgs = buildMessages();
  msgs[0].deleted_at = new Date(Date.now() - 60000).toISOString();
  msgs[0].delete_reason = 'manual';
  await setupApiMocks(page, { messages: msgs });

  await page.goto('/trash');
  await expect(page.locator('tr[data-row-id="1"]')).toBeVisible();
  await page.getByTestId('restore-1').click();
  await expect(page.getByText(/Rad återställd/i)).toBeVisible();
  await expect(page.locator('tr[data-row-id="1"]')).toHaveCount(0);
});

test('Trash — hard-delete en rad: confirm-dialog, purge_drive-toggle', async ({
  page,
}) => {
  const msgs = buildMessages();
  msgs[0].deleted_at = new Date(Date.now() - 60000).toISOString();
  msgs[0].delete_reason = 'manual';
  await setupApiMocks(page, { messages: msgs });

  await page.goto('/trash');
  await page.getByTestId('hard-delete-1').click();
  await expect(page.getByTestId('hard-delete-dialog')).toBeVisible();
  // purge_drive är av default
  const purgeToggle = page.getByTestId('purge-drive-toggle');
  await expect(purgeToggle).not.toBeChecked();

  const req = page.waitForRequest(
    (r) =>
      r.url().includes('/api/messages/1') &&
      r.url().includes('permanent=true') &&
      r.method() === 'DELETE',
  );
  await page.getByTestId('confirm-hard-delete').click();
  const sent = await req;
  expect(sent.url()).toContain('purge_drive=false');
  await expect(page.getByText(/Rad raderad permanent/i)).toBeVisible();
});

test('Trash — töm papperskorgen med confirm', async ({ page }) => {
  const msgs = buildMessages();
  msgs.forEach((m) => {
    m.deleted_at = new Date(Date.now() - 60000).toISOString();
    m.delete_reason = 'manual';
  });
  await setupApiMocks(page, { messages: msgs });

  await page.goto('/trash');
  await page.getByTestId('empty-trash').click();
  await expect(page.getByTestId('hard-delete-dialog')).toBeVisible();
  await page.getByTestId('confirm-hard-delete').click();
  await expect(page.getByText(/Papperskorgen tömd/i)).toBeVisible();
  await expect(page.getByText(/Papperskorgen är tom/i)).toBeVisible();
});

test('Trash — bulk-select i Dashboard + Ta bort', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('tr[data-row-id="1"]')).toBeVisible();

  // Välj första två raderna via bulk-checkbox i cell
  const row1 = page.locator('tr[data-row-id="1"] [data-testid="bulk-checkbox"]');
  const row2 = page.locator('tr[data-row-id="2"] [data-testid="bulk-checkbox"]');
  await row1.check();
  await row2.check();

  const bulkBar = page.getByTestId('bulk-bar');
  await expect(bulkBar).toBeVisible();
  await expect(bulkBar).toContainText('2');

  await page.getByTestId('bulk-bar-delete').click();
  await expect(page.getByTestId('delete-reason-dialog')).toBeVisible();
  await page.getByTestId('confirm-delete').click();

  await expect(page.getByText(/Rader borttagna/i)).toBeVisible();
  await expect(page.locator('tr[data-row-id="1"]')).toHaveCount(0);
  await expect(page.locator('tr[data-row-id="2"]')).toHaveCount(0);
});

test('Trash — Review "Ta bort"-knapp triggar delete-flow', async ({ page }) => {
  await page.goto('/review');
  await expect(page.getByTestId('review-form')).toBeVisible();
  await page.getByTestId('review-delete').click();
  await expect(page.getByTestId('delete-reason-dialog')).toBeVisible();
  await page.getByTestId('confirm-delete').click();
  await expect(page.getByText(/Rad borttagen/i)).toBeVisible();
});

test('Trash — Settings auto-purge-dropdown default Aldrig + spara', async ({
  page,
}) => {
  await page.goto('/settings');
  const select = page.getByTestId('trash-auto-purge');
  await expect(select).toHaveValue('0');
  await select.selectOption('60');

  const putReq = page.waitForRequest(
    (r) => r.url().includes('/api/settings') && r.method() === 'PUT',
  );
  await page.getByTestId('save-settings').click();
  const req = await putReq;
  const body = req.postDataJSON();
  expect(body.trash_auto_purge_days).toBe(60);
});

test('Trash — sidebar-räknare uppdateras optimistic efter delete', async ({
  page,
}) => {
  await page.goto('/');
  // Initialt 0 → ingen badge
  await expect(page.getByTestId('trash-count-badge')).toHaveCount(0);

  await page.getByTestId('row-delete-1').click();
  await page.getByTestId('confirm-delete').click();

  // Badgen syns med siffran 1
  await expect(page.getByTestId('trash-count-badge')).toHaveText('1');
});
