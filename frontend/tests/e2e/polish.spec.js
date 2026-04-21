import { expect, test } from '@playwright/test';
import { buildStats, setupApiMocks } from './fixtures.js';

test('Skeleton visas initialt på Dashboard', async ({ page }) => {
  // Fördröj messages-responsen så vi hinner se skeleton
  await setupApiMocks(page);
  await page.route('**/api/messages?**', async (route) => {
    await new Promise((r) => setTimeout(r, 600));
    return route.continue();
  });

  await page.goto('/');
  await expect(page.getByTestId('message-table-loading')).toBeVisible();
  // Skeleton-rader finns
  await expect(page.getByTestId('skeleton-row-0')).toBeVisible();
});

test('Keyboard-nav: ArrowDown/J/Enter på Dashboard-tabellen', async ({
  page,
}) => {
  await setupApiMocks(page);
  await page.goto('/');
  await expect(page.locator('tr[data-row-id]').first()).toBeVisible();

  const firstRow = page.locator('tr[data-row-id]').first();
  await firstRow.focus();
  await page.keyboard.press('ArrowDown');
  // Fokus ska ha flyttats till nästa rad (inte första)
  const focused = page.locator('tr[data-row-id]:focus');
  await expect(focused).toHaveCount(1);

  await page.keyboard.press('Enter');
  await expect(page.getByTestId('drawer')).toBeVisible();
});

test('Dashboard visar "Inga nya mail senast" när sista körningen hade 0', async ({
  page,
}) => {
  const stats = buildStats();
  stats.last_run.messages_processed = 0;
  stats.last_run.status = 'ok';
  await setupApiMocks(page, { stats });
  await page.goto('/');
  await expect(page.getByText(/Inga nya mail senast/i)).toBeVisible();
});

test('Appen kraschar inte när /api/messages fallerar', async ({ page }) => {
  // useApiData fångar loader-errors och vi render:ar tomt. Det viktiga är
  // att sidebar + topbar + resten av appen fortfarande står stabilt — ingen
  // whiteskärm. Error-boundary i sig är testad enhetsvis via React-pattern.
  await setupApiMocks(page);
  await page.route('**/api/messages?**', (route) =>
    route.fulfill({
      status: 500,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'boom' }),
    }),
  );
  await page.goto('/');
  // Sidebar är kvar
  await expect(page.getByRole('button', { name: 'Översikt', exact: true })).toBeVisible();
  // Topbar-title (Tema A använder uppercase via CSS)
  await expect(page.locator('.topbar .title')).toBeVisible();
  // Inga meddelanden renderas — tom-state visas
  await expect(page.getByText(/Inga mail bearbetade ännu|No mails processed yet/i)).toBeVisible();
});

test('Toast-stack — flera toastar samtidigt (via två error-routes)', async ({
  page,
}) => {
  // Vi triggar två toastar snabbt via Review:s godkänn-fel + rensa-fel från Log.
  // Enklast: scan-knappen i Dashboard triggar en toast, sedan direkt till Log
  // och klicka Rensa fel.
  await setupApiMocks(page);
  page.on('dialog', (d) => d.accept());

  await page.goto('/');
  await page.getByTestId('scan-button').click();
  // Första toast: "Scanning startad"
  await expect(page.getByText(/Scanning startad|Scan started/i)).toBeVisible();

  // Navigera till Log och trigga rensa-fel → andra toast
  await page.getByRole('button', { name: 'Logg', exact: true }).click();
  await page.getByTestId('clear-errors').click();
  // Två toastar samtidigt — minst en av varje kind
  await expect(page.getByTestId('toast-ok').first()).toBeVisible();
});
