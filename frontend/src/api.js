const BASE = import.meta.env.VITE_API_BASE || '';

async function req(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
  });
  if (resp.status === 401) {
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
    throw new Error('Not authenticated');
  }
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  if (resp.status === 204) return null;
  return resp.json();
}

export const api = {
  me: () => req('/api/me'),
  stats: () => req('/api/stats'),
  messages: (limit = 50) => req(`/api/messages?limit=${limit}`),
  runs: (limit = 20) => req(`/api/runs?limit=${limit}`),
  scan: () => req('/api/scan', { method: 'POST' }),
  deleteErrors: () => req('/api/messages/errors', { method: 'DELETE' }),
  uploadToBezala: (id) => req(`/api/messages/${id}/upload-to-bezala`, { method: 'POST' }),
  getSettings: () => req('/api/settings'),
  updateSettings: (payload) => req('/api/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }),
  logout: async () => {
    await fetch(`${BASE}/logout`, { method: 'POST', credentials: 'same-origin' });
    window.location.href = '/login';
  },
};
