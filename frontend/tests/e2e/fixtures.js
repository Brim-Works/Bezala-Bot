/* Mock-fixturer för Playwright. Route-intercept på /api/* returnerar
 * deterministisk data så testerna kan köra utan backend. */

const ONE_HOUR = 60 * 60 * 1000;
const ONE_DAY = 24 * ONE_HOUR;

function isoAgo(ms) {
  return new Date(Date.now() - ms).toISOString();
}

function emptyTrashFields() {
  return { deleted_at: null, delete_reason: null };
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
      amount: 503.0,
      currency: 'EUR',
      receipt_date: '2026-04-20',
      category: 'Flyg',
      summary: 'Flygbiljett HEL-CPH den 20 april.',
      ai_confidence: 94,
      bezala_transaction_id: null,
      bezala_upload_status: 'pending',
      bezala_error_message: null,
      ...emptyTrashFields(),
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
      amount: 970.0,
      currency: 'SEK',
      receipt_date: '2026-04-15',
      category: 'Taxi',
      summary: 'Kollektivtrafik Stockholm.',
      ai_confidence: 88,
      bezala_transaction_id: null,
      bezala_upload_status: 'pending',
      bezala_error_message: null,
      ...emptyTrashFields(),
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
      amount: 1850.0,
      currency: 'SEK',
      receipt_date: '2026-04-10',
      category: 'Hotell',
      summary: 'Hotellnatt inkl. frukost.',
      ai_confidence: 77,
      bezala_transaction_id: null,
      bezala_upload_status: 'pending',
      bezala_error_message: null,
      ...emptyTrashFields(),
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
      amount: 249.0,
      currency: 'SEK',
      receipt_date: '2026-04-21',
      category: 'Annat',
      summary: 'Kontorsmaterial.',
      ai_confidence: 96,
      bezala_transaction_id: 'bez-txn-004',
      bezala_upload_status: 'success',
      bezala_error_message: null,
      ...emptyTrashFields(),
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
      amount: 23.5,
      currency: 'EUR',
      receipt_date: '2026-04-20',
      category: 'Taxi',
      summary: 'Taxi i Helsingfors.',
      ai_confidence: 91,
      bezala_transaction_id: null,
      bezala_upload_status: 'failed',
      bezala_error_message: '422 Unprocessable Entity',
      ...emptyTrashFields(),
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
      status: 'ok',
      notes: null,
      filtered_messages: [],
    });
  }
  return runs;
}

export function buildFilteredEntries() {
  return [
    {
      message_id: 'gm-moovy-1',
      sender: 'Moovy <kvitto@moovy.fi>',
      subject: 'Din parkering 19.04.2026',
      received_at: isoAgo(3 * ONE_HOUR),
      reason: 'ai_filtered',
      confidence: 35,
      detail: null,
    },
    {
      message_id: 'gm-html-2',
      sender: 'Skånetrafiken <kvitto@skanetrafiken.se>',
      subject: 'Biljett 12345',
      received_at: isoAgo(3 * ONE_HOUR),
      reason: 'html_pdf_failed',
      confidence: null,
      detail: 'weasyprint css error',
    },
    {
      message_id: 'gm-spam-3',
      sender: 'marketing@badguy.com',
      subject: 'SUPER KAMPANJ',
      received_at: isoAgo(3 * ONE_HOUR),
      reason: 'not_receipt',
      confidence: 92,
      detail: null,
    },
  ];
}

export function buildSettings(overrides = {}) {
  return {
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
    trash_auto_purge_days: 0,
    ai_min_confidence_to_save: 40,
    link_fetch_senders: ['noreply@arlandaexpress.se'],
    html_to_pdf_enabled: true,
    builtin_senders: [
      'eticket@amadeus.com',
      'noreply@finnair.com',
      'noreply@arlandaexpress.se',
    ],
    updated_at: new Date().toISOString(),
    ...overrides,
  };
}

export function buildSkippedMessage(overrides = {}) {
  return {
    id: 80,
    message_id: 'gmail-skipped-80',
    sender: 'Moovy <kvitto@moovy.fi>',
    subject: 'Din parkering',
    received_at: isoAgo(2 * ONE_HOUR),
    processed_at: isoAgo(2 * ONE_HOUR),
    file_name: null,
    drive_file_id: null,
    drive_link: null,
    status: 'skipped:no_pdf',
    error_message: null,
    vendor: null,
    amount: null,
    currency: null,
    receipt_date: null,
    category: null,
    summary: null,
    ai_confidence: null,
    bezala_transaction_id: null,
    bezala_upload_status: null,
    bezala_error_message: null,
    pending_link: null,
    ...emptyTrashFields(),
    ...overrides,
  };
}

export function buildPendingDownloadMessage(overrides = {}) {
  return {
    id: 99,
    message_id: 'gmail-pending-99',
    sender: 'Arlanda Express <noreply@arlandaexpress.se>',
    subject: 'Din resa — kvitto',
    received_at: isoAgo(1 * ONE_HOUR),
    processed_at: isoAgo(1 * ONE_HOUR),
    file_name: null,
    drive_file_id: null,
    drive_link: null,
    status: 'needs_manual_download',
    error_message: null,
    vendor: 'Arlanda Express',
    amount: null,
    currency: null,
    receipt_date: null,
    category: null,
    summary: null,
    ai_confidence: null,
    bezala_transaction_id: null,
    bezala_upload_status: null,
    bezala_error_message: null,
    pending_link: 'https://arlandaexpress.se/receipt/abc-token-xyz',
    ...emptyTrashFields(),
    ...overrides,
  };
}

// FAS 11.1 — Resor.
export function buildTripSuggestion(overrides = {}) {
  return {
    id: 1,
    title: 'Stockholm 30 apr - 2 maj 2026',
    destination: 'Stockholm, SE',
    start_date: '2026-04-30',
    end_date: '2026-05-02',
    total_amount: 412.32,
    base_currency: 'EUR',
    status: 'suggested',
    created_at: isoAgo(2 * 60 * 60 * 1000),
    user_decision_at: null,
    ai_confidence: 85,
    description: 'Affärsresa till Stockholm.',
    user_edited: false,
    netvisor_trip_id: null,
    netvisor_synced_at: null,
    messages: [
      {
        id: 1,
        message_id: 'gmail-msg-1',
        vendor: 'Finnair',
        amount: 200.0,
        currency: 'EUR',
        receipt_date: '2026-04-30',
        received_at: isoAgo(2 * 24 * 60 * 60 * 1000),
        category: 'Flyg',
        subject: 'Boarding pass HEL-ARN',
        summary: 'Flygbiljett',
        added_by: 'ai_suggestion',
      },
      {
        id: 2,
        message_id: 'gmail-msg-2',
        vendor: 'Hertz',
        amount: 150.0,
        currency: 'EUR',
        receipt_date: '2026-04-30',
        received_at: isoAgo(2 * 24 * 60 * 60 * 1000),
        category: 'Annat',
        subject: 'Hyrbil bokning',
        summary: 'Hyrbil',
        added_by: 'ai_suggestion',
      },
      {
        id: 3,
        message_id: 'gmail-msg-3',
        vendor: 'Skånetrafiken',
        amount: 62.32,
        currency: 'EUR',
        receipt_date: '2026-05-01',
        received_at: isoAgo(1 * 24 * 60 * 60 * 1000),
        category: 'Taxi',
        subject: 'Biljett 12345',
        summary: 'Kollektivtrafik',
        added_by: 'ai_suggestion',
      },
    ],
    ...overrides,
  };
}

export function buildActiveTrip(overrides = {}) {
  return buildTripSuggestion({
    id: 2,
    title: 'Helsinki 12-14 mar 2026',
    destination: 'Helsinki, FI',
    start_date: '2026-03-12',
    end_date: '2026-03-14',
    total_amount: 950.0,
    status: 'active',
    user_decision_at: isoAgo(24 * 60 * 60 * 1000),
    ai_confidence: 92,
    description: 'Affärsresa till Helsingfors.',
    messages: [
      {
        id: 10,
        message_id: 'gmail-active-1',
        vendor: 'SAS',
        amount: 400.0,
        currency: 'EUR',
        receipt_date: '2026-03-12',
        received_at: isoAgo(15 * 24 * 60 * 60 * 1000),
        category: 'Flyg',
        subject: 'Boarding pass',
        summary: 'Flyg',
        added_by: 'ai_suggestion',
      },
    ],
    ...overrides,
  });
}

function jsonResponse(body, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  };
}

/* Sätter upp alla nödvändiga mockar på en page. Trash-relaterade routes
 * hanteras via en central dispatcher på /api/messages/** som inspekterar
 * URL + method — så vi slipper kolla glob-pattern-ordningen. */
export async function setupApiMocks(page, overrides = {}) {
  const state = {
    messages: overrides.messages || buildMessages(),
    stats: overrides.stats || buildStats(),
    runs: overrides.runs || buildRuns(),
    settings: overrides.settings || buildSettings(),
    uploadResponse: overrides.uploadResponse || null,
    deleteErrorsResponse: overrides.deleteErrorsResponse || { deleted: 1 },
    fetchPdfResponse: overrides.fetchPdfResponse || null,
    lastDeleteRequest: null,
    lastFetchPdfId: null,
    lastReprocessId: null,
    reprocessResponse: overrides.reprocessResponse || null,
    bodyResponse: overrides.bodyResponse || null,
    fetchPdfFromUrlResponse: overrides.fetchPdfFromUrlResponse || null,
    lastFetchPdfFromUrl: null,
    lastFeedbackRequest: null,
    feedbackStats: overrides.feedbackStats || {
      total: 0,
      last_30_days: 0,
      by_field: {},
    },
    // FAS 11.1 — Resor
    tripSuggestions: overrides.tripSuggestions || [buildTripSuggestion()],
    activeTrips: overrides.activeTrips || [buildActiveTrip()],
    lastTripRequest: null,
    refreshTripsResponse: overrides.refreshTripsResponse || { generated: 1 },
  };

  // --- enklare globala routes ---
  await page.route('**/api/me', (route) =>
    route.fulfill(jsonResponse({ authenticated: true })),
  );

  await page.route('**/api/stats', (route) =>
    route.fulfill(jsonResponse(state.stats)),
  );

  await page.route('**/api/runs**', (route) =>
    route.fulfill(jsonResponse(state.runs)),
  );

  await page.route('**/api/scan', (route) =>
    route.fulfill(jsonResponse({ status: 'started', max_results: 50 })),
  );

  await page.route('**/api/settings', async (route) => {
    const request = route.request();
    if (request.method() === 'PUT') {
      const body = request.postDataJSON();
      state.settings = { ...state.settings, ...body, updated_at: new Date().toISOString() };
      return route.fulfill(jsonResponse(state.settings));
    }
    return route.fulfill(jsonResponse(state.settings));
  });

  // --- AI-feedback routes (FAS 8 + 8.1) ---
  await page.route('**/api/feedback/thumbs', async (route) => {
    const body = route.request().postDataJSON() || {};
    state.lastFeedbackRequest = { kind: 'thumbs', body };
    return route.fulfill(jsonResponse({ saved: 1 }));
  });

  await page.route('**/api/feedback/correction', async (route) => {
    const body = route.request().postDataJSON() || {};
    state.lastFeedbackRequest = { kind: 'correction', body };
    return route.fulfill(jsonResponse({ saved: true }));
  });

  await page.route('**/api/feedback/not-a-receipt', async (route) => {
    const body = route.request().postDataJSON() || {};
    state.lastFeedbackRequest = { kind: 'not_a_receipt', body };
    // Soft-deleta motsvarande rad i mock-state så nästa /api/messages-fetch
    // döljer den (frontend triggar refetch via bumpMessagesVersion).
    if (body.message_id) {
      const idx = state.messages.findIndex(
        (m) => m.message_id === body.message_id,
      );
      if (idx >= 0) {
        state.messages[idx] = {
          ...state.messages[idx],
          deleted_at: new Date().toISOString(),
          delete_reason: 'user_marked_not_receipt',
        };
      }
    }
    return route.fulfill(jsonResponse({ saved: true, deleted: true }));
  });

  await page.route('**/api/feedback/stats', (route) =>
    route.fulfill(jsonResponse(state.feedbackStats)),
  );

  // --- FAS 11.1 — Resor ---
  await page.route('**/api/trips**', async (route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    const pathname = url.pathname;

    if (method === 'GET' && pathname.endsWith('/api/trips/suggestions')) {
      return route.fulfill(jsonResponse({ trips: state.tripSuggestions }));
    }
    if (method === 'GET' && pathname.endsWith('/api/trips/active')) {
      return route.fulfill(jsonResponse({ trips: state.activeTrips }));
    }
    if (method === 'GET' && pathname.endsWith('/api/trips/stats')) {
      return route.fulfill(jsonResponse({
        suggested: state.tripSuggestions.length,
        active: state.activeTrips.length,
        total_amount_eur: state.activeTrips.reduce(
          (s, t) => s + (t.total_amount || 0), 0,
        ),
      }));
    }
    if (method === 'POST' && pathname.endsWith('/api/trips/refresh-suggestions')) {
      state.lastTripRequest = { kind: 'refresh' };
      return route.fulfill(jsonResponse(state.refreshTripsResponse));
    }

    const acceptMatch = pathname.match(/\/api\/trips\/(\d+)\/accept$/);
    if (method === 'POST' && acceptMatch) {
      const id = Number(acceptMatch[1]);
      const idx = state.tripSuggestions.findIndex((t) => t.id === id);
      if (idx < 0) return route.fulfill(jsonResponse({ detail: 'Not found' }, 404));
      const trip = { ...state.tripSuggestions[idx], status: 'active',
                     user_decision_at: new Date().toISOString() };
      state.tripSuggestions.splice(idx, 1);
      state.activeTrips.unshift(trip);
      state.lastTripRequest = { kind: 'accept', id };
      return route.fulfill(jsonResponse(trip));
    }

    const rejectMatch = pathname.match(/\/api\/trips\/(\d+)\/reject$/);
    if (method === 'POST' && rejectMatch) {
      const id = Number(rejectMatch[1]);
      const idx = state.tripSuggestions.findIndex((t) => t.id === id);
      if (idx < 0) return route.fulfill(jsonResponse({ detail: 'Not found' }, 404));
      const trip = { ...state.tripSuggestions[idx], status: 'rejected' };
      state.tripSuggestions.splice(idx, 1);
      state.lastTripRequest = { kind: 'reject', id };
      return route.fulfill(jsonResponse(trip));
    }

    const feedbackMatch = pathname.match(/\/api\/trips\/(\d+)\/feedback$/);
    if (method === 'POST' && feedbackMatch) {
      const id = Number(feedbackMatch[1]);
      const body = request.postDataJSON() || {};
      state.lastTripRequest = { kind: 'feedback', id, body };
      return route.fulfill(jsonResponse({ saved: true, id: 99 }));
    }

    const idMatch = pathname.match(/\/api\/trips\/(\d+)$/);
    if (idMatch) {
      const id = Number(idMatch[1]);
      const findInList = (list) => list.findIndex((t) => t.id === id);
      let idx = findInList(state.tripSuggestions);
      let list = state.tripSuggestions;
      if (idx < 0) {
        idx = findInList(state.activeTrips);
        list = state.activeTrips;
      }
      if (idx < 0) return route.fulfill(jsonResponse({ detail: 'Not found' }, 404));

      if (method === 'GET') {
        return route.fulfill(jsonResponse(list[idx]));
      }
      if (method === 'PATCH') {
        const body = request.postDataJSON() || {};
        const trip = { ...list[idx], user_edited: true };
        if (body.title != null) trip.title = body.title;
        if (body.destination != null) trip.destination = body.destination;
        if (body.start_date) trip.start_date = body.start_date;
        if (body.end_date) trip.end_date = body.end_date;
        if (body.description != null) trip.description = body.description;
        if (Array.isArray(body.remove_message_ids)) {
          trip.messages = (trip.messages || []).filter(
            (m) => !body.remove_message_ids.includes(m.message_id),
          );
        }
        list[idx] = trip;
        state.lastTripRequest = { kind: 'edit', id, body };
        return route.fulfill(jsonResponse(trip));
      }
      if (method === 'DELETE') {
        const trip = { ...list[idx], status: 'archived' };
        list.splice(idx, 1);
        state.lastTripRequest = { kind: 'archive', id };
        return route.fulfill(jsonResponse(trip));
      }
    }

    return route.fallback();
  });

  // --- central dispatcher för /api/messages/** ---
  await page.route('**/api/messages**', async (route) => {
    const request = route.request();
    const method = request.method();
    const url = new URL(request.url());
    const pathname = url.pathname;

    // GET /api/messages/trash/count
    if (method === 'GET' && pathname.endsWith('/api/messages/trash/count')) {
      const count = state.messages.filter((m) => m.deleted_at).length;
      return route.fulfill(jsonResponse({ count }));
    }

    // GET /api/messages/trash
    if (method === 'GET' && pathname.endsWith('/api/messages/trash')) {
      const trash = state.messages
        .filter((m) => m.deleted_at)
        .sort((a, b) => (b.deleted_at || '').localeCompare(a.deleted_at || ''));
      return route.fulfill(jsonResponse(trash));
    }

    // DELETE /api/messages/trash — töm papperskorgen
    if (method === 'DELETE' && pathname.endsWith('/api/messages/trash')) {
      const before = state.messages.length;
      state.messages = state.messages.filter((m) => !m.deleted_at);
      const deleted = before - state.messages.length;
      return route.fulfill(jsonResponse({ deleted }));
    }

    // DELETE /api/messages/errors
    if (method === 'DELETE' && pathname.endsWith('/api/messages/errors')) {
      return route.fulfill(jsonResponse(state.deleteErrorsResponse));
    }

    // POST /api/messages/bulk-delete
    if (method === 'POST' && pathname.endsWith('/api/messages/bulk-delete')) {
      const body = request.postDataJSON() || {};
      const ids = (body.ids || []).map(Number);
      const reason = body.reason || 'manual';
      const permanent = Boolean(body.permanent);
      state.lastDeleteRequest = { kind: 'bulk', body };
      if (permanent) {
        state.messages = state.messages.filter((m) => !ids.includes(m.id));
      } else {
        state.messages = state.messages.map((m) =>
          ids.includes(m.id)
            ? { ...m, deleted_at: new Date().toISOString(), delete_reason: reason }
            : m,
        );
      }
      return route.fulfill(jsonResponse({ deleted: ids.length, ids, permanent }));
    }

    // POST /api/messages/:id/reprocess
    const reprocessMatch = pathname.match(/\/api\/messages\/(\d+)\/reprocess$/);
    if (method === 'POST' && reprocessMatch) {
      const id = Number(reprocessMatch[1]);
      state.lastReprocessId = id;
      if (state.reprocessResponse && state.reprocessResponse.status) {
        return route.fulfill(state.reprocessResponse);
      }
      const idx = state.messages.findIndex((m) => m.id === id);
      const prior = idx >= 0 ? state.messages[idx].status : null;
      if (idx >= 0) {
        state.messages.splice(idx, 1);
      }
      return route.fulfill(
        jsonResponse({ status: 'reprocessing', id, prior_status: prior }),
      );
    }

    // GET /api/messages/:id/body
    const bodyMatch = pathname.match(/\/api\/messages\/(\d+)\/body$/);
    if (method === 'GET' && bodyMatch) {
      if (state.bodyResponse && state.bodyResponse.status) {
        return route.fulfill(state.bodyResponse);
      }
      return route.fulfill(
        jsonResponse({
          html:
            '<p>Tack för din resa!</p>' +
            '<p>Hämta kvitto: <a href="https://arlandaexpress.se/r/abc">Klicka här</a></p>',
          text: 'Tack för din resa! Hämta kvitto: https://arlandaexpress.se/r/abc',
          links: [
            { href: 'https://arlandaexpress.se/r/abc', text: 'Klicka här' },
          ],
        }),
      );
    }

    // POST /api/messages/:id/fetch-pdf-from-url
    const fetchPdfFromUrlMatch = pathname.match(
      /\/api\/messages\/(\d+)\/fetch-pdf-from-url$/,
    );
    if (method === 'POST' && fetchPdfFromUrlMatch) {
      const id = Number(fetchPdfFromUrlMatch[1]);
      const body = request.postDataJSON() || {};
      state.lastFetchPdfFromUrl = { id, url: body.url };
      if (state.fetchPdfFromUrlResponse && state.fetchPdfFromUrlResponse.status) {
        return route.fulfill(state.fetchPdfFromUrlResponse);
      }
      const idx = state.messages.findIndex((m) => m.id === id);
      if (idx < 0) {
        return route.fulfill(jsonResponse({ detail: 'Not found' }, 404));
      }
      state.messages[idx] = {
        ...state.messages[idx],
        status: 'saved',
        file_name: '20260421 Fetched From URL.pdf',
        drive_file_id: `drive-url-${id}`,
        drive_link: `https://drive.google.com/file/d/drive-url-${id}/view`,
        pending_link: null,
        vendor: state.messages[idx].vendor || 'URL-Leverantör',
        amount: 99.0,
        currency: 'SEK',
        ai_confidence: 91,
        bezala_upload_status: 'pending',
      };
      return route.fulfill(jsonResponse(state.messages[idx]));
    }

    // POST /api/messages/:id/fetch-pdf
    const fetchPdfMatch = pathname.match(/\/api\/messages\/(\d+)\/fetch-pdf$/);
    if (method === 'POST' && fetchPdfMatch) {
      const id = Number(fetchPdfMatch[1]);
      state.lastFetchPdfId = id;
      if (state.fetchPdfResponse && state.fetchPdfResponse.status) {
        return route.fulfill(state.fetchPdfResponse);
      }
      const idx = state.messages.findIndex((m) => m.id === id);
      if (idx < 0) {
        return route.fulfill(jsonResponse({ detail: 'Not found' }, 404));
      }
      state.messages[idx] = {
        ...state.messages[idx],
        status: 'saved',
        file_name: '20260421 Arlanda Express Resa.pdf',
        drive_file_id: `drive-fetched-${id}`,
        drive_link: `https://drive.google.com/file/d/drive-fetched-${id}/view`,
        pending_link: null,
        vendor: state.messages[idx].vendor || 'Arlanda Express',
        amount: 320.0,
        currency: 'SEK',
        ai_confidence: 92,
        bezala_upload_status: 'pending',
      };
      return route.fulfill(jsonResponse(state.messages[idx]));
    }

    // POST /api/messages/:id/upload-to-bezala
    const uploadMatch = pathname.match(/\/api\/messages\/(\d+)\/upload-to-bezala$/);
    if (method === 'POST' && uploadMatch) {
      if (state.uploadResponse && state.uploadResponse.status) {
        return route.fulfill(state.uploadResponse);
      }
      const id = Number(uploadMatch[1]);
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
      return route.fulfill(jsonResponse(row));
    }

    // POST /api/messages/:id/restore
    const restoreMatch = pathname.match(/\/api\/messages\/(\d+)\/restore$/);
    if (method === 'POST' && restoreMatch) {
      const id = Number(restoreMatch[1]);
      const idx = state.messages.findIndex((m) => m.id === id);
      if (idx >= 0) {
        state.messages[idx] = {
          ...state.messages[idx],
          deleted_at: null,
          delete_reason: null,
        };
        return route.fulfill(jsonResponse(state.messages[idx]));
      }
      return route.fulfill(jsonResponse({ detail: 'Not found' }, 404));
    }

    // DELETE /api/messages/:id (soft eller permanent)
    const idMatch = pathname.match(/\/api\/messages\/(\d+)$/);
    if (method === 'DELETE' && idMatch) {
      const id = Number(idMatch[1]);
      const permanent = url.searchParams.get('permanent') === 'true';
      const idx = state.messages.findIndex((m) => m.id === id);
      if (idx < 0) return route.fulfill(jsonResponse({ detail: 'Not found' }, 404));
      state.lastDeleteRequest = { kind: 'row', id, permanent };
      if (permanent) {
        state.messages.splice(idx, 1);
        return route.fulfill(jsonResponse({ status: 'deleted', permanent: true }));
      }
      let reason = 'manual';
      try {
        const body = request.postDataJSON();
        if (body && body.reason) reason = body.reason;
      } catch {
        // ingen body är OK
      }
      state.messages[idx] = {
        ...state.messages[idx],
        deleted_at: new Date().toISOString(),
        delete_reason: reason,
      };
      return route.fulfill(jsonResponse(state.messages[idx]));
    }

    // GET /api/messages + GET /api/messages?limit=…&include_deleted=…
    if (method === 'GET' && pathname.endsWith('/api/messages')) {
      const includeDeleted = url.searchParams.get('include_deleted') === 'true';
      const list = includeDeleted
        ? state.messages
        : state.messages.filter((m) => !m.deleted_at);
      return route.fulfill(jsonResponse(list));
    }

    return route.fallback();
  });

  return state;
}
