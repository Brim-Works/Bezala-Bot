import { useCallback, useEffect, useState } from 'react';
import { api } from '../../api/client.js';
import { useI18n } from '../../i18n/useI18n.jsx';

/* C46 — avsändare som boten får läsa i privata Gmail. Tabellen
 * private_gmail_senders är sanningen. "Ta bort" sätter active=false. En ny
 * avsändare startar en engångsskanning från 2026-01-01 i bakgrunden. */
export default function PrivateSendersBlock() {
  const { t } = useI18n();
  const s = t.settings.auth.senders;
  const [data, setData] = useState({ senders: [], last_scan: null });
  const [address, setAddress] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await api.privateSenders();
      if (d && Array.isArray(d.senders)) setData(d);
    } catch (e) {
      /* visas av global felhantering */
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const add = async (ev) => {
    ev.preventDefault();
    setError('');
    setInfo('');
    if (!address.trim()) return;
    setBusy(true);
    try {
      const r = await api.addPrivateSender({ address: address.trim(), note: note.trim() || null });
      setAddress('');
      setNote('');
      setInfo(r?.backfill_started ? s.addedScan : s.added);
      await load();
    } catch (e) {
      setError(e?.body?.detail || e?.message || s.addFailed);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (addr) => {
    setError('');
    setInfo('');
    try {
      await api.removePrivateSender(addr);
      await load();
    } catch (e) {
      setError(e?.body?.detail || e?.message || s.removeFailed);
    }
  };

  const active = data.senders.filter((x) => x.active);
  const fmt = (iso) => (iso ? new Date(iso).toLocaleString() : '—');

  return (
    <div className="private-senders" data-testid="private-senders-block">
      <div className="settings-field__label">{s.title}</div>
      <p className="muted">{s.lead}</p>
      {data.last_scan && (
        <p className="muted" data-testid="private-senders-last-scan">
          {s.lastScan
            .replace('{at}', fmt(data.last_scan.at))
            .replace('{found}', String(data.last_scan.found ?? 0))
            .replace('{rejected}', String(data.last_scan.rejected_unknown_sender ?? 0))}
        </p>
      )}
      {active.length === 0 ? (
        <p className="muted" data-testid="private-senders-empty">{s.empty}</p>
      ) : (
        <table className="private-senders__table" data-testid="private-senders-table">
          <thead>
            <tr>
              <th>{s.colAddress}</th>
              <th>{s.colNote}</th>
              <th>{s.colLastScan}</th>
              <th>{s.colFetched}</th>
              <th>{s.colRejected}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {active.map((x) => (
              <tr key={x.address} data-testid="private-sender-row">
                <td>
                  <code>{x.address}</code>
                  <span className="muted"> ({x.added_via})</span>
                </td>
                <td>{x.note || ''}</td>
                <td>{fmt(x.last_scan_at)}</td>
                <td>{x.fetched ?? 0}</td>
                <td>{x.rejected ?? 0}</td>
                <td>
                  <button
                    type="button"
                    className="btn ghost"
                    onClick={() => remove(x.address)}
                    data-testid="private-sender-remove"
                  >
                    {s.remove}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="private-senders__add" onSubmit={add}>
        <input
          type="text"
          value={address}
          placeholder={s.placeholder}
          onChange={(e) => setAddress(e.target.value)}
          data-testid="private-sender-input"
          aria-label={s.colAddress}
        />
        <input
          type="text"
          value={note}
          placeholder={s.notePlaceholder}
          onChange={(e) => setNote(e.target.value)}
          data-testid="private-sender-note"
          aria-label={s.colNote}
        />
        <button type="submit" className="btn primary" disabled={busy} data-testid="private-sender-add">
          {s.add}
        </button>
      </form>
      {error && <p className="settings-auth-row__status--err" data-testid="private-sender-error">{error}</p>}
      {info && <p className="muted" data-testid="private-sender-info">{info}</p>}
    </div>
  );
}
