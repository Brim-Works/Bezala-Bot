const BASE = import.meta.env.VITE_API_BASE || '';

async function req(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, options);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json();
}

export const api = {
  stats: () => req('/api/stats'),
  messages: (limit = 50) => req(`/api/messages?limit=${limit}`),
  runs: (limit = 20) => req(`/api/runs?limit=${limit}`),
  scan: () => req('/api/scan', { method: 'POST' }),
};
