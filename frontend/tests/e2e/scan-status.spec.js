import { expect, test } from '@playwright/test';
import { buildMessages, buildRuns, buildStats, setupApiMocks } from './fixtures.js';

/* Gate 4: scan-status i UI.
 *
 * TopBar/Dashboard scan-knapp ska under pågående scanning:
 *  - visa "Scannar…"-text istället för "Kör scanning nu"
 *  - vara disabled (aria-busy=true)
 *  - ha en pulsande dot + roterande spinner
 *
 * Toast vid klart:
 *  - X > 0: "Scanning klar — X nya kvitton hittade"
 *  - X = 0: "Inga nya kvitton matchade filtren"
 *  - Timeout 45s: "Scanning tar längre tid än väntat — se Loggen"
 */

test('Gate 4 — scan-knappen visar "Scannar…" + spinner medan scanning pågår', async ({
  page,
}) => {
  // Håll /api/runs hängande så scan-polling aldrig resolvar innan
  // vi har hunnit läsa knappens scanning-state.
  let resolveRuns;
  await setupApiMocks(page);
  await page.route('**/api/runs**', (route) => {
    // Aldrig fulfilla → polling timeoutar efter 45s, men vi behöver
    // bara se knappen medan scanning pågår.
    new Promise((r) => {
      resolveRuns = r;
    }).then(() => route.fulfill({ status: 200, body: '[]' }));
  });

  await page.goto('/');
  const btn = page.getByTestId('scan-button');

  await btn.click();

  // Knappen ska nu vara disabled + visa "Scannar…"
  await expect(btn).toBeDisabled();
  await expect(btn).toHaveAttribute('aria-busy', 'true');
  await expect(btn).toContainText(/Scannar/i);

  // Pulsande dot + spinner ska renderas
  await expect(btn.locator('.scan-btn__dot')).toBeVisible();
  await expect(btn.locator('.scan-btn__spinner')).toBeVisible();
});

test('Gate 4 — toast "Scanning klar — 3 nya kvitton" vid lyckad scan', async ({
  page,
}) => {
  const runs = buildRuns();
  // Senaste körning — 3 nya mail, finished_at just nu
  runs[0] = {
    ...runs[0],
    // Långt in i framtiden + långt i det förflutna så vi aldrig
    // race-krockar med scan-start-tiden i parallella workers.
    started_at: new Date(Date.now() - 60_000).toISOString(),
    finished_at: new Date(Date.now() + 60_000).toISOString(),
    messages_found: 3,
    messages_processed: 3,
    errors: 0,
  };

  await setupApiMocks(page, { runs });
  await page.goto('/');

  await page.getByTestId('scan-button').click();

  await expect(
    page.getByText(/3 nya kvitton/i),
  ).toBeVisible({ timeout: 15_000 });
});

test('Gate 4 — toast "Inga nya kvitton" när messages_found=0', async ({
  page,
}) => {
  const runs = buildRuns();
  runs[0] = {
    ...runs[0],
    // Långt in i framtiden + långt i det förflutna så vi aldrig
    // race-krockar med scan-start-tiden i parallella workers.
    started_at: new Date(Date.now() - 60_000).toISOString(),
    finished_at: new Date(Date.now() + 60_000).toISOString(),
    messages_found: 0,
    messages_processed: 0,
    errors: 0,
  };

  await setupApiMocks(page, { runs });
  await page.goto('/');

  await page.getByTestId('scan-button').click();

  await expect(
    page.getByText(/Inga nya kvitton matchade filtren/i),
  ).toBeVisible({ timeout: 15_000 });
});

test('Gate 4 — scan-knappen disabled hindrar dubbelklick', async ({ page }) => {
  // Mocka /api/scan så vi kan räkna antal anrop
  let scanCount = 0;

  await setupApiMocks(page);
  await page.route('**/api/scan', (route) => {
    scanCount += 1;
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'started', max_results: 50 }),
    });
  });

  await page.goto('/');
  const btn = page.getByTestId('scan-button');
  await btn.click();
  // Snabb-klicka igen — ska ignoreras eftersom disabled
  await btn.click({ force: true }).catch(() => {});

  // Vänta lite så event-loopen hinner processa
  await page.waitForTimeout(200);
  expect(scanCount).toBe(1);
});
