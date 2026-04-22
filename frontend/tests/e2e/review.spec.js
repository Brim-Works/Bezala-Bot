import { expect, test } from '@playwright/test';
import { buildMessages, buildStats, buildRuns, setupApiMocks } from './fixtures.js';

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page);
});

test('Review — renderar vyn när man navigerar till /review', async ({ page }) => {
  await page.goto('/review');
  await expect(page.getByRole('heading', { name: /Granska innan överföring/i })).toBeVisible();
  await expect(page.getByTestId('review-grid')).toBeVisible();
});

test('Review — kön visar pending-rader med receipt_date DESC (Gate 3)', async ({ page }) => {
  await page.goto('/review');
  const queue = page.getByTestId('review-queue');
  await expect(queue).toBeVisible();
  // Av 5 fixturer är 3 pending (id 1, 2, 3).
  const items = queue.locator('[data-testid^="queue-item-"]');
  await expect(items).toHaveCount(3);
  // Receipt dates: id 1=2026-04-20, id 2=2026-04-15, id 3=2026-04-10.
  // Default sort = receipt_date DESC → id 1 överst.
  const firstId = await items.first().getAttribute('data-testid');
  expect(firstId).toBe('queue-item-1');
});

test('Review — klick på en rad uppdaterar formulär + PDF-preview', async ({ page }) => {
  await page.goto('/review');
  const form = page.getByTestId('review-form');
  await expect(form).toBeVisible();

  // Klicka på SL-raden (id 2)
  await page.getByTestId('queue-item-2').click();

  // Vendor-input ska innehålla SL
  const vendorInput = form.locator('input').first();
  await expect(vendorInput).toHaveValue('SL');

  // PDF-iframen ska peka på rätt Drive-URL
  const iframe = page.getByTestId('pdf-iframe');
  await expect(iframe).toHaveAttribute(
    'src',
    'https://drive.google.com/file/d/drive-file-2/preview',
  );
});

test('Review — alla 10 formfält renderas med AI-värden', async ({ page }) => {
  await page.goto('/review');
  const form = page.getByTestId('review-form');
  await expect(form).toBeVisible();

  // Labels för alla fält
  const labels = [
    'Leverantör',
    'Datum',
    'Belopp',
    'Valuta',
    'Moms',
    'Kategori',
    'Projekt',
    'Betalningssätt',
    'Filnamn',
    'Kommentar',
  ];
  for (const label of labels) {
    await expect(form.getByText(label, { exact: true })).toBeVisible();
  }
});

test('Review — redigering ger edited-markering + räknare', async ({ page }) => {
  await page.goto('/review');
  const form = page.getByTestId('review-form');

  // Ändra vendor-fältet
  const vendorInput = form.locator('input').first();
  await vendorInput.fill('Finnair AB');

  // Räknaren visas
  await expect(page.getByTestId('edited-count')).toContainText('1');

  // Ändra datum också
  const dateInput = form.locator('input[type="date"]').first();
  await dateInput.fill('2026-04-22');
  await expect(page.getByTestId('edited-count')).toContainText('2');
});

test('Review — godkänn anropar upload-endpoint + raden försvinner', async ({
  page,
}) => {
  await setupApiMocks(page);
  // id 1 är nu först (receipt_date DESC efter Gate 3)
  const uploadRequest = page.waitForRequest(
    (req) =>
      req.url().includes('/api/messages/1/upload-to-bezala') &&
      req.method() === 'POST',
  );

  await page.goto('/review');
  await expect(page.getByTestId('queue-item-1')).toBeVisible();

  await page.getByTestId('approve-button').click();
  await uploadRequest;

  await expect(page.getByTestId('queue-item-1')).toHaveCount(0);
  await expect(page.getByText(/Skickat till Bezala/i)).toBeVisible();
});

test('Review — godkänn-fel återställer raden i kön med error-toast', async ({
  page,
}) => {
  await setupApiMocks(page, {
    uploadResponse: {
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Bad Gateway' }),
    },
  });

  await page.goto('/review');
  // id 1 är först efter Gate 3
  await expect(page.getByTestId('queue-item-1')).toBeVisible();

  await page.getByTestId('approve-button').click();

  await expect(page.getByTestId('queue-item-1')).toBeVisible();
  await expect(page.getByText(/misslyckades/i)).toBeVisible();
});

test('Review — Nästa-knappen navigerar mellan rader', async ({ page }) => {
  await page.goto('/review');
  const form = page.getByTestId('review-form');
  const vendorInput = form.locator('input').first();

  // Gate 3: receipt_date DESC → id 1 (Finnair, 2026-04-20) överst
  await expect(vendorInput).toHaveValue('Finnair');
  await page.getByRole('button', { name: /Nästa/ }).click();
  await expect(vendorInput).toHaveValue('SL'); // id 2 (2026-04-15)
  await page.getByRole('button', { name: /Nästa/ }).click();
  await expect(vendorInput).toHaveValue('Scandic'); // id 3 (2026-04-10, äldst)
});

test('Review — tom-state när inga pending (SV + EN)', async ({ page }) => {
  // Byt ut fixture till bara non-pending rader
  const all = buildMessages().map((m) => ({
    ...m,
    bezala_upload_status: 'success',
  }));
  await setupApiMocks(page, { messages: all });
  await page.goto('/review');

  await expect(page.getByTestId('review-empty')).toBeVisible();
  await expect(page.getByText(/Kön är tom/i)).toBeVisible();

  // Byt till engelska
  await page.getByRole('radio', { name: 'EN' }).click();
  await expect(page.getByText(/Queue is empty/i)).toBeVisible();
});

test('Review — båda teman renderar utan error', async ({ page }) => {
  await page.goto('/review');
  // Tema A är default. Verifiera data-theme
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'A');
  await expect(page.getByTestId('review-grid')).toBeVisible();

  // Växla till Skog
  await page.getByRole('radio', { name: 'Skog' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'B');
  await expect(page.getByTestId('review-grid')).toBeVisible();
});
