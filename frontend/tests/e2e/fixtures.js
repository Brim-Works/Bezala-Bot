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
    });
  }
  return runs;
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
    updated_at: new Date().toISOString(),
    ...overrides,
  };
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
    lastDeleteRequest: null,
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
