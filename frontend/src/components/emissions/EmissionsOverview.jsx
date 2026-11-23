import { Fragment, useCallback, useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { request } from '../../api/client.js';
import '../netvisor/netvisor.css';

/* C53/C54 — Admins sju rader per resa (hållbarhetsrapport).
 * Siffrorna kommer från samma funktion som MCP get_emissions_summary och
 * export_trip_legs (/api/emissions/summary). Ändringar av delsträckor sparas
 * bara i Bezala Bots egen databas — inget går till Bezala. */

const ADMIN_KEYS = [
  'short_flights_km', 'medium_flights_km', 'long_flights_km',
  'train_km', 'bus_km', 'non_company_car_km', 'hotel_nights',
];

const STR = {
  sv: {
    title: 'Km per resa (Admins rapport)',
    desc: 'Admins sju rader: flyg kort (<700 km) / medel (700-3700) / lång (3700+) per flygsträcka, tåg, buss, taxi och egen bil, hotellnätter. Tjänstebilen visas separat och ingår inte. Inga CO2e-beräkningar.',
    year: 'År', yearTotal: 'Summa', colDate: 'Datum', colPlace: 'Ort', colWarn: 'Varning',
    total: 'Summa', none: 'Inga resor.', error: 'Kunde inte hämta',
    adminCsv: 'Admin-CSV', csv: 'Detalj-CSV', json: 'JSON', routes: 'Rutttabell',
    estimate: 'uppskattning', missing: (n) => `km saknas (${n})`,
    car: 'Tjänstebil (endast info, ingår inte i Admins rapport)',
    rejected: (n) => `${n} avvisade resor räknas inte`,
    labels: {
      short_flights_km: 'Flyg kort km', medium_flights_km: 'Flyg medel km', long_flights_km: 'Flyg lång km',
      train_km: 'Tåg km', bus_km: 'Buss km', non_company_car_km: 'Taxi/egen bil km', hotel_nights: 'Hotellnätter',
    },
    legs: 'Delsträckor', show: 'Visa', hide: 'Dölj',
    cols: ['Datum', 'Färdsätt', 'Från', 'Till', 'Km', 'Energi', 'Källa', 'Uppskattn.', 'Kvitto / Bezala', 'Uteslut', ''],
    save: 'Spara', saved: 'Sparat', yes: 'ja', no: 'nej', tripSum: 'Summa resan',
    reasons: {
      no_route_in_table: 'rutt saknas i tabellen', no_receipt: 'inget kvitto',
      direction_unknown: 'riktning okänd', no_km_on_receipt: 'km står inte på kvittot',
      airport_unknown: 'flygplats okänd',
    },
    onlyBot: 'Ändringar sparas bara i Bezala Bot, inget skickas till Bezala.',
    ask: 'Fråga Mikko',
  },
  en: {
    title: 'Km per trip (Admin report)',
    desc: "Admin's seven rows: short (<700 km) / medium (700-3700) / long (3700+) flights per flight leg, train, bus, taxi and own car, hotel nights. The company car is shown separately and not included. No CO2e calculation.",
    year: 'Year', yearTotal: 'Total', colDate: 'Date', colPlace: 'Place', colWarn: 'Warning',
    total: 'Total', none: 'No trips.', error: 'Could not load',
    adminCsv: 'Admin CSV', csv: 'Detail CSV', json: 'JSON', routes: 'Route table',
    estimate: 'estimate', missing: (n) => `km missing (${n})`,
    car: 'Company car (info only, not in Admin report)',
    rejected: (n) => `${n} rejected trips not counted`,
    labels: {
      short_flights_km: 'Short flights km', medium_flights_km: 'Medium flights km', long_flights_km: 'Long flights km',
      train_km: 'Train km', bus_km: 'Bus km', non_company_car_km: 'Non-company car km', hotel_nights: 'Hotel nights',
    },
    legs: 'Legs', show: 'Show', hide: 'Hide',
    cols: ['Date', 'Mode', 'From', 'To', 'Km', 'Energy', 'Source', 'Estimate', 'Receipt / Bezala', 'Exclude', ''],
    save: 'Save', saved: 'Saved', yes: 'yes', no: 'no', tripSum: 'Trip total',
    reasons: {
      no_route_in_table: 'no route in table', no_receipt: 'no receipt',
      direction_unknown: 'direction unknown', no_km_on_receipt: 'km not on receipt',
      airport_unknown: 'airport unknown',
    },
    onlyBot: 'Changes are saved in Bezala Bot only, nothing is sent to Bezala.',
    ask: 'Ask Mikko',
  },
};

const YEARS = [2026, 2025];

function num(v) {
  if (v == null) return '—';
  return Number(v).toLocaleString('sv-SE', { maximumFractionDigits: 1 });
}

function AdminCell({ admin, k, s }) {
  const v = admin[k];
  const est = admin.estimate && admin.estimate[k];
  return (
    <td className="mono" data-testid={`admin-${k}`}>
      {v ? num(v) : '—'}
      {v && est ? <div className="muted nv-small">{s.estimate}</div> : null}
    </td>
  );
}

function CarInfo({ car, s }) {
  if (!car || !car.legs) return null;
  const parts = [`${num(car.km)} km`];
  if (car.kwh) parts.push(`${num(car.kwh)} kWh`);
  if (car.litres) parts.push(`${num(car.litres)} l`);
  return <div className="muted nv-small" data-testid="company-car-info">{s.car}: {parts.join(', ')}</div>;
}

function LegRow({ leg, s, onSaved }) {
  const [km, setKm] = useState(leg.km ?? '');
  const [from, setFrom] = useState(leg.from || '');
  const [to, setTo] = useState(leg.to || '');
  const [excluded, setExcluded] = useState(leg.excluded);
  const [state, setState] = useState(null);
  const dirty = String(km) !== String(leg.km ?? '') || from !== (leg.from || '')
    || to !== (leg.to || '') || excluded !== leg.excluded;

  const save = async () => {
    const body = { excluded };
    if (String(km).trim() === '') {
      if (leg.km != null) body.clear_km = true;
    } else if (String(km) !== String(leg.km ?? '')) {
      body.km = Number(String(km).replace(',', '.'));
    }
    if (from !== (leg.from || '')) body.from_place = from;
    if (to !== (leg.to || '')) body.to_place = to;
    setState('saving');
    try {
      await request(`/api/emissions/legs/${leg.leg_id}`, { method: 'PATCH', body });
      setState('saved');
      onSaved();
    } catch (e) {
      setState(e.message || String(e));
    }
  };

  const r = leg.receipt;
  const energy = leg.energy_amount != null ? `${num(leg.energy_amount)} ${leg.energy_unit}` : '—';
  return (
    <tr data-testid={`leg-${leg.leg_id}`} className={leg.excluded ? 'muted' : ''}>
      <td className="mono">{leg.date || '—'}</td>
      <td>{leg.mode}{leg.flight_category ? ` (${leg.flight_category})` : ''}{leg.company_car_info_only ? ' *' : ''}</td>
      <td><input className="nv-input" value={from} onChange={(e) => setFrom(e.target.value)} size={12} /></td>
      <td><input className="nv-input" value={to} onChange={(e) => setTo(e.target.value)} size={12} /></td>
      <td>
        <input className="nv-input mono" value={km} onChange={(e) => setKm(e.target.value)} size={6} inputMode="decimal" />
        {leg.km_missing && !leg.excluded ? (
          <div className="pill pill--err nv-small">{s.reasons[leg.km_reason] || leg.km_reason || 'km'}</div>
        ) : null}
      </td>
      <td className="mono nv-small">{energy}</td>
      <td className="nv-small">{leg.source}{leg.set_by === 'user' ? ' (Mikko)' : ''}</td>
      <td className="nv-small">{leg.is_estimate ? s.yes : s.no}</td>
      <td className="nv-small">
        {r ? (
          <>
            {r.drive_link ? <a href={r.drive_link} target="_blank" rel="noreferrer">{r.vendor || 'Receipt'}</a> : (r.vendor || 'Receipt')}
            {' '}{r.amount != null ? `${num(r.amount)} ${r.currency || ''}` : ''}
            <div className="muted">#{r.receipt_id}{r.bezala_transaction_id ? ` - Bezala ${r.bezala_transaction_id}` : ' - Bezala -'}</div>
          </>
        ) : '—'}
        {leg.note ? <div className="muted">{leg.note}</div> : null}
      </td>
      <td><input type="checkbox" checked={excluded} onChange={(e) => setExcluded(e.target.checked)} aria-label={s.cols[9]} /></td>
      <td>
        <button type="button" className="btn" disabled={!dirty || state === 'saving'} onClick={save}>{s.save}</button>
        {state === 'saved' ? <div className="muted nv-small">{s.saved}</div> : null}
        {state && state !== 'saved' && state !== 'saving' ? <div className="pill pill--err nv-small">{state}</div> : null}
      </td>
    </tr>
  );
}

function TripLegs({ tripId, s, onChanged }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => {
    request(`/api/emissions/trips/${tripId}/legs`).then(setData).catch((e) => setError(e.message || String(e)));
  }, [tripId]);
  useEffect(() => { load(); }, [load]);
  if (error) return <div className="pill pill--err">{s.error}: {error}</div>;
  if (!data) return <div className="muted">…</div>;
  const saved = () => { load(); onChanged(); };
  return (
    <div data-testid={`trip-legs-${tripId}`}>
      <table className="nv-table">
        <thead><tr>{s.cols.map((c, i) => <th key={i}>{c}</th>)}</tr></thead>
        <tbody>
          {data.legs.map((l) => <LegRow key={`${l.leg_id}-${l.km}-${l.from}-${l.to}-${l.excluded}`} leg={l} s={s} onSaved={saved} />)}
          <tr data-testid={`trip-sum-${tripId}`}>
            <td colSpan={11}>
              <strong>{s.tripSum}:</strong>{' '}
              {ADMIN_KEYS.map((k) => `${s.labels[k]} ${num(data.admin[k])}${data.admin[k] && data.admin.estimate[k] ? ` (${s.estimate})` : ''}`).join(' · ')}
              <CarInfo car={data.admin.company_car_info_only} s={s} />
            </td>
          </tr>
        </tbody>
      </table>
      <p className="muted nv-small">* {s.car}. {s.onlyBot}</p>
    </div>
  );
}

export default function EmissionsOverview({ year: initialYear = 2026 }) {
  const { lang } = useI18n();
  const s = STR[lang] || STR.sv;
  const [year, setYear] = useState(initialYear);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    request(`/api/emissions/summary?year=${year}`)
      .then((d) => { if (alive) setData(d); })
      .catch((e) => { if (alive) setError(e.message || String(e)); });
    return () => { alive = false; };
  }, [year, tick]);

  const rows = data ? data.per_trip : [];
  const admin = data ? data.admin : null;
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
            <select value={year} onChange={(e) => { setYear(Number(e.target.value)); setOpen(null); }} data-testid="emissions-year">
              {YEARS.map((y) => <option key={y} value={y}>{y}</option>)}
            </select>
          </label>
          <a className="btn" href={`/api/emissions/export.csv?year=${year}&mode=admin`} data-testid="emissions-admin-csv">{s.adminCsv}</a>
          <a className="btn" href={`/api/emissions/export.csv?year=${year}`} data-testid="emissions-csv">{s.csv}</a>
          <a className="btn" href={`/api/emissions/export.json?year=${year}`}>{s.json}</a>
          <a className="btn" href={`/api/emissions/export.csv?year=${year}&mode=route_table`}>{s.routes}</a>
        </div>
      </div>
      {error ? <div className="card card-pad pill pill--err">{s.error}: {error}</div> : null}

      {admin ? (
        <div className="card card-pad" data-testid="emissions-year-total">
          <strong>{s.yearTotal} {year} ({data.trips})</strong>
          {admin.legs_missing_km ? <span className="pill pill--err" style={{ marginLeft: 8 }}>{s.missing(admin.legs_missing_km)}</span> : null}
          {data.trips_rejected_not_counted ? <span className="muted nv-small" style={{ marginLeft: 8 }}>{s.rejected(data.trips_rejected_not_counted)}</span> : null}
          <table className="nv-table">
            <thead><tr>{ADMIN_KEYS.map((k) => <th key={k}>{s.labels[k]}</th>)}</tr></thead>
            <tbody><tr>{ADMIN_KEYS.map((k) => <AdminCell key={k} admin={admin} k={k} s={s} />)}</tr></tbody>
          </table>
          <CarInfo car={data.company_car_info_only} s={s} />
        </div>
      ) : null}

      {data && rows.length === 0 ? <div className="card card-pad muted">{s.none}</div> : null}
      {rows.length ? (
        <div className="card nv-table-wrap">
          <table className="nv-table">
            <thead>
              <tr>
                <th>{s.colDate}</th><th>{s.colPlace}</th>
                {ADMIN_KEYS.map((k) => <th key={k}>{s.labels[k]}</th>)}
                <th>{s.colWarn}</th><th>{s.legs}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.trip_id}>
                  <tr data-testid={`emissions-trip-${r.trip_id}`}>
                    <td className="mono">{r.start_date}{r.end_date !== r.start_date ? ` – ${r.end_date}` : ''}</td>
                    <td>{r.destination || '—'}</td>
                    {ADMIN_KEYS.map((k) => <AdminCell key={k} admin={r.admin} k={k} s={s} />)}
                    <td className="nv-small">
                      {r.legs_missing_km ? <span className="pill pill--err">{s.missing(r.legs_missing_km)}</span> : null}
                      {(r.questions || []).map((q) => (
                        <div key={`${q.kind}-${q.leg_id || ''}`} data-testid={`emissions-ask-${r.trip_id}`}>
                          <span className="pill pill--warn">{s.ask}</span> {lang === 'en' ? q.text_en : q.text_sv}
                        </div>
                      ))}
                      {!r.legs_missing_km && !(r.questions || []).length ? '—' : null}
                      <CarInfo car={r.admin.company_car_info_only} s={s} />
                    </td>
                    <td>
                      <button type="button" className="btn" onClick={() => setOpen(open === r.trip_id ? null : r.trip_id)}
                        data-testid={`emissions-open-${r.trip_id}`}>
                        {open === r.trip_id ? s.hide : s.show}
                      </button>
                    </td>
                  </tr>
                  {open === r.trip_id ? (
                    <tr><td colSpan={ADMIN_KEYS.length + 4}>
                      <TripLegs tripId={r.trip_id} s={s} onChanged={() => setTick((t) => t + 1)} />
                    </td></tr>
                  ) : null}
                </Fragment>
              ))}
              <tr data-testid="emissions-total">
                <td colSpan={2}><strong>{s.total} ({data.trips})</strong></td>
                {ADMIN_KEYS.map((k) => <td key={k} className="mono"><strong>{admin[k] ? num(admin[k]) : '—'}</strong></td>)}
                <td className="nv-small">{admin.legs_missing_km ? s.missing(admin.legs_missing_km) : '—'}</td>
                <td />
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
