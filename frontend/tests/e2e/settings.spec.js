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
  // Target confidence-slider specifically — efter min-confidence-tillägget
  // finns två range-inputs i dokumentet.
  const slider = page.getByTestId('confidence-slider').locator('input[type="range"]');
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

test('Settings — html_to_pdf-toggle är på som default och persisterar via PUT', async ({
  page,
}) => {
  await page.goto('/settings');
  const toggle = page.getByTestId('toggle-html_to_pdf_enabled');
  await expect(toggle).toBeVisible();
  await expect(toggle).toBeChecked();

  // Stäng av togglen
  await toggle.uncheck();

  const putRequest = page.waitForRequest(
    (req) => req.url().includes('/api/settings') && req.method() === 'PUT',
  );
  await page.getByTestId('save-settings').click();
  const req = await putRequest;
  const body = req.postDataJSON();
  expect(body.html_to_pdf_enabled).toBe(false);
});

test('Settings — html_to_pdf-toggle visar svensk hjälptext om Moovy/Skånetrafiken', async ({
  page,
}) => {
  await page.goto('/settings');
  await expect(
    page.getByText(/Moovy.*Skånetrafiken/i, { exact: false }),
  ).toBeVisible();
});

test('Settings — SaveBar nollställs efter lyckad save och aktiveras igen vid ny ändring', async ({
  page,
}) => {
  await page.goto('/settings');
  const saveButton = page.getByTestId('save-settings');
  const saveBar = page.getByTestId('save-bar');

  // Initialt: ingenting ändrat → Spara disabled, "Allt sparat" syns
  await expect(saveButton).toBeDisabled();
  await expect(saveBar).toContainText(/Allt sparat/i);

  // Ändra scan-intervall → dirty blir true
  await page.getByTestId('scan-interval').selectOption('30');
  await expect(saveButton).toBeEnabled();
  await expect(saveBar).toContainText(/Ändringar är inte sparade/i);

  // Spara → toast + dirty nollställs
  await saveButton.click();
  await expect(page.getByText(/Inställningar sparade/i)).toBeVisible();
  await expect(saveButton).toBeDisabled();
  await expect(saveBar).toContainText(/Allt sparat/i);

  // Ändra igen → dirty ska bli true på nytt
  await page.getByTestId('scan-interval').selectOption('15');
  await expect(saveButton).toBeEnabled();
  await expect(saveBar).toContainText(/Ändringar är inte sparade/i);
});

test('C46 — avsändare i privat Gmail: lista, lägg till, ta bort, fel visas', async ({ page }) => {
  let senders = [
    {
      address: 'no_reply@email.apple.com', kind: 'email', active: true, added_via: 'env',
      note: null, last_scan_at: '2026-11-21T10:00:00', fetched: 3, saved: 2, rejected: 1,
    },
  ];
  let lastPost = null;
  await page.route('**/api/private-gmail/senders**', async (route) => {
    const req = route.request();
    if (req.method() === 'POST') {
      lastPost = req.postDataJSON();
      if (lastPost.address.endsWith('@gmail.com')) {
        return route.fulfill({
          status: 400, contentType: 'application/json',
          body: JSON.stringify({ detail: 'gmail.com är en publik e-posttjänst och får inte läggas till' }),
        });
      }
      senders = [...senders, {
        address: lastPost.address, kind: 'domain', active: true, added_via: 'ui',
        note: lastPost.note, last_scan_at: null, fetched: 0, saved: 0, rejected: 0,
      }];
      return route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ status: 'added', backfill_started: true }),
      });
    }
    if (req.method() === 'DELETE') {
      const addr = new URL(req.url()).searchParams.get('address');
      senders = senders.map((s) => (s.address === addr ? { ...s, active: false } : s));
      return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    }
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        senders,
        last_scan: { at: '2026-11-21T10:00:00', found: 4, rejected_unknown_sender: 1 },
      }),
    });
  });
  await page.goto('/settings');
  const block = page.getByTestId('private-senders-block');
  await expect(block.getByTestId('private-sender-row')).toHaveCount(1);
  await expect(block.getByTestId('private-senders-last-scan')).toContainText('4');

  await block.getByTestId('private-sender-input').fill('@scandichotels.com');
  await block.getByTestId('private-sender-note').fill('Hotell');
  await block.getByTestId('private-sender-add').click();
  await expect(block.getByTestId('private-sender-row')).toHaveCount(2);
  await expect(block.getByTestId('private-sender-info')).toBeVisible();
  expect(lastPost).toEqual({ address: '@scandichotels.com', note: 'Hotell' });

  await block.getByTestId('private-sender-input').fill('x@gmail.com');
  await block.getByTestId('private-sender-add').click();
  await expect(block.getByTestId('private-sender-error')).toContainText('gmail.com');

  await block.getByTestId('private-sender-remove').first().click();
  await expect(block.getByTestId('private-sender-row')).toHaveCount(1);
});
