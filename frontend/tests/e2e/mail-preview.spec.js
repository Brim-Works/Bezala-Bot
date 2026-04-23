import { expect, test } from '@playwright/test';
import {
  buildMessages,
  buildPendingDownloadMessage,
  setupApiMocks,
} from './fixtures.js';

/* Gate 5: mail-preview + fetch-pdf-from-url.
 * Bara needs_download-rader ska visa "Visa kvittomail"-knappen.
 * Klick hämtar GET /body, renderar iframe + länk-lista.
 * Klick på en länk → confirm-dialog → POST /fetch-pdf-from-url → toast. */

test('Gate 5 — "Visa kvittomail"-knapp syns bara för needs_download-rader', async ({
  page,
}) => {
  const pending = buildPendingDownloadMessage();
  await setupApiMocks(page, { messages: [...buildMessages(), pending] });

  await page.goto('/');
  await page.locator(`tr[data-row-id="${pending.id}"]`).click();

  await expect(page.getByTestId('drawer-tab-gmail-content')).toBeVisible();
  await expect(page.getByTestId('show-mail-preview')).toBeVisible();
});

test('Gate 5 — knappen finns INTE för en vanlig saved-rad', async ({ page }) => {
  await setupApiMocks(page);
  await page.goto('/');
  // Klicka rad 4 (Clas Ohlson, saved)
  await page.locator('tr[data-row-id="4"]').click();
  await expect(page.getByTestId('drawer-tab-gmail-content')).toBeVisible();
  await expect(page.getByTestId('show-mail-preview')).toHaveCount(0);
});

test('Gate 5 — klick "Visa kvittomail" laddar preview + länk-lista', async ({
  page,
}) => {
  const pending = buildPendingDownloadMessage();
  await setupApiMocks(page, { messages: [...buildMessages(), pending] });

  await page.goto('/');
  await page.locator(`tr[data-row-id="${pending.id}"]`).click();
  await page.getByTestId('show-mail-preview').click();

  // Iframe renderas med sandbox=""
  const frame = page.getByTestId('mail-preview-frame');
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute('sandbox', '');
  // Länk-listan visar den detekterade länken
  await expect(page.getByTestId('mail-preview-links')).toBeVisible();
  await expect(
    page.getByTestId('mail-preview-link-https://arlandaexpress.se/r/abc'),
  ).toBeVisible();
});

test('Gate 5 — klick på detekterad länk → confirm → POST + success-toast', async ({
  page,
}) => {
  const pending = buildPendingDownloadMessage();
  await setupApiMocks(page, { messages: [...buildMessages(), pending] });

  // Godkänn confirm-dialogen automatiskt
  page.on('dialog', (dialog) => dialog.accept());

  const postRequest = page.waitForRequest(
    (req) =>
      /\/api\/messages\/\d+\/fetch-pdf-from-url$/.test(req.url()) &&
      req.method() === 'POST',
  );

  await page.goto('/');
  await page.locator(`tr[data-row-id="${pending.id}"]`).click();
  await page.getByTestId('show-mail-preview').click();
  await page
    .getByTestId('mail-preview-link-https://arlandaexpress.se/r/abc')
    .click();

  const req = await postRequest;
  const body = req.postDataJSON();
  expect(body).toEqual({ url: 'https://arlandaexpress.se/r/abc' });

  // Success-toast
  await expect(
    page.getByText(/PDF hämtad och sparad till Drive/i),
  ).toBeVisible();
});

test('Gate 5 — fetch success → switch till Drive-flik + preview-iframe visas', async ({
  page,
}) => {
  const pending = buildPendingDownloadMessage();
  await setupApiMocks(page, { messages: [...buildMessages(), pending] });

  page.on('dialog', (dialog) => dialog.accept());

  await page.goto('/');
  await page.locator(`tr[data-row-id="${pending.id}"]`).click();
  await page.getByTestId('show-mail-preview').click();
  await page
    .getByTestId('mail-preview-link-https://arlandaexpress.se/r/abc')
    .click();

  // Efter success: drawern ska automatiskt byta till Drive-fliken
  await expect(page.getByTestId('drawer-tab-drive')).toHaveAttribute(
    'aria-selected',
    'true',
  );
  // Drive-tabben ska visa preview-iframen (inte längre "Kvittot ligger
  // bakom en länk"-bannern)
  await expect(page.getByTestId('drawer-drive-iframe')).toBeVisible();
  await expect(page.getByTestId('download-banner')).toHaveCount(0);
});

test('Gate 5 — fetch-pdf-from-url 422 → error-toast', async ({ page }) => {
  const pending = buildPendingDownloadMessage();
  await setupApiMocks(page, {
    messages: [...buildMessages(), pending],
    fetchPdfFromUrlResponse: {
      status: 422,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'Länken gav text/html istället för PDF' }),
    },
  });

  page.on('dialog', (dialog) => dialog.accept());

  await page.goto('/');
  await page.locator(`tr[data-row-id="${pending.id}"]`).click();
  await page.getByTestId('show-mail-preview').click();
  await page
    .getByTestId('mail-preview-link-https://arlandaexpress.se/r/abc')
    .click();

  await expect(page.getByText(/istället för PDF/i)).toBeVisible();
});
