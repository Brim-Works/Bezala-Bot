/* Mock-fixturer för Playwright. Route-intercept på /api/* returnerar
 * deterministisk data så testerna kan köra utan backend. */

const ONE_HOUR = 60 * 60 * 1000;
const ONE_DAY = 24 * ONE_HOUR;

function isoAgo(ms) {
  return new Date(Date.now() - ms).toISOString();
}

export function buildMessages() {
  return [
    {
      id: 1,
      message_id: 'gmail-msg-1',
      sender: 'Finnair <receipts@finnair.com>',
      subject: 'Din kvittens — HEL-CPH',
      received_at: isoAgo(3 * ONE_HOUR),
      processed_at: isoAgo(3 * ONE_HOUR),
      file_name: '20260420 Finnair HEL-CPH.pdf',
      drive_file_id: 'drive-file-1',
      drive_link: 'https://drive.google.com/file/d/drive-file-1/view',
      status: 'saved',
      error_message: null,
      vendor: 'Finnair',
      amount: 503.00,
      currency: 'EUR',
      receipt_date: '2026-04-20',
      category: 'Flyg',
      summary: 'Flygbiljett HEL-CPH den 20 april.',
      ai_confidence: 94,
      bezala_transaction_id: null,
      bezala_upload_status: 'pending',
      bezala_error_message: null,
    },
    {
      id: 2,
      message_id: 'gmail-msg-2',
      sender: 'SL <kvitto@sl.se>',
      subject: 'Månadskort april 2026',
      received_at: isoAgo(8 * ONE_HOUR),
      processed_at: isoAgo(8 * ONE_HOUR),
      file_name: '20260415 SL Manadskort.pdf',
      drive_file_id: 'drive-file-2',
      drive_link: 'https://drive.google.com/file/d/drive-file-2/view',
      status: 'saved',
      error_message: null,
      vendor: 'SL',
      amount: 970.00,
      currency: 'SEK',
      receipt_date: '2026-04-15',
      category: 'Taxi',
      summary: 'Kollektivtrafik Stockholm.',
      ai_confidence: 88,
      bezala_transaction_id: null,
      bezala_upload_status: 'pending',
      bezala_error_message: null,
    },
    {
      id: 3,
      message_id: 'gmail-msg-3',
      sender: 'Scandic <info@scandichotels.com>',
      subject: 'Hotellbokning Stockholm',
      received_at: isoAgo(1.5 * ONE_DAY),
      processed_at: isoAgo(1.5 * ONE_DAY),
      file_name: '20260410 Scandic Hotell Stockholm.pdf',
      drive_file_id: 'drive-file-3',
      drive_link: 'https://drive.google.com/file/d/drive-file-3/view',
      status: 'saved',
      error_message: null,
      vendor: 'Scandic',
      amount: 1850.00,
      currency: 'SEK',
      receipt_date: '2026-04-10',
      category: 'Hotell',
      summary: 'Hotellnatt inkl. frukost.',
      ai_confidence: 77,
      bezala_transaction_id: null,
      bezala_upload_status: 'pending',
      bezala_error_message: null,
    },
    {
      id: 4,
      message_id: 'gmail-msg-4',
      sender: 'Clas Ohlson <order@clasohlson.com>',
      subject: 'Orderbekräftelse #887766',
      received_at: isoAgo(2 * ONE_HOUR),
      processed_at: isoAgo(2 * ONE_HOUR),
      file_name: '20260421 Clas Ohlson Kontorsmaterial.pdf',
      drive_file_id: 'drive-file-4',
      drive_link: 'https://drive.google.com/file/d/drive-file-4/view',
      status: 'saved',
      error_message: null,
      vendor: 'Clas Ohlson',
      amount: 249.00,
      currency: 'SEK',
      receipt_date: '2026-04-21',
      category: 'Annat',
      summary: 'Kontorsmaterial.',
      ai_confidence: 96,
      bezala_transaction_id: 'bez-txn-004',
      bezala_upload_status: 'success',
      bezala_error_message: null,
    },
    {
      id: 5,
      message_id: 'gmail-msg-5',
      sender: 'Uber <receipts@uber.com>',
      subject: 'Your Monday morning trip',
      received_at: isoAgo(20 * ONE_HOUR),
      processed_at: isoAgo(20 * ONE_HOUR),
      file_name: '20260420 Uber Taxi HEL.pdf',
      drive_file_id: 'drive-file-5',
      drive_link: 'https://drive.google.com/file/d/drive-file-5/view',
      status: 'saved',
      error_message: null,
      vendor: 'Uber',
      amount: 23.50,
      currency: 'EUR',
      receipt_date: '2026-04-20',
      category: 'Taxi',
      summary: 'Taxi i Helsingfors.',
      ai_confidence: 91,
      bezala_transaction_id: null,
      bezala_upload_status: 'failed',
      bezala_error_message: '422 Unprocessable Entity',
    },
  ];
}

export function buildStats() {
  return {
    total: 17,
    saved: 15,
    errors: 1,
    last_run: {
      started_at: isoAgo(30 * 60 * 1000),
      finished_at: isoAgo(29 * 60 * 1000),
      status: 'ok',
      messages_processed: 3,
    },
  };
}

export function buildRuns() {
  const runs = [];
  for (let i = 0; i < 14; i += 1) {
    const start = Date.now() - (i + 1) * ONE_HOUR;
    const end = start + 45 * 1000 + Math.random() * 15000;
    const processed = i === 0 ? 3 : i === 1 ? 0 : i === 2 ? 2 : i === 3 ? 1 : 0;
    const errors = i === 3 ? 1 : 0;
    runs.push({
      id: 100 + i,
      started_at: new Date(start).toISOString(),
      finished_at: new Date(end).toISOString(),
      messages_found: processed + errors,
      messages_processed: processed,
      messages_skipped: 0,
      errors,
      status: errors > 0 ? 'ok' : 'ok',
      notes: null,
    });
  }
  return runs;
}

/* Sätter upp alla nödvändiga mockar på en page. Tar en optional
 * override-map så enskilda tester kan svara annorlunda. */
export async function setupApiMocks(page, overrides = {}) {
  const state = {
    messages: overrides.messages || buildMessages(),
    stats: overrides.stats || buildStats(),
    runs: overrides.runs || buildRuns(),
    uploadResponse: overrides.uploadResponse || null,
    deleteErrorsResponse: overrides.deleteErrorsResponse || { deleted: 1 },
  };

  await page.route('**/api/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ authenticated: true }),
    }),
  );

  await page.route('**/api/messages?**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.messages),
    });
  });

  await page.route('**/api/messages', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.messages),
    });
  });

  await page.route('**/api/stats', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.stats),
    }),
  );

  await page.route('**/api/runs**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.runs),
    }),
  );

  await page.route('**/api/messages/*/upload-to-bezala', (route) => {
    if (state.uploadResponse && state.uploadResponse.status) {
      return route.fulfill(state.uploadResponse);
    }
    const url = new URL(route.request().url());
    const match = url.pathname.match(/\/api\/messages\/(\d+)\/upload-to-bezala/);
    const id = match ? Number(match[1]) : null;
    // Simulera riktigt backend-beteende: muterar state.messages så att nästa
    // /api/messages-refetch inte längre har raden som pending.
    const idx = state.messages.findIndex((m) => m.id === id);
    if (idx >= 0) {
      state.messages[idx] = {
        ...state.messages[idx],
        bezala_transaction_id: 'bez-txn-new',
        bezala_upload_status: 'success',
        bezala_error_message: null,
      };
    }
    const row = idx >= 0 ? state.messages[idx] : {};
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(row),
    });
  });

  await page.route('**/api/messages/errors', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(state.deleteErrorsResponse),
    }),
  );

  await page.route('**/api/scan', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'started', max_results: 50 }),
    }),
  );

  await page.route('**/api/settings', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        scan_interval_minutes: 60,
        ai_naming_enabled: true,
        auto_upload_enabled: false,
        confidence_threshold: 90,
        require_attachments: true,
        exclude_promotions: true,
        exclude_social: true,
        exclude_calendar: true,
        include_senders: [],
        exclude_senders: [],
        exclude_subjects: [],
        updated_at: new Date().toISOString(),
      }),
    }),
  );

  return state;
}
