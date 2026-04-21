/* E2E-tester för länk-fetch-flödet.
 * Täcker:
 *  - Dashboard: needs_download-rad får nedladdningsikon + gul FileBadge
 *  - Drawer: Drive-fliken visar banner + pending_link + "Hämta PDF"
 *  - fetch-pdf → toast + raden uppdateras till saved
 *  - fetch-pdf fail → toast + raden behåller needs_manual_download
 *  - Settings: BuiltinSendersBlock renderas read-only
 *  - Settings: LinkFetchSection chip-editor + min-confidence-slider
 */

import { expect, test } from '@playwright/test';
import {
  buildMessages,
  buildPendingDownloadMessage,
  setupApiMocks,
} from './fixtures.js';

test('Dashboard — needs_download-rad visar nedladdningsknapp', async ({ page }) => {
  const messages = [buildPendingDownloadMessage({ id: 99 }), ...buildMessages()];
  await setupApiMocks(page, { messages });

  await page.goto('/');
  await expect(page.getByTestId('row-download-99')).toBeVisible();
});

test('Drawer Drive-tab — öppna pending-rad visar download-banner + länk', async ({
  page,
}) => {
  const messages = [buildPendingDownloadMessage({ id: 99 })];
  await setupApiMocks(page, { messages });

  await page.goto('/');
  // Klicka på nedladdningsikonen i raden → drawer öppnas på Drive-fliken
  await page.getByTestId('row-download-99').click();

  await expect(page.getByTestId('drawer')).toBeVisible();
  await expect(page.getByTestId('drawer-tab-drive')).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByTestId('download-banner')).toBeVisible();
  await expect(page.getByTestId('pending-link')).toHaveAttribute(
    'href',
    'https://arlandaexpress.se/receipt/abc-token-xyz',
  );
  await expect(page.getByTestId('fetch-pdf-btn')).toBeVisible();
});

test('Drawer Drive-tab — lyckad fetch-pdf visar toast + uppdaterar raden', async ({
  page,
}) => {
  const messages = [buildPendingDownloadMessage({ id: 99 })];
  await setupApiMocks(page, { messages });

  await page.goto('/');
  // Download-ikonen öppnar drawer direkt på Drive-fliken
  await page.getByTestId('row-download-99').click();
  await expect(page.getByTestId('drawer-tab-drive-content')).toBeVisible();

  await page.getByTestId('fetch-pdf-btn').click();

  // Success-toast syns
  await expect(
    page.getByText('PDF hämtad och sparad till Drive'),
  ).toBeVisible();

  // Stäng drawer och verifiera att raden inte längre visar download-ikonen
  // (status har blivit 'saved' efter bumpMessagesVersion → refetch).
  await page.keyboard.press('Escape');
  await expect(page.getByTestId('drawer')).toHaveCount(0);
  await expect(page.getByTestId('row-download-99')).toHaveCount(0);
});

test('Drawer Drive-tab — fetch-pdf fel visar error-toast + behåller banner', async ({
  page,
}) => {
  const messages = [buildPendingDownloadMessage({ id: 99 })];
  await setupApiMocks(page, {
    messages,
    fetchPdfResponse: {
      status: 502,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: 'Länken gav text/html istället för PDF',
      }),
    },
  });

  await page.goto('/');
  await page.getByTestId('row-download-99').click();
  await page.getByTestId('fetch-pdf-btn').click();

  await expect(page.getByText(/Kunde inte hämta PDF/)).toBeVisible();
  // Bannern är kvar — raden är orörd
  await expect(page.getByTestId('download-banner')).toBeVisible();
});

test('Settings — BuiltinSendersBlock visar read-only pills', async ({ page }) => {
  await setupApiMocks(page);
  await page.goto('/settings');
  await expect(page.getByTestId('builtin-senders')).toBeVisible();
  // Minst en av default-adresserna ska synas
  await expect(
    page.getByTestId('builtin-senders').getByText('eticket@amadeus.com'),
  ).toBeVisible();
  // Ingen × på en builtin-chip — pills är read-only
  await expect(
    page.getByTestId('builtin-senders').locator('.chip__remove'),
  ).toHaveCount(0);
});

test('Settings — LinkFetchSection + min-confidence-slider fungerar', async ({
  page,
}) => {
  await setupApiMocks(page);
  await page.goto('/settings');

  // link_fetch_senders default (noreply@arlandaexpress.se) visas
  await expect(page.getByTestId('link-fetch')).toBeVisible();
  await expect(
    page
      .getByTestId('link-fetch')
      .getByText('noreply@arlandaexpress.se', { exact: true }),
  ).toBeVisible();

  // Lägg till en ny avsändare
  const input = page.getByTestId('link-fetch-senders-input');
  await input.fill('noreply@newsupplier.com');
  await input.press('Enter');
  await expect(
    page.getByText('noreply@newsupplier.com', { exact: true }),
  ).toBeVisible();

  // Ta bort default-avsändaren
  await page
    .getByTestId('link-fetch-senders-remove-noreply@arlandaexpress.se')
    .click();
  await expect(
    page
      .getByTestId('link-fetch')
      .getByText('noreply@arlandaexpress.se', { exact: true }),
  ).toHaveCount(0);

  // min-confidence-slider default = 40
  await expect(page.getByTestId('min-confidence-value')).toHaveText('40%');
  const slider = page.getByTestId('min-confidence-slider').locator('input[type="range"]');
  await slider.fill('65');
  await expect(page.getByTestId('min-confidence-value')).toHaveText('65%');
});
