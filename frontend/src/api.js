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
  logout: async () => {
    await fetch(`${BASE}/logout`, { method: 'POST', credentials: 'same-origin' });
    window.location.href = '/login';
  },
};
