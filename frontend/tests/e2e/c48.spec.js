import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* C48 — +kvitto-adressen (Inställningar + /m) och varningar på svepkort. */

const INBOX = {
  address: 'mikko.keinonen+kvitto@example.com',
  also_accepted: ['mikko.keinonen+kvitto@example.com', 'mikkokeinonen+kvitto@example.com'],
  allowed_senders: ['mikko.keinonen@example.com', 'mikko.private@gmail.com'],
  recent: [
    { at: '2026-11-20T08:00:00', sender: 'mikko.private@gmail.com', attachments: 2,
      outcome: 'accepted', reason: null },
    { at: '2026-11-19T08:00:00', sender: 'x@evil.com', attachments: null,
      outcome: 'rejected', reason: "sender is not one of Mikko's own addresses" },
  ],
};

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page);
  await page.route('**/api/receipt-inbox', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(INBOX) }));
});

test('C48 — Inställningar visar +kvitto-adressen med kopieringsknapp', async ({ page }) => {
  await page.goto('/settings');
  const block = page.getByTestId('receipt-inbox');
  await expect(block).toBeVisible();
  await expect(page.getByTestId('receipt-inbox-address')).toHaveText(INBOX.address);
  await expect(page.getByTestId('receipt-inbox-copy')).toBeVisible();
  await expect(block).toContainText('mikkokeinonen+kvitto@example.com');
  await expect(block).toContainText('mikko.private@gmail.com');
  await expect(page.getByTestId('receipt-inbox-recent').locator('li')).toHaveCount(2);
});

test('C48 — /m visar "Mail a receipt to" i sidfoten', async ({ page }) => {
  await page.goto('/m');
  await expect(page.getByTestId('m-inbox-footer')).toContainText(
    `Mail a receipt to: ${INBOX.address}`);
});

test('C48 — svepkort visar varning och spärrar ja utan konto', async ({ page }) => {
  await page.route('**/api/queue', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify({
      count: 1, total: 1, counts: { sub: 1 }, last_decision: null,
      items: [{
        id: 'sub:1', kind: 'sub', title: 'Subscription: Cursor?',
        subtitle: 'about 22.03 EUR / month - Account missing - set it first',
        yes_label: 'Yes, auto-handle it', no_label: 'Not a subscription',
        warning: 'Account missing - set it first', blocked: true,
        subscription: { id: 1, name: 'Cursor', recent: [], flags: ['account_missing'] },
      }],
    }),
  }));
  await page.goto('/m/swipe');
  await expect(page.getByTestId('queue-card-warning')).toContainText('Account missing - set it first');
  await expect(page.getByRole('button', { name: /Ja, sköt automatiskt/ })).toBeDisabled();
});
