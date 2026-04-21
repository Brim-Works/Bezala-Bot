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

function BezalaBadge({ status }) {
  if (!status) return <span className="muted">—</span>;
  const map = {
    success: { cls: 'status-saved', label: '✅ Uppladdad' },
    pending: { cls: 'bezala-pending', label: '⏳ Väntar' },
    failed: { cls: 'status-error', label: '❌ Fel' },
    skipped: { cls: 'status-skipped', label: '—' },
  };
  const { cls, label } = map[status] || { cls: 'status-skipped', label: status };
  return <span className={`status-badge ${cls}`}>{label}</span>;
}

function formatDate(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('sv-SE');
}

function formatAmount(amount, currency) {
  if (amount === null || amount === undefined) return '—';
  const n = Number(amount);
  if (Number.isNaN(n)) return '—';
  const formatted = n.toLocaleString('sv-SE', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

export default function Dashboard({ navigate }) {
  const [stats, setStats] = useState(null);
  const [messages, setMessages] = useState([]);
  const [selected, setSelected] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [uploadingId, setUploadingId] = useState(null);
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

  const uploadToBezala = async (id) => {
    setUploadingId(id);
    setError(null);
    try {
      const updated = await api.uploadToBezala(id);
      setMessages((ms) => ms.map((m) => (m.id === id ? updated : m)));
      if (selected?.id === id) {
        setSelected(updated);
      }
    } catch (e) {
      setError(e.message);
      await refresh();
    } finally {
      setUploadingId(null);
    }
  };

  const clearErrors = async () => {
    if (!window.confirm('Ta bort alla rader med status "error"? De kan processas om vid nästa scanning.')) {
      return;
    }
    setClearing(true);
    try {
      const { deleted } = await api.deleteErrors();
      setError(null);
      await refresh();
      if (deleted === 0) {
        window.alert('Inga error-rader fanns att rensa.');
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setClearing(false);
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
          <button onClick={clearErrors} disabled={clearing} className="logout-btn">
            {clearing ? 'Rensar…' : 'Rensa felade'}
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
                <th>Leverantör</th>
                <th>Belopp</th>
                <th>Kategori</th>
                <th>Filnamn</th>
                <th>Status</th>
                <th>Bezala</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {messages.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted" style={{ textAlign: 'center', padding: '2rem' }}>
                    Inga mail bearbetade ännu.
                  </td>
                </tr>
              )}
              {messages.map((m) => {
                const canUpload =
                  (m.bezala_upload_status === 'pending' || m.bezala_upload_status === 'failed')
                  && m.status === 'saved'
                  && m.drive_file_id;
                return (
                  <tr
                    key={m.id}
                    onClick={() => setSelected(m)}
                    className={selected?.id === m.id ? 'selected' : ''}
                  >
                    <td>{formatDate(m.processed_at)}</td>
                    <td>{m.vendor || <span className="muted">—</span>}</td>
                    <td>{formatAmount(m.amount, m.currency)}</td>
                    <td>{m.category || <span className="muted">—</span>}</td>
                    <td>{m.file_name || <span className="muted">—</span>}</td>
                    <td><StatusBadge status={m.status} /></td>
                    <td><BezalaBadge status={m.bezala_upload_status} /></td>
                    <td>
                      {canUpload && (
                        <button
                          className="inline-btn"
                          disabled={uploadingId === m.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            uploadToBezala(m.id);
                          }}
                        >
                          {uploadingId === m.id ? 'Laddar upp…' : 'Ladda upp till Bezala'}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {selected && (
          <div className="preview-pane">
            <h3>{selected.file_name || selected.subject || selected.message_id}</h3>
            <p className="muted">Från: {selected.sender || '—'}</p>
            <p className="muted">Ämne: {selected.subject || '—'}</p>
            {(selected.vendor || selected.amount !== null || selected.category || selected.summary) && (
              <div className="ai-meta">
                {selected.vendor && <p><strong>Leverantör:</strong> {selected.vendor}</p>}
                {selected.amount !== null && selected.amount !== undefined && (
                  <p><strong>Belopp:</strong> {formatAmount(selected.amount, selected.currency)}</p>
                )}
                {selected.receipt_date && <p><strong>Kvittodatum:</strong> {selected.receipt_date}</p>}
                {selected.category && <p><strong>Kategori:</strong> {selected.category}</p>}
                {selected.ai_confidence !== null && selected.ai_confidence !== undefined && (
                  <p><strong>AI-confidence:</strong> {selected.ai_confidence}%</p>
                )}
                {selected.summary && <p className="muted">{selected.summary}</p>}
              </div>
            )}
            {selected.bezala_upload_status && (
              <p>
                <strong>Bezala:</strong>{' '}
                <BezalaBadge status={selected.bezala_upload_status} />
                {selected.bezala_transaction_id && (
                  <span className="muted"> (id: {selected.bezala_transaction_id})</span>
                )}
              </p>
            )}
            {selected.bezala_error_message && (
              <p style={{ color: 'var(--err)' }}>
                Bezala-fel: {selected.bezala_error_message}
              </p>
            )}
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
