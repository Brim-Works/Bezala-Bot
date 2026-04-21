/* Fetch-wrapper — session-cookies via credentials:'include', 401 → /login.
 *
 * En mekanism för att registrera en callback som triggas vid 401. Registreras
 * av App-shell:en så den kan navigera utan att api-klienten känner till
 * routerns internals.
 */

const BASE = import.meta.env.VITE_API_BASE || '';

let unauthorizedHandler = () => {
  if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
    window.location.href = '/login';
  }
};

export function setUnauthorizedHandler(handler) {
  if (typeof handler === 'function') {
    unauthorizedHandler = handler;
  }
}

export class ApiError extends Error {
  constructor(message, { status, body } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

async function request(path, options = {}) {
  const { method = 'GET', body, headers: customHeaders, signal } = options;
  const headers = { Accept: 'application/json', ...(customHeaders || {}) };
  let payload;
  if (body instanceof FormData || body == null) {
    payload = body;
  } else if (typeof body === 'string') {
    payload = body;
  } else {
    headers['Content-Type'] = headers['Content-Type'] || 'application/json';
    payload = JSON.stringify(body);
  }

  const resp = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: payload,
    credentials: 'include',
    signal,
  });

  if (resp.status === 401) {
    unauthorizedHandler();
    throw new ApiError('Not authenticated', { status: 401 });
  }

  if (resp.status === 204) return null;

  const contentType = resp.headers.get('content-type') || '';
  const isJson = contentType.includes('application/json');
  const data = isJson ? await resp.json().catch(() => null) : await resp.text();

  if (!resp.ok) {
    const message =
      (data && typeof data === 'object' && (data.detail || data.message)) ||
      `${resp.status} ${resp.statusText}`;
    throw new ApiError(message, { status: resp.status, body: data });
  }

  return data;
}

export const api = {
  me: () => request('/api/me'),
  stats: () => request('/api/stats'),
  messages: (limit = 50) => request(`/api/messages?limit=${limit}`),
  runs: (limit = 20) => request(`/api/runs?limit=${limit}`),
  settings: () => request('/api/settings'),
  updateSettings: (payload) => request('/api/settings', { method: 'PUT', body: payload }),
  scan: () => request('/api/scan', { method: 'POST' }),
  uploadToBezala: (id) => request(`/api/messages/${id}/upload-to-bezala`, { method: 'POST' }),
  deleteErrors: () => request('/api/messages/errors', { method: 'DELETE' }),
  trashList: (limit = 200) => request(`/api/messages/trash?limit=${limit}`),
  trashCount: () => request('/api/messages/trash/count'),
  softDeleteMessage: (id, reason = 'manual') =>
    request(`/api/messages/${id}`, { method: 'DELETE', body: { reason } }),
  hardDeleteMessage: (id, { purgeDrive = false } = {}) =>
    request(
      `/api/messages/${id}?permanent=true&purge_drive=${purgeDrive ? 'true' : 'false'}`,
      { method: 'DELETE' },
    ),
  restoreMessage: (id) =>
    request(`/api/messages/${id}/restore`, { method: 'POST' }),
  bulkDelete: ({ ids, reason = 'manual', permanent = false, purge_drive = false }) =>
    request('/api/messages/bulk-delete', {
      method: 'POST',
      body: { ids, reason, permanent, purge_drive },
    }),
  emptyTrash: ({ purgeDrive = false } = {}) =>
    request(
      `/api/messages/trash?purge_drive=${purgeDrive ? 'true' : 'false'}`,
      { method: 'DELETE' },
    ),
  logout: async () => {
    await fetch(`${BASE}/logout`, { method: 'POST', credentials: 'include' });
    if (typeof window !== 'undefined') {
      window.location.href = '/login';
    }
  },
};

export { request };
