import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

let apiState;

test.beforeEach(async ({ page }) => {
  apiState = await setupApiMocks(page);
});

async function openAiTabForRow(page, rowId = 1) {
  await page.goto('/');
  await expect(page.getByText('Finnair', { exact: false }).first()).toBeVisible();
  await page.locator(`tr[data-row-id="${rowId}"]`).click();
  await expect(page.getByTestId('drawer')).toBeVisible();
  await page.getByTestId('drawer-tab-ai').click();
  await expect(page.getByTestId('drawer-tab-ai-content')).toBeVisible();
  await expect(page.getByTestId('feedback-buttons')).toBeVisible();
}

test('Feedback — 👍 skickar thumbs + visar tack-state', async ({ page }) => {
  await openAiTabForRow(page, 1);

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/feedback/thumbs') && req.method() === 'POST',
  );
  await page.getByTestId('feedback-thumbs-up').click();
  const req = await reqPromise;
  const body = req.postDataJSON();
  expect(body.is_positive).toBe(true);
  expect(body.message_id).toBe('gmail-msg-1');
  expect(Array.isArray(body.fields)).toBe(true);

  await expect(page.getByTestId('feedback-submitted')).toBeVisible();
  await expect(page.getByTestId('feedback-thumbs-up')).toHaveCount(0);
  await expect(page.getByText(/Tack för feedbacken/i)).toBeVisible();
});

test('Feedback — 👎 öppnar modal', async ({ page }) => {
  await openAiTabForRow(page, 1);
  await page.getByTestId('feedback-thumbs-down').click();
  await expect(page.getByTestId('feedback-modal')).toBeVisible();
  await expect(page.getByTestId('feedback-field-vendor')).toBeVisible();
  await expect(page.getByTestId('feedback-field-amount')).toBeVisible();
  await expect(page.getByTestId('feedback-field-date')).toBeVisible();
  await expect(page.getByTestId('feedback-field-category')).toBeVisible();
});

test('Feedback — modal save skickar valda fält', async ({ page }) => {
  await openAiTabForRow(page, 1);
  await page.getByTestId('feedback-thumbs-down').click();
  await expect(page.getByTestId('feedback-modal')).toBeVisible();

  await page.getByTestId('feedback-field-vendor').check();
  await page.getByTestId('feedback-field-amount').check();

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/feedback/thumbs') && req.method() === 'POST',
  );
  await page.getByTestId('feedback-save').click();
  const req = await reqPromise;
  const body = req.postDataJSON();
  expect(body.is_positive).toBe(false);
  expect(body.message_id).toBe('gmail-msg-1');
  expect(body.fields).toEqual(expect.arrayContaining(['vendor', 'amount']));
  expect(body.fields).toHaveLength(2);

  await expect(page.getByText(/AI lär sig av detta/i)).toBeVisible();
  await expect(page.getByTestId('feedback-modal')).toHaveCount(0);
});

test('Granska — redigerade fält skickar /correction parallellt med upload', async ({
  page,
}) => {
  await page.goto('/review');
  await expect(page.getByTestId('review-form')).toBeVisible();

  // Vendor-input är etiketterad "Leverantör". Editera till nytt värde.
  const vendorInput = page.getByLabel('Leverantör').first();
  await vendorInput.fill('Finnair Cargo');

  const correctionPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/feedback/correction') &&
      req.method() === 'POST',
  );
  const uploadPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/upload-to-bezala') && req.method() === 'POST',
  );

  await page.getByTestId('approve-button').click();

  const [correctionReq, uploadReq] = await Promise.all([
    correctionPromise,
    uploadPromise,
  ]);
  const cBody = correctionReq.postDataJSON();
  expect(cBody.field_name).toBe('vendor');
  expect(cBody.correct_value).toBe('Finnair Cargo');
  expect(cBody.ai_value).toBe('Finnair');
  expect(cBody.message_id).toBeTruthy();
  expect(uploadReq.method()).toBe('POST');

  await expect(page.getByText(/Skickat till Bezala/i)).toBeVisible();
  await expect(page.getByText(/AI lär sig av detta/i)).toBeVisible();
  expect(apiState.lastFeedbackRequest?.kind).toBe('correction');
});

// ---------- FAS 8.1 — not_a_receipt-feedback ----------

test('Feedback 8.1 — 👎 modal har radio-knappar', async ({ page }) => {
  await openAiTabForRow(page, 1);
  await page.getByTestId('feedback-thumbs-down').click();
  await expect(page.getByTestId('feedback-modal')).toBeVisible();

  await expect(page.getByTestId('feedback-kind-field-error')).toBeVisible();
  await expect(page.getByTestId('feedback-kind-not-receipt')).toBeVisible();
  // Default: field_error är vald — fields-listan synlig
  await expect(page.getByTestId('feedback-fields-list')).toBeVisible();
  await expect(page.getByTestId('feedback-not-receipt-info')).toHaveCount(0);
});

test('Feedback 8.1 — välj "Inte ett kvitto" → fields-checkboxar döljs + info-banner visas', async ({ page }) => {
  await openAiTabForRow(page, 1);
  await page.getByTestId('feedback-thumbs-down').click();
  await expect(page.getByTestId('feedback-modal')).toBeVisible();

  await page.getByTestId('feedback-kind-not-receipt').check();

  await expect(page.getByTestId('feedback-fields-list')).toHaveCount(0);
  await expect(page.getByTestId('feedback-not-receipt-info')).toBeVisible();
  // Save-knappens text byts till "Markera som icke-kvitto"
  await expect(page.getByTestId('feedback-save')).toContainText(/Markera som icke-kvitto/i);
});

test('Feedback 8.1 — save skickar POST /not-a-receipt', async ({ page }) => {
  await openAiTabForRow(page, 1);
  await page.getByTestId('feedback-thumbs-down').click();
  await page.getByTestId('feedback-kind-not-receipt').check();

  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/feedback/not-a-receipt') &&
      req.method() === 'POST',
  );
  await page.getByTestId('feedback-save').click();
  const req = await reqPromise;
  const body = req.postDataJSON();
  expect(body.message_id).toBe('gmail-msg-1');

  // Toast med inlärnings-text
  await expect(page.getByText(/AI lär sig att filtrera liknande/i)).toBeVisible();
  expect(apiState.lastFeedbackRequest?.kind).toBe('not_a_receipt');
});

test('Feedback 8.1 — efter save: drawer stängs + raden försvinner ur listan', async ({ page }) => {
  await openAiTabForRow(page, 1);
  await page.getByTestId('feedback-thumbs-down').click();
  await page.getByTestId('feedback-kind-not-receipt').check();

  await page.getByTestId('feedback-save').click();

  // Drawer stängs
  await expect(page.getByTestId('drawer')).toHaveCount(0, { timeout: 5000 });
  // Modal stängs också
  await expect(page.getByTestId('feedback-modal')).toHaveCount(0);
  // Raden försvinner ur listan (mock soft-deletar id=1 → nästa /api/messages
  // returnerar utan den)
  await expect(
    page.locator('tr[data-row-id="1"]'),
  ).toHaveCount(0, { timeout: 5000 });
});
