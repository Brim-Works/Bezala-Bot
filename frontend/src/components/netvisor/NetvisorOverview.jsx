import { useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { request } from '../../api/client.js';
import { nvStrings } from './strings.js';
import './netvisor.css';

/* C49 Del 5 — översikt 2026: datum, ort, land, status, Netvisor-status,
 * varningar. Filter "Klara för Netvisor" = approved + not_created + inga
 * varningar. Bara läsning. */

export default function NetvisorOverview({ year = 2026 }) {
  const { lang, t } = useI18n();
  const s = nvStrings(lang);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [readyOnly, setReadyOnly] = useState(false);

  useEffect(() => {
    let alive = true;
    request(`/api/netvisor/overview?year=${year}`)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.message || String(e)); });
    return () => { alive = false; };
  }, [year]);

  const rows = data ? data.trips.filter((r) => !readyOnly || r.ready_for_netvisor) : [];
  return (
    <section className="trips-view__section" data-testid="netvisor-overview">
      <div className="cal-trips__head">
        <div>
          <h3 className="trips-view__section-title">{s.overviewTitle}</h3>
          <p className="muted cal-trips__desc">{s.overviewDesc}</p>
        </div>
        <label className="nv-filter">
          <input
            type="checkbox"
            checked={readyOnly}
            onChange={(e) => setReadyOnly(e.target.checked)}
            data-testid="netvisor-ready-filter"
          />{' '}
          {s.readyFilter}{data ? ` (${data.ready_count})` : ''}
        </label>
      </div>
      {error ? <div className="card card-pad pill pill--err">{s.error}: {error}</div> : null}
      {data && rows.length === 0 ? <div className="card card-pad muted">{s.none}</div> : null}
      {rows.length ? (
        <div className="card nv-table-wrap">
          <table className="nv-table">
            <thead>
              <tr>
                <th>{s.colDate}</th><th>{s.colPlace}</th><th>{s.colCountry}</th>
                <th>{s.colStatus}</th><th>{s.colNetvisor}</th><th>{s.colWarnings}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.trip_id} data-testid={`netvisor-overview-${r.trip_id}`}>
                  <td className="mono">{r.start_date}{r.end_date !== r.start_date ? ` – ${r.end_date}` : ''}</td>
                  <td>{r.destination || '—'}</td>
                  <td className="mono">{r.country || '—'}</td>
                  <td className="mono">{t.trips.calendar[r.trip_status] || r.trip_status}</td>
                  <td className="mono">
                    {s.nvStatus[r.netvisor_status] || r.netvisor_status}
                    {r.netvisor_report_id ? ` #${r.netvisor_report_id}` : ''}
                    {r.ready_for_netvisor ? <span className="pill pill--ok mono"> {s.ready}</span> : null}
                  </td>
                  <td className="nv-small">
                    {r.warnings.length ? (
                      <ul className="nv-wlist">{r.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
                    ) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
