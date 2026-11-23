import { expect, test } from '@playwright/test';
import { setupApiMocks } from './fixtures.js';

/* C49 — Netvisor-flik per resa + 2026-översikt (bara läsning). */

const TRIP = {
  id: 79,
  start_date: '2026-05-07',
  end_date: '2026-05-08',
  destination: 'Båstad',
  open_return: false,
  status: 'approved',
  flights: [],
  hotels: [],
  source_event_ids: [],
  days: [],
  receipts: [],
};

const ENTRY = {
  trip_id: 79,
  trip_status: 'approved',
  netvisor_status: { status: 'not_created', netvisor_report_id: null, note: null },
  title: 'Business trip Båstad, Sweden 7.5.2026-8.5.2026',
  description: 'HRM SE kick-off, Båstad',
  rows: [1, 2].map((n) => ({
    row: n,
    by_air_or_sea: true,
    object: 'Sverige',
    country: 'SE',
    start_date: n === 1 ? '7.5.2026' : '8.5.2026',
    start_time: n === 1 ? '04:39' : '00:00',
    end_date: '8.5.2026',
    end_time: n === 1 ? '00:00' : '20:59',
    description: 'Business trip Båstad, Sweden - HRM SE kick-off',
    settings: {
      over_15_km: true, over_5_km: true, meals_included: n === 1,
      meals_included_days: n === 1 ? ['7.5.2026'] : [], night_travel: false,
      night_travel_check: null,
    },
  })),
  warnings: [],
  notes: ["Split into 2 calculator rows because 'Inklusive kost' applies to a whole calculator row"],
};

const OVERVIEW = {
  year: 2026, count: 2, ready_count: 1,
  trips: [
    { trip_id: 93, start_date: '2026-01-13', end_date: '2026-01-14', destination: 'Helsingborg',
      country: 'SE', trip_status: 'edited', netvisor_status: 'created_manual',
      netvisor_report_id: '1655', warnings: ['Already created in Netvisor'], ready_for_netvisor: false },
    { trip_id: 79, start_date: '2026-05-07', end_date: '2026-05-08', destination: 'Båstad',
      country: 'SE', trip_status: 'approved', netvisor_status: 'not_created',
      netvisor_report_id: null, warnings: [], ready_for_netvisor: true },
  ],
};

test.beforeEach(async ({ page }) => {
  await setupApiMocks(page, { calendarTrips: [TRIP] });
  await page.route('**/api/netvisor/**', (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/overview')) return route.fulfill({ json: OVERVIEW });
    if (url.pathname.endsWith('/entry')) return route.fulfill({ json: ENTRY });
    if (url.pathname.endsWith('/payload')) {
      return route.fulfill({ json: { xml: '<root><tripexpense/></root>', schema_errors: [],
        uncertain_fields: { amount: 'unknown' } } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
});

test('Netvisor-översikten visar status och filtret Klara för Netvisor', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trips-tab-netvisor').click();
  const ov = page.getByTestId('netvisor-overview');
  await expect(page.getByTestId('netvisor-overview-93')).toContainText('#1655');
  await expect(page.getByTestId('netvisor-overview-79')).toBeVisible();
  await page.getByTestId('netvisor-ready-filter').check();
  await expect(page.getByTestId('netvisor-overview-93')).toHaveCount(0);
  await expect(ov).toContainText('Båstad');
});

test('Netvisor-fliken visar två kalkylatorrader med kost-flagga', async ({ page }) => {
  await page.goto('/trips');
  await page.getByTestId('trips-tab-approved').click();
  await page.getByTestId('trip-row-toggle-79').click();
  const panel = page.getByTestId('netvisor-panel-79');
  await panel.locator('summary').first().click();
  await expect(panel).toContainText('Business trip Båstad, Sweden 7.5.2026-8.5.2026');
  await expect(page.getByTestId('netvisor-row-1')).toContainText('Sverige');
  await expect(page.getByTestId('netvisor-row-1')).toContainText('7.5.2026');
  await expect(page.getByTestId('netvisor-row-2')).toContainText('20:59');
  await expect(panel.locator('a[href$="payload.xml"]')).toHaveCount(1);
});
