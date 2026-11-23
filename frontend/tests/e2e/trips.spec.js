import { expect, test } from '@playwright/test';
import {
  buildActiveTrip,
  buildTripSuggestion,
  setupApiMocks,
} from './fixtures.js';

let apiState;

test.beforeEach(async ({ page }) => {
  apiState = await setupApiMocks(page);
});

test('Sidebar har en Resor-flik och navigerar till /trips', async ({ page }) => {
  await page.goto('/');
  const link = page.getByTestId('nav-trips');
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/trips$/);
  await expect(page.getByTestId('trips-view')).toBeVisible();
});

test('Resor-vyn visar förslag och aktiva resor', async ({ page }) => {
  await page.goto('/trips');
  await expect(page.getByTestId('trips-view')).toBeVisible();
  const row = page.getByTestId('trip-suggestion-1');
  await expect(row).toBeVisible();
  await expect(row).toContainText('Stockholm');
  await expect(row).toContainText('30 apr – 2 maj 2026');
  await page.getByTestId('trips-tab-approved').click();
  await expect(page.getByTestId('trip-active-2')).toBeVisible();
});

test('Acceptera-knappen flyttar förslag till aktiva resor', async ({ page }) => {
  await page.goto('/trips');
  const accept = page.getByTestId('trip-accept-1');
  await accept.click();
  // Suggestion försvinner, ny aktiv resa dyker upp under Godkända
  await expect(page.getByTestId('trip-suggestion-1')).toHaveCount(0);
  await page.getByTestId('trips-tab-approved').click();
  await expect(page.getByTestId('trip-active-1')).toBeVisible();
});

test('Justera-knappen öppnar edit-modal med förfyllda värden', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-suggestion-toggle-1').click();
  await page.getByTestId('trip-edit-1').click();
  await expect(page.getByTestId('trip-edit-modal-1')).toBeVisible();
  const titleInput = page.getByTestId('trip-edit-title');
  await expect(titleInput).toHaveValue('Stockholm 30 apr - 2 maj 2026');
  await titleInput.fill('Min Stockholm-resa');
  await page.getByTestId('trip-edit-save').click();
  await expect(page.getByTestId('trip-edit-modal-1')).toHaveCount(0);
});

test('Visa-knappen öppnar drawer med kvitton', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-suggestion-toggle-1').click();
  await page.getByTestId('trip-show-1').click();
  await expect(page.getByTestId('trip-drawer-1')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-receipt-gmail-msg-1')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-receipt-gmail-msg-2')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-receipt-gmail-msg-3')).toBeVisible();
});

test('Drawer: ta bort kvitto från resa', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-suggestion-toggle-1').click();
  await page.getByTestId('trip-show-1').click();
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().match(/\/api\/trips\/1$/) && req.method() === 'PATCH',
  );
  await page.getByTestId('trip-drawer-remove-gmail-msg-3').click();
  const req = await reqPromise;
  const body = req.postDataJSON();
  expect(body.remove_message_ids).toContain('gmail-msg-3');
});

test('Drawer: bad-feedback öppnar modal och POST:ar feedback', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trip-suggestion-toggle-1').click();
  await page.getByTestId('trip-show-1').click();
  await page.getByTestId('trip-drawer-bad-feedback').click();
  await expect(page.getByTestId('trip-feedback-modal-1')).toBeVisible();
  await page.getByTestId('trip-feedback-option-wrong_dates').check();
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/trips/1/feedback') && req.method() === 'POST',
  );
  await page.getByTestId('trip-feedback-submit').click();
  const req = await reqPromise;
  expect(req.postDataJSON().feedback_type).toBe('wrong_dates');
});

test('Refresh-knappen anropar refresh-suggestions', async ({ page }) => {
  await page.goto('/trips');
  const reqPromise = page.waitForRequest(
    (req) =>
      req.url().includes('/api/trips/refresh-suggestions') &&
      req.method() === 'POST',
  );
  await page.getByTestId('trips-refresh').click();
  await reqPromise;
});

test('Tom väntelista visar empty-state', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    tripSuggestions: [],
    activeTrips: [buildActiveTrip()],
  });
  await page.goto('/trips');
  await expect(page.getByTestId('trips-waiting-empty')).toBeVisible();
});

test('Tom active-lista visar empty-state', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    tripSuggestions: [buildTripSuggestion()],
    activeTrips: [],
  });
  await page.goto('/trips');
  await page.getByTestId('trips-tab-approved').click();
  await expect(page.getByTestId('trips-active-empty')).toBeVisible();
});

test('Trip-drawer skiljer AI-förslag från manuellt tillagda kvitton', async ({ page }) => {
  apiState = await setupApiMocks(page, {
    tripSuggestions: [],
    activeTrips: [
      buildActiveTrip({
        id: 5,
        title: 'Stockholm 30 apr',
        messages: [
          {
            id: 100,
            message_id: 'ai-1',
            vendor: 'Finnair',
            amount: 200,
            currency: 'EUR',
            receipt_date: '2026-04-30',
            received_at: '2026-04-29T08:00:00Z',
            category: 'Flyg',
            subject: 'Boarding',
            summary: 'Flyg',
            added_by: 'ai_suggestion',
          },
          {
            id: 101,
            message_id: 'manual-1',
            vendor: 'Restaurant',
            amount: 32,
            currency: 'EUR',
            receipt_date: '2026-05-01',
            received_at: '2026-05-01T20:00:00Z',
            category: 'Mat',
            subject: 'Lunch',
            summary: 'Lunch',
            added_by: 'manual',
          },
        ],
      }),
    ],
  });
  await page.goto('/trips');
  await page.getByTestId('trips-tab-approved').click();
  await page.getByTestId('trip-active-5').click();
  await expect(page.getByTestId('trip-drawer-ai-label')).toBeVisible();
  await expect(page.getByTestId('trip-drawer-manual-label')).toBeVisible();
  await expect(
    page.getByTestId('trip-drawer-receipt-ai-1'),
  ).toHaveAttribute('data-added-by', 'ai_suggestion');
  await expect(
    page.getByTestId('trip-drawer-receipt-manual-1'),
  ).toHaveAttribute('data-added-by', 'manual');
});

// ---------- Resor R1 — kalenderresor ----------

const CAL_TRIP = {
  id: 7,
  start_date: '2026-04-14',
  end_date: '2026-04-15',
  destination: 'Stockholm',
  open_return: false,
  status: 'suggested',
  flights: [],
  hotels: [{ id: 'h1', name: 'Scandic Klara' }],
  source_event_ids: ['f1', 'h1', 'm1', 'f2'],
  days: [
    {
      date: '2026-04-14', start_time: '16:00', end_time: null,
      destination: 'Stockholm', meetings: [],
      hotel: { id: 'h1', name: 'Scandic Klara' }, source_event_ids: ['f1', 'h1'],
    },
    {
      date: '2026-04-15', start_time: null, end_time: '20:05',
      destination: 'Stockholm',
      meetings: [{ id: 'm1', summary: 'Workshop Visma', start_time: '09:00', end_time: '15:00', location: '' }],
      hotel: null, source_event_ids: ['m1', 'f2'],
    },
  ],
  receipts: [
    { message_id: 'r1', vendor: 'Finnair', amount: 250, currency: 'EUR', receipt_date: '2026-04-10', trip_id: 1 },
  ],
};

test('Kalenderresor visas som kort med tider, hotell, möten och kvitton', async ({ page }) => {
  await setupApiMocks(page, { calendarTrips: [CAL_TRIP] });
  await page.goto('/trips');
  const row = page.getByTestId('trip-row-7');
  await expect(row).toContainText('Stockholm');
  await expect(row).toContainText('250,00 EUR');
  // Detaljer först vid klick
  await expect(page.getByTestId('calendar-trip-7')).toHaveCount(0);
  await page.getByTestId('trip-row-toggle-7').click();
  const card = page.getByTestId('calendar-trip-7');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Avresa hemifrån 16:00');
  await expect(card).toContainText('Hemma 20:05');
  await expect(card).toContainText('Scandic Klara');
  await expect(card).toContainText('Workshop Visma');
  await expect(card).toContainText('Finnair');
});

test('C40: kalenderkortet visar status, flaggor, land och syfte från chatten', async ({ page }) => {
  const edited = {
    ...CAL_TRIP,
    id: 8,
    status: 'edited',
    country: 'SE',
    purpose: 'Customer workshop',
    flags: ['needs_review'],
  };
  await setupApiMocks(page, { calendarTrips: [edited] });
  await page.goto('/trips');
  await expect(page.getByTestId('trip-row-status-8')).toHaveText('Redigerad');
  await page.getByTestId('trip-row-toggle-8').click();
  const card = page.getByTestId('calendar-trip-8');
  await expect(page.getByTestId('calendar-trip-review-8')).toHaveText('Välj flyg');
  // C41: landskoden visas som namn (SE → Sverige)
  await expect(card).toContainText('Sverige');
  await expect(page.getByTestId('calendar-trip-purpose-8')).toContainText('Customer workshop');
});

test('Läs kalendern utan consent visar Återanslut Gmail', async ({ page }) => {
  await setupApiMocks(page, {
    calendarRefreshResponse: { status: 'consent_required', detail: 'x' },
  });
  await page.goto('/trips');
  await page.getByTestId('trips-calendar-refresh').click();
  const notice = page.getByTestId('trips-calendar-notice');
  await expect(notice).toBeVisible();
  await expect(notice.getByRole('link', { name: 'Återanslut Gmail' })).toHaveAttribute(
    'href', '/api/auth/gmail/start',
  );
});

test('Läs kalendern visar nya resor och ogrupperade event', async ({ page }) => {
  await setupApiMocks(page, {
    calendarRefreshResponse: {
      status: 'ok',
      stats: { trips: 1 },
      trips: [CAL_TRIP],
      ungrouped: [{ id: 'h9', summary: 'Hotel Kämp', date: '2026-06-03', reason: 'hotel_without_flight' }],
    },
  });
  await page.goto('/trips');
  await page.getByTestId('trips-calendar-refresh').click();
  await expect(page.getByTestId('trip-row-7')).toBeVisible();
  await expect(page.getByTestId('trips-calendar-ungrouped')).toContainText('Kunde inte grupperas (1)');
});

// ---------- C56 — kort och tydlig ----------

const C56_HIDDEN = [
  {
    id: 41, title: 'Oslo 21 oktober 2026', destination: null,
    start_date: '2026-10-21', end_date: '2026-10-22', total_amount: 668,
    currency: 'EUR', receipt_count: 2, messages: [], visible: false, old: false,
    covered_by: { trip_id: 140, destination: 'Oslo', status: 'suggested' },
    duplicates: [], receipt_warnings: [],
  },
  {
    id: 42, title: 'Köpenhamn & Malmö 20-21 Mar 2025', destination: null,
    start_date: '2025-03-20', end_date: '2025-03-21', total_amount: 300,
    currency: 'EUR', receipt_count: 1, messages: [], visible: false, old: true,
    covered_by: null, duplicates: [], receipt_warnings: [],
  },
];

test('C56: sammanfattning, flikar och dolda förslag', async ({ page }) => {
  await setupApiMocks(page, {
    calendarTrips: [
      { ...CAL_TRIP, id: 140, destination: 'Oslo', start_date: '2026-10-21', end_date: '2026-10-22' },
      { ...CAL_TRIP, id: 9, status: 'approved' },
    ],
    c56Hidden: C56_HIDDEN,
    emissionsPerTrip: [
      { trip_id: 140, km: 1580, legs_missing_km: 1, flags: [] },
      { trip_id: 9, km: 820.4, legs_missing_km: 0, flags: [] },
    ],
    netvisorDescriptions: [
      { trip_id: 140, netvisor_description: 'Travel for internal audit in Oslo' },
    ],
  });
  await page.goto('/trips');
  // 2 resor; väntar = resa 140 + AI-förslag 1; 1 saknar km
  await expect(page.getByTestId('trips-stats-summary')).toHaveText(
    '2 resor · 2 väntar på ditt beslut · 1 saknar km',
  );
  await expect(page.getByTestId('trips-tab-waiting')).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByTestId('trip-row-140')).toBeVisible();
  await expect(page.getByTestId('trip-row-9')).toHaveCount(0);
  await expect(page.getByTestId('trip-row-km-140')).toHaveText('km saknas');
  await expect(page.getByTestId('trip-row-nv-140')).toContainText('Travel for internal audit in Oslo');
  // Täckt förslag visas inte som förslag, bara i listan "redan täckta"
  await expect(page.getByTestId('trip-suggestion-41')).toHaveCount(0);
  await expect(page.getByTestId('trips-covered')).toContainText('1 förslag är redan täckta av resor');
  await page.getByTestId('trips-covered').locator('summary').click();
  await expect(page.getByTestId('trips-covered-41')).toContainText('Redan täckt av resa 140');
  await expect(page.getByTestId('trip-suggestion-42')).toHaveCount(0);
  await expect(page.getByTestId('trips-old-hidden')).toContainText('1 förslag före 2026 är dolda');
  // Godkända
  await page.getByTestId('trips-tab-approved').click();
  await expect(page.getByTestId('trip-row-9')).toBeVisible();
  await expect(page.getByTestId('trip-row-km-9')).toHaveText('820 km');
});

test('C56: Acceptera/Avvisa står på rubrikraden och Säkerhet på svenska', async ({ page }) => {
  await page.goto('/trips');
  const row = page.getByTestId('trip-suggestion-1');
  const head = row.locator('.trip-row__main');
  await expect(head.getByTestId('trip-accept-1')).toBeVisible();
  await expect(head.getByTestId('trip-reject-1')).toBeVisible();
  await expect(page.getByTestId('trip-confidence-1')).toHaveText('Säkerhet: 85%');
  // Detaljerna är hopfällda
  await expect(page.getByTestId('trip-edit-1')).toHaveCount(0);
});

test('C56: Godkänn på raden ändrar bara status (efter bekräftelse)', async ({ page }) => {
  const state = await setupApiMocks(page, {
    calendarTrips: [{ ...CAL_TRIP, status: 'edited' }],
    netvisorDescriptions: [{ trip_id: 7, netvisor_description: 'Travel for workshop in Stockholm' }],
  });
  await page.goto('/trips');
  let dialogText = '';
  page.once('dialog', (d) => { dialogText = d.message(); d.accept(); });
  await page.getByTestId('trip-approve-7').click();
  await expect(page.getByTestId('trip-row-7')).toHaveCount(0);
  expect(dialogText).toContain('Travel for workshop in Stockholm');
  expect(state.lastTripRequest).toEqual({ kind: 'approve', id: 7 });
  await page.getByTestId('trips-tab-approved').click();
  await expect(page.getByTestId('trip-row-status-7')).toHaveText('Godkänd');
});

test('C56: engelska — inga svenska etiketter', async ({ page }) => {
  await page.addInitScript(() => window.localStorage.setItem('bb_lang', 'en'));
  await setupApiMocks(page, {
    calendarTrips: [{ ...CAL_TRIP, status: 'edited', destination: 'Gothenburg' }],
    tripSuggestions: [buildTripSuggestion({ destination: 'Köpenhamn, Danmark' })],
  });
  await page.goto('/trips');
  await expect(page.getByTestId('trips-stats-summary')).toHaveText(
    '1 trips · 2 waiting for your decision · 0 missing km',
  );
  await expect(page.getByTestId('trip-row-7')).toContainText('Gothenburg');
  await expect(page.getByTestId('trip-row-7')).toContainText('14–15 Apr 2026');
  const sugg = page.getByTestId('trip-suggestion-1');
  await expect(sugg).toContainText('Copenhagen');
  await expect(page.getByTestId('trip-confidence-1')).toHaveText('Confidence: 85%');
  const text = await page.getByTestId('trips-view').innerText();
  for (const word of ['Acceptera', 'Avvisa', 'Justera', 'Säkerhet', 'AI-förslag', 'Hittat',
    'Checklista', 'Godkänn', 'Väntar', 'Redigerad', 'Köpenhamn']) {
    expect(text).not.toContain(word);
  }
});

test('C56: samma biljett räknas en gång och kvitton utan resa listas', async ({ page }) => {
  await setupApiMocks(page, {
    calendarTrips: [{
      ...CAL_TRIP,
      receipts: [
        { message_id: 'et', vendor: 'Finnair', amount: 446.77, currency: 'EUR', receipt_date: '2026-04-14', counted: true },
        { message_id: 'bc', vendor: 'Finnair', amount: 446.77, currency: 'EUR', receipt_date: '2026-04-14', counted: false, duplicate_of: 'et' },
        { message_id: 'f1', vendor: 'Flytoget', amount: 276, currency: 'NOK', receipt_date: '2026-04-14', counted: true },
        { message_id: 'f2', vendor: 'Flytoget', amount: 276, currency: 'NOK', receipt_date: '2026-04-14', counted: true },
      ],
    }],
    unassignedReceipts: [
      { message_id: 'ay-1126', vendor: 'Finnair', amount: 134.48, currency: 'EUR', receipt_date: '2026-11-26', reason: 'ambiguous', candidates: [146, 147] },
    ],
    missingInfoReceipts: [{ message_id: 'n1' }, { message_id: 'n2' }],
  });
  await page.goto('/trips');
  await expect(page.getByTestId('trip-row-7')).toContainText('446,77 EUR + 552,00 NOK');
  await page.getByTestId('trip-row-toggle-7').click();
  await expect(page.getByTestId('calendar-trip-receipt-bc')).toContainText('samma biljett, räknas en gång');
  const un = page.getByTestId('trips-unassigned');
  await expect(un).toContainText('Kvitton utan resa (1)');
  await expect(page.getByTestId('trips-unassigned-ay-1126')).toContainText('passar flera resor: 146, 147');
  await expect(page.getByTestId('trips-missing-info')).toContainText('2 kvitton saknar uppgifter');
});

test('C57: avbokad bokning hopfälld, räknas inte, och frågor till Mikko', async ({ page }) => {
  const booking = (ref, amount, date) => ({
    booking: `finnair:${ref}`, vendor: 'Finnair', booking_ref: ref, amount, currency: 'EUR',
    purchase_date: date, refund_date: date, counted: false,
    receipts: [{ message_id: `${ref}-et`, vendor: 'Finnair', amount, currency: 'EUR', receipt_date: date, counted: false, cancelled: true }],
  });
  await setupApiMocks(page, {
    calendarTrips: [{
      ...CAL_TRIP,
      receipts: [
        { message_id: 'ot', vendor: 'Finnair', amount: 514.48, currency: 'EUR', receipt_date: '2026-04-14', counted: true },
      ],
      cancelled_bookings: [booking('8HZCXX', 674.48, '2026-04-13')],
    }],
    cancelledWithoutTrip: [booking('8NLAI4', 134.48, '2026-11-18')],
    emissionsPerTrip: [{
      trip_id: 7, km: 0, legs_missing_km: 1, flags: ['km_missing', 'home_transport_unknown'],
      questions: [
        { kind: 'km_missing', leg_id: 66, text_sv: 'Taxi 2026-09-01: km står inte på kvittot och start och slut saknas. Hur många km? (Boten gissar inte.)', text_en: 'x' },
        { kind: 'home_transport', leg_id: null, text_sv: 'Hur tog du dig till och från HEL? Bil, taxi eller annat?', text_en: 'y' },
      ],
    }],
  });
  await page.goto('/trips');
  await expect(page.getByTestId('trip-row-7')).toContainText('514,48 EUR');
  await expect(page.getByTestId('trip-row-7')).not.toContainText('674,48');
  await expect(page.getByTestId('trip-row-ask-7')).toContainText('Fråga Mikko');
  await expect(page.getByTestId('trip-row-ask-7')).toContainText('Bil, taxi eller annat?');
  await expect(page.getByTestId('trip-row-ask-7')).toContainText('start och slut saknas');
  await page.getByTestId('trip-row-toggle-7').click();
  const row = page.getByTestId('cancelled-finnair:8HZCXX');
  await expect(row).toContainText('Avbokad och återbetald (räknas inte)');
  await expect(row).toContainText('bokning 8HZCXX');
  await expect(row).toContainText('674,48 EUR');
  await expect(page.getByTestId('trips-unassigned-8NLAI4-et')).toHaveCount(0);
  await expect(page.getByTestId('trips-cancelled-no-trip')).toContainText('Avbokade och återbetalda, utan resa (1)');
});
