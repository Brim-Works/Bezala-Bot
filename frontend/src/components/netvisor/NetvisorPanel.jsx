import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { request } from '../../api/client.js';
import { nvStrings, fill } from './strings.js';
import './netvisor.css';

/* C49 — "Netvisor"-flik per resa: exakt det som fylls i i Netvisors
 * Dagpenningskalkylator + förhandsvisning/nedladdning av tripexpense.nv.
 * Bara läsning. Ingen knapp skickar något till Netvisor. */

function YesNo({ s, value }) {
  return <span className={value ? 'pill pill--ok mono' : 'pill pill--muted mono'}>{value ? s.yes : s.no}</span>;
}

function Row({ s, r }) {
  const st = r.settings || {};
  return (
    <div className="nv-row card-pad" data-testid={`netvisor-row-${r.row}`}>
      <div className="cal-trip__label muted mono">{fill(s.row, { n: r.row })}</div>
      <dl className="nv-grid">
        <dt>{s.byAir}</dt><dd><YesNo s={s} value={r.by_air_or_sea} /></dd>
        <dt>{s.object}</dt><dd className="cal-trip__strong">{r.object || '—'}</dd>
        <dt>{s.start}</dt><dd className="mono">{r.start_date || '—'} {r.start_time || ''}</dd>
        <dt>{s.end}</dt><dd className="mono">{r.end_date || '—'} {r.end_time || ''}</dd>
        <dt>{s.lineDescription}</dt><dd className="nv-copyable">{r.description}</dd>
        <dt>{s.over15}</dt><dd><YesNo s={s} value={st.over_15_km} /></dd>
        <dt>{s.over5}</dt><dd><YesNo s={s} value={st.over_5_km} /></dd>
        <dt>{s.meals}</dt>
        <dd>
          <YesNo s={s} value={st.meals_included} />
          {st.meals_included_days && st.meals_included_days.length ? (
            <span className="mono muted"> {st.meals_included_days.join(', ')}</span>
          ) : null}
        </dd>
        <dt>{s.night}</dt>
        <dd>
          <YesNo s={s} value={st.night_travel} />
          {st.night_travel_check ? <span className="muted nv-small"> ⚑</span> : null}
        </dd>
      </dl>
    </div>
  );
}

export default function NetvisorPanel({ tripId }) {
  const { lang } = useI18n();
  const s = nvStrings(lang);
  const [entry, setEntry] = useState(null);
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (entry || loading) return;
    setLoading(true);
    try {
      const [e, p] = await Promise.all([
        request(`/api/netvisor/trips/${tripId}/entry`),
        request(`/api/netvisor/trips/${tripId}/payload`),
      ]);
      setEntry(e);
      setPayload(p);
    } catch (err) {
      setError(err.message || String(err));
    } finally {
      setLoading(false);
    }
  };

  const nv = entry && entry.netvisor_status;
  return (
    <details
      className="cal-trip__netvisor nv-panel"
      data-testid={`netvisor-panel-${tripId}`}
      onToggle={(ev) => { if (ev.currentTarget.open) load(); }}
    >
      <summary className="cal-trip__label mono">{s.tab}</summary>
      {loading ? <div className="muted">{s.loading}</div> : null}
      {error ? <div className="pill pill--err">{s.error}: {error}</div> : null}
      {entry ? (
        <div className="nv-body">
          <div className="nv-line">
            <span className="muted">{s.status}: </span>
            <span className="mono">
              {s.nvStatus[nv.status] || nv.status}
              {nv.netvisor_report_id ? ` #${nv.netvisor_report_id}` : ''}
            </span>
            {nv.note ? <span className="muted"> – {nv.note}</span> : null}
          </div>
          {entry.warnings.length ? (
            <div className="nv-warn" data-testid={`netvisor-warnings-${tripId}`}>
              <div className="cal-trip__label mono">{s.warnings}</div>
              <ul>{entry.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          ) : null}
          <dl className="nv-grid">
            <dt>{s.title}</dt><dd className="cal-trip__strong nv-copyable">{entry.title}</dd>
            <dt>{s.description}</dt><dd className="nv-copyable">{entry.description || '—'}</dd>
          </dl>
          {entry.rows.map((r) => <Row key={r.row} s={s} r={r} />)}
          {entry.notes.length ? (
            <div className="nv-notes">
              <div className="cal-trip__label muted mono">{s.notes}</div>
              <ul>{entry.notes.map((n) => <li key={n} className="muted">{n}</li>)}</ul>
            </div>
          ) : null}
          <div className="muted nv-small">{s.noAmounts}</div>
          {payload ? (
            <details className="nv-preview">
              <summary className="mono">{s.preview}</summary>
              <div className="nv-actions">
                <a className="btn btn--ghost" href={`/api/netvisor/trips/${tripId}/payload.xml`}>{s.downloadXml}</a>
                <a className="btn btn--ghost" href={`/api/netvisor/trips/${tripId}/payload.json`}>{s.downloadJson}</a>
              </div>
              {payload.schema_errors && payload.schema_errors.length ? (
                <div className="nv-warn">
                  <div className="cal-trip__label mono">{s.schemaErrors}</div>
                  <ul>{payload.schema_errors.map((e) => <li key={e}>{e}</li>)}</ul>
                </div>
              ) : null}
              <pre className="nv-xml mono">{payload.xml}</pre>
              <div className="cal-trip__label muted mono">{s.uncertain}</div>
              <ul className="nv-uncertain">
                {Object.entries(payload.uncertain_fields || {}).map(([k, v]) => (
                  <li key={k}><span className="mono">{k}</span>: <span className="muted">{v}</span></li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      ) : null}
    </details>
  );
}
