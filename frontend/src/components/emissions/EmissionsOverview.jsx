import { useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { request } from '../../api/client.js';
import '../netvisor/netvisor.css';

/* C53 — km per resa uppdelat på färdsätt (underlag för utsläppsrapportering).
 * Bara läsning; delsträckor rättas via MCP (update_trip_leg). Boten räknar
 * inga kg CO2e. */

const STR = {
  sv: {
    title: 'Km per resa',
    desc: 'Aktivitetsdata för utsläppsrapportering (km, kWh, liter). Bilresor hem–HEL är uppskattningar. Inga CO2e-beräkningar.',
    year: 'År',
    colDate: 'Datum', colPlace: 'Ort', colTotal: 'Summa km', colMissing: 'Saknar km', colEnergy: 'Energi',
    total: 'Summa', none: 'Inga resor.', error: 'Kunde inte hämta',
    csv: 'Ladda ner CSV', json: 'Ladda ner JSON',
    flags: { km_missing: 'km saknas', home_transport_unknown: 'hemresa okänd', return_flight_missing: 'returflyg saknas' },
  },
  en: {
    title: 'Km per trip',
    desc: 'Activity data for emissions reporting (km, kWh, litres). Car legs home-HEL are estimates. No CO2e calculation.',
    year: 'Year',
    colDate: 'Date', colPlace: 'Place', colTotal: 'Total km', colMissing: 'Missing km', colEnergy: 'Energy',
    total: 'Total', none: 'No trips.', error: 'Could not load',
    csv: 'Download CSV', json: 'Download JSON',
    flags: { km_missing: 'km missing', home_transport_unknown: 'home transport unknown', return_flight_missing: 'return flight missing' },
  },
};

const MODES = [
  ['flight', 'Flight'], ['train', 'Train'], ['bus', 'Bus'], ['taxi', 'Taxi'], ['car_phev', 'Car (PHEV)'],
];
const YEARS = [2026, 2025];

function km(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('sv-SE', { maximumFractionDigits: 1 });
}

function energy(kwh, litres) {
  const parts = [];
  if (kwh) parts.push(`${km(kwh)} kWh`);
  if (litres) parts.push(`${km(litres)} l`);
  return parts.length ? parts.join(', ') : '—';
}

export default function EmissionsOverview({ year: initialYear = 2026 }) {
  const { lang } = useI18n();
  const s = STR[lang] || STR.sv;
  const [year, setYear] = useState(initialYear);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    request(`/api/emissions/summary?year=${year}`)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.message || String(e)); });
    return () => { alive = false; };
  }, [year]);

  const rows = data ? data.per_trip : [];
  const byMode = data ? data.by_mode || {} : {};
  return (
    <section className="trips-view__section" data-testid="emissions-overview">
      <div className="cal-trips__head">
        <div>
          <h3 className="trips-view__section-title">{s.title}</h3>
          <p className="muted cal-trips__desc">{s.desc}</p>
        </div>
        <div className="nv-actions">
          <label className="nv-filter">
            {s.year}{' '}
            <select value={year} onChange={(e) => setYear(Number(e.target.value))} data-testid="emissions-year">
              {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
          <a className="btn" href={`/api/emissions/export.csv?year=${year}`} data-testid="emissions-csv">{s.csv}</a>
          <a className="btn" href={`/api/emissions/export.json?year=${year}`}>{s.json}</a>
        </div>
      </div>
      {error ? <div className="card card-pad pill pill--err">{s.error}: {error}</div> : null}
      {data && rows.length === 0 ? <div className="card card-pad muted">{s.none}</div> : null}
      {rows.length ? (
        <div className="card nv-table-wrap">
          <table className="nv-table">
            <thead>
              <tr>
                <th>{s.colDate}</th><th>{s.colPlace}</th>
                {MODES.map(([m, label]) => <th key={m}>{label}</th>)}
                <th>{s.colTotal}</th><th>{s.colEnergy}</th><th>{s.colMissing}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.trip_id} data-testid={`emissions-trip-${r.trip_id}`}>
                  <td className="mono">{r.start_date}{r.end_date !== r.start_date ? ` – ${r.end_date}` : ''}</td>
                  <td>{r.destination || '—'}</td>
                  {MODES.map(([m]) => <td key={m} className="mono">{r.by_mode[m] != null ? km(r.by_mode[m]) : '—'}</td>)}
                  <td className="mono"><strong>{km(r.km)}</strong></td>
                  <td className="mono nv-small">{energy(r.kwh, r.litres)}</td>
                  <td className="nv-small">
                    {r.legs_missing_km ? `${r.legs_missing_km}` : '—'}
                    {r.flags.filter((f) => f !== 'km_missing').map((f) => (
                      <div key={f} className="muted">{s.flags[f] || f}</div>
                    ))}
                  </td>
                </tr>
              ))}
              <tr data-testid="emissions-total">
                <td colSpan={2}><strong>{s.total} ({data.trips})</strong></td>
                {MODES.map(([m]) => <td key={m} className="mono"><strong>{byMode[m] ? km(byMode[m].km) : '—'}</strong></td>)}
                <td className="mono"><strong>{km(data.total_km)}</strong></td>
                <td className="mono nv-small">
                  {energy(byMode.car_phev && byMode.car_phev.kwh, byMode.car_phev && byMode.car_phev.litres)}
                </td>
                <td className="nv-small">{data.legs_missing_km || '—'}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
