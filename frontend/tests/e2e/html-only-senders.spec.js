import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* HTML-only senders — Settings-sektion: lista, lägg till, toggle, radera. */

const INITIAL_SENDERS = [
  {
    id: 1,
    sender_pattern: 'skanetrafiken',
    description: 'Tågbiljetter Skåne',
    is_active: true,
    created_at: '2026-05-01T00:00:00',
  },
  {
    id: 2,
    sender_pattern: 'noreply@moovy.fi',
    description: 'Moovy notifieringar',
    is_active: false,
    created_at: '2026-05-01T00:00:00',
  },
];

async function setupHtmlOnlyMocks(page) {
  await setupApiMocks(page);

  let state = JSON.parse(JSON.stringify(INITIAL_SENDERS));
  let nextId = 100;

  await page.route('**/api/settings/html-only-senders', async (route) => {
    const req = route.request();
    if (req.method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ senders: state }),
      });
    }
    if (req.method() === 'POST') {
      const body = req.postDataJSON() || {};
      const row = {
        id: nextId++,
        sender_pattern: (body.sender_pattern || '').toLowerCase(),
        description: body.description || null,
        is_active: true,
        created_at: new Date().toISOString(),
      };
      state.push(row);
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...row, already_exists: false }),
      });
    }
    return route.continue();
  });

  await page.route(
    '**/api/settings/html-only-senders/*',
    async (route) => {
      const req = route.request();
      const id = Number(req.url().split('/').pop());
      if (req.method() === 'DELETE') {
        const before = state.length;
        state = state.filter((s) => s.id !== id);
        return route.fulfill({
          status: before === state.length ? 404 : 200,
          contentType: 'application/json',
          body: JSON.stringify({ success: before !== state.length }),
        });
      }
      if (req.method() === 'PATCH') {
        const body = req.postDataJSON() || {};
        const idx = state.findIndex((s) => s.id === id);
        if (idx === -1) {
          return route.fulfill({
            status: 404, contentType: 'application/json', body: '{}',
          });
        }
        state[idx].is_active = !!body.is_active;
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(state[idx]),
        });
      }
      return route.continue();
    },
  );
}

test('Settings — HTML-only-sektionen syns med lista', async ({ page }) => {
  await setupHtmlOnlyMocks(page);
  await page.goto('/settings');
  await expect(
    page.getByTestId('html-only-senders-section'),
  ).toBeVisible();
  await expect(page.getByTestId('html-only-sender-1')).toBeVisible();
  await expect(page.getByTestId('html-only-sender-2')).toBeVisible();
});

test('Settings — Lägg till sender → POST kallas + listan uppdateras',
  async ({ page }) => {
    await setupHtmlOnlyMocks(page);
    await page.goto('/settings');
    await page.getByTestId('html-only-senders-add').click();
    await page.getByTestId('html-only-senders-pattern-input').fill('cursor');
    await page.getByTestId('html-only-senders-description-input')
      .fill('Cursor AI');
    const reqPromise = page.waitForRequest(
      (req) =>
        req.url().includes('/api/settings/html-only-senders') &&
        req.method() === 'POST',
    );
    await page.getByTestId('html-only-senders-confirm').click();
    await reqPromise;
    // Den nya raden ska synas (id 100 från mocken)
    await expect(page.getByTestId('html-only-sender-100')).toBeVisible();
  });

test('Settings — Toggle aktiv → PATCH kallas', async ({ page }) => {
  await setupHtmlOnlyMocks(page);
  await page.goto('/settings');
  // Sender 2 är inaktiv från start → klick aktiverar
  await expect(
    page.getByTestId('html-only-sender-2'),
  ).toHaveAttribute('data-active', 'false');
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().endsWith('/api/settings/html-only-senders/2') &&
      req.method() === 'PATCH',
  );
  await page.getByTestId('html-only-sender-toggle-2').check();
  await reqPromise;
});

test('Settings — Radera sender → DELETE kallas + raden försvinner',
  async ({ page }) => {
    await setupHtmlOnlyMocks(page);
    // Confirm() måste accepteras automatiskt
    page.on('dialog', (dialog) => dialog.accept());
    await page.goto('/settings');
    const reqPromise = page.waitForRequest(
      (req) =>
        req.url().endsWith('/api/settings/html-only-senders/1') &&
        req.method() === 'DELETE',
    );
    await page.getByTestId('html-only-sender-remove-1').click();
    await reqPromise;
    await expect(page.getByTestId('html-only-sender-1')).toHaveCount(0);
  });

test('Settings — Empty-state visas när inga senders finns',
  async ({ page }) => {
    await setupApiMocks(page);
    await page.route(
      '**/api/settings/html-only-senders',
      (route) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ senders: [] }),
      }),
    );
    await page.goto('/settings');
    await expect(
      page.getByTestId('html-only-senders-empty'),
    ).toBeVisible();
    // Add-knappen ska fortfarande synas så användaren kan lägga till
    await expect(
      page.getByTestId('html-only-senders-add'),
    ).toBeVisible();
  });
