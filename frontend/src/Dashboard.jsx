import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';

function StatCard({ label, value }) {
  return (
    <div className="stat-card">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

function StatusBadge({ status }) {
  const cls = status?.startsWith('saved')
    ? 'status-saved'
    : status === 'error'
    ? 'status-error'
    : 'status-skipped';
  return <span className={`status-badge ${cls}`}>{status || '—'}</span>;
}

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('sv-SE');
}

export default function Dashboard({ navigate }) {
  const [stats, setStats] = useState(null);
  const [messages, setMessages] = useState([]);
  const [selected, setSelected] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const [s, m] = await Promise.all([api.stats(), api.messages(100)]);
      setStats(s);
      setMessages(m);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  const triggerScan = async () => {
    setScanning(true);
    try {
      await api.scan();
      setTimeout(refresh, 1500);
    } catch (e) {
      setError(e.message);
    } finally {
      setTimeout(() => setScanning(false), 2000);
    }
  };

  const lastRun = stats?.last_run;

  return (
    <>
      <header>
        <h1>Bezala Bot</h1>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <button onClick={triggerScan} disabled={scanning}>
            {scanning ? 'Scannar…' : 'Kör scanning nu'}
          </button>
          <button onClick={() => navigate('/settings')} className="logout-btn">
            Inställningar
          </button>
          <button onClick={() => api.logout()} className="logout-btn">
            Logga ut
          </button>
        </div>
      </header>
      <div className="container">
        {error && (
          <div className="stat-card" style={{ borderColor: 'var(--err)', marginBottom: '1rem' }}>
            <div className="label">Fel</div>
            <div style={{ color: 'var(--err)' }}>{error}</div>
          </div>
        )}

        <div className="stats">
          <StatCard label="Totalt bearbetade" value={stats?.total ?? '—'} />
          <StatCard label="Sparade till Drive" value={stats?.saved ?? '—'} />
          <StatCard label="Fel" value={stats?.errors ?? '—'} />
          <StatCard
            label="Senaste scanning"
            value={lastRun?.finished_at ? formatDate(lastRun.finished_at) : '—'}
          />
        </div>

        <div className="section-header">
          <h2>Bearbetade mail</h2>
          <span className="muted">{messages.length} rader</span>
        </div>

        <div className="message-list">
          <table>
            <thead>
              <tr>
                <th>Tid</th>
                <th>Från</th>
                <th>Ämne</th>
                <th>Filnamn</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {messages.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted" style={{ textAlign: 'center', padding: '2rem' }}>
                    Inga mail bearbetade ännu.
                  </td>
                </tr>
              )}
              {messages.map((m) => (
                <tr
                  key={m.id}
                  onClick={() => setSelected(m)}
                  className={selected?.id === m.id ? 'selected' : ''}
                >
                  <td>{formatDate(m.processed_at)}</td>
                  <td>{m.sender || '—'}</td>
                  <td>{m.subject || '—'}</td>
                  <td>{m.file_name || <span className="muted">—</span>}</td>
                  <td><StatusBadge status={m.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {selected && (
          <div className="preview-pane">
            <h3>{selected.file_name || selected.subject || selected.message_id}</h3>
            <p className="muted">Från: {selected.sender || '—'}</p>
            <p className="muted">Ämne: {selected.subject || '—'}</p>
            {selected.error_message && (
              <p style={{ color: 'var(--err)' }}>Fel: {selected.error_message}</p>
            )}
            {selected.drive_link && (
              <>
                <p>
                  <a href={selected.drive_link} target="_blank" rel="noreferrer">
                    Öppna i Google Drive ↗
                  </a>
                </p>
                {selected.drive_file_id && (
                  <iframe
                    title="PDF preview"
                    src={`https://drive.google.com/file/d/${selected.drive_file_id}/preview`}
                    width="100%"
                    height="600"
                    style={{ border: 0, borderRadius: 6 }}
                  />
                )}
              </>
            )}
          </div>
        )}
      </div>
    </>
  );
}
