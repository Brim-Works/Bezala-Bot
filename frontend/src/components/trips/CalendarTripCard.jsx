import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';
import { request } from '../../api/client.js';
import { receiptsChip } from './tripFormat.js';
import NetvisorPanel from '../netvisor/NetvisorPanel.jsx';

/* Resor R1 — en resa upptäckt i Google Calendar.
 * Read-only: visar tider, destination, möten, hotell och kvitton som
 * redan är kopplade till överlappande kvittoresor. Ingen redigering här —
 * C40: resor granskas/redigeras/godkänns i Claude-chatten (MCP). Kortet
 * visar status, flaggor, land och syfte så ändringarna syns.
 * C42: checklista (flyg/hotell per natt/parkering/lokal + kortrader utan
 * kvitto) — read-only, Mikko markerar i chatten.
 */

function fill(template, params) {
  if (!template) return '';
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    params && params[key] != null ? params[key] : '',
  );
}

/* C41 — databasen har engelska värden (Copenhagen, SE); UI översätter. */
function cityLabel(c, value) {
  if (!value) return value;
  return value
    .split(' → ')
    .map((part) => (c.cities && c.cities[part]) || part)
    .join(' → ');
}

function countryLabel(c, code) {
  if (!code) return '';
  return code
    .split(',')
    .map((cc) => (c.countries && c.countries[cc.trim()]) || cc.trim())
    .join(', ');
}

/* C42 — checklista: färg per status. */
const CHECK_PILL = {
  found: 'pill pill--ok mono',
  missing: 'pill pill--err mono',
  not_applicable: 'pill pill--muted mono',
  optional: 'pill pill--muted mono',
  receipt_not_available: 'pill pill--muted mono',
};

function CompletenessPill({ c, trip }) {
  const chip = receiptsChip(c.check, trip.completeness);
  if (!chip) return null;
  return (
    <span
      className={chip.cls}
      title={chip.title || undefined}
      data-testid={`calendar-trip-completeness-${trip.id}`}
    >
      {chip.text}
    </span>
  );
}

/* C58 — hotellnatt utan kvitto: Mikko väljer själv (inget förifylls).
 * Valet sparas i bottens egen tabell; inget skickas till Bezala/Netvisor. */
const NIGHT_CHOICES = [
  ['no_hotel', 'noHotel'],
  ['handled_in_bezala', 'handled'],
  ['invoiced_to_company', 'invoiced'],
  ['receipt_coming', 'coming'],
];

function HotelNightRow({ k, trip, item, onChecklist, lang }) {
  const n = k.night;
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [tips, setTips] = useState(null);
  const choose = async (choice) => {
    setBusy(true);
    setErr('');
    try {
      const res = await request(`/api/c58/calendar-trips/${trip.id}/hotel-night`, {
        method: 'POST',
        body: { night: item.slot, choice },
      });
      if (onChecklist) onChecklist(res);
    } catch (e) {
      setErr(`${n.saveFailed}: ${e && e.message ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };
  const loadTips = async () => {
    try {
      const res = await request(`/api/c58/calendar-trips/${trip.id}/hotel-tips`);
      const night = ((res && res.nights) || []).find((x) => x.night === item.slot);
      setTips((night && night.tips) || []);
    } catch (e) {
      setTips([]);
    }
  };
  const chosen = item.resolution;
  return (
    <div className="cal-trip__night" data-testid={`night-${trip.id}-${item.slot}`}>
      {chosen ? (
        <>
          <span className="sr-only" data-testid={`night-choice-${trip.id}-${item.slot}`}>{chosen}</span>
          {item.resolved_at ? (
            <span className="mono muted cal-trip__small">
              {fill(n.chosenOn, { date: item.resolved_at.slice(0, 10) })}
            </span>
          ) : null}
          {item.note ? <span className="muted cal-trip__small">{item.note}</span> : null}
          <button type="button" className="btn btn-sm" disabled={busy}
            onClick={() => choose('undo')} data-testid={`night-undo-${trip.id}-${item.slot}`}>
            {n.undo}
          </button>
        </>
      ) : (
        <>
          <span className="muted">{fill(n.question, { night: item.slot })}</span>
          <span className="cal-trip__night-buttons">
            {NIGHT_CHOICES.map(([choice, label]) => (
              <button key={choice} type="button" className="btn btn-sm" disabled={busy}
                onClick={() => choose(choice)}
                data-testid={`night-${choice}-${trip.id}-${item.slot}`}>
                {n[label]}
              </button>
            ))}
            <button type="button" className="btn btn-sm btn-ghost" onClick={loadTips}
              data-testid={`night-tips-${trip.id}-${item.slot}`}>
              {n.tips}
            </button>
          </span>
        </>
      )}
      {tips ? (
        <ul className="cal-trip__tips muted cal-trip__small" data-testid={`night-tiplist-${trip.id}-${item.slot}`}>
          {tips.length === 0 ? <li>{n.tipsNone}</li> : tips.map((x, i) => (
            <li key={i}>
              {x.source === 'bezala'
                ? fill(n.tipBezala, { id: x.bezala_transaction_id || '—', text: x.text || '',
                  amount: x.amount != null ? fmtAmount(x.amount, x.currency, lang) : '', date: x.date })
                : fill(n.tipBot, { vendor: x.vendor || '—',
                  amount: x.amount != null ? fmtAmount(x.amount, x.currency, lang) : '', date: x.date })}
            </li>
          ))}
        </ul>
      ) : null}
      {err ? <div className="err cal-trip__small">{err}</div> : null}
    </div>
  );
}

function isOpenNight(item) {
  return item.kind === 'hotel' && item.slot
    && (item.resolution || (item.status === 'missing' && item.set_by !== 'user'));
}

function Checklist({ c, trip, lang, onChecklist }) {
  const cl = trip.checklist;
  if (!cl) return null;
  const k = c.check;
  return (
    <section className="cal-trip__receipts" data-testid={`calendar-trip-checklist-${trip.id}`}>
      <div className="cal-trip__label muted mono">{k.title}</div>
      <ul className="cal-trip__checklist">
        {(cl.items || []).map((item) => (
          <li
            key={`${item.kind}-${item.slot || ''}`}
            className={`cal-trip__check cal-trip__check--${item.status}`}
            data-testid={`check-${trip.id}-${item.kind}-${item.slot || 'x'}`}
          >
            <span className={CHECK_PILL[item.status] || 'pill pill--muted mono'}>
              {item.resolution && k.night.chosen[item.resolution]
                ? k.night.chosen[item.resolution]
                : (k.status[item.status] || item.status)}
            </span>
            <span className="cal-trip__strong">{k.kind[item.kind] || item.kind}</span>
            {item.kind === 'hotel' && item.slot ? (
              <span className="mono muted">{item.slot}</span>
            ) : null}
            {item.kind === 'local' && item.slot ? (
              <span className="mono muted">{item.slot}</span>
            ) : null}
            {item.receipt && item.receipt.vendor ? (
              item.receipt.drive_link ? (
                <a href={item.receipt.drive_link} target="_blank" rel="noreferrer" className="muted">
                  {item.receipt.vendor}
                </a>
              ) : (
                <span className="muted">{item.receipt.vendor}</span>
              )
            ) : null}
            {item.set_by === 'user' && !item.resolution ? (
              <span className="mono muted cal-trip__small">{k.byMikko}{item.note ? `: ${item.note}` : ''}</span>
            ) : null}
            {isOpenNight(item) ? (
              <HotelNightRow k={k} trip={trip} item={item} onChecklist={onChecklist} lang={lang} />
            ) : null}
          </li>
        ))}
        {(cl.card_lines || []).map((line) => (
          <li
            key={`card-${line.bill_line_id}`}
            className={`cal-trip__check cal-trip__check--${line.status}`}
            data-testid={`check-${trip.id}-card-${line.bill_line_id}`}
          >
            <span className={CHECK_PILL[line.status] || 'pill pill--err mono'}>
              {k.status[line.status] || line.status}
            </span>
            <span className="cal-trip__strong">{k.cardLine}</span>
            <span className="mono muted">{line.date || '—'}</span>
            <span>{line.vendor || line.description || '—'}</span>
            <span className="mono">{fmtAmount(line.amount, line.currency, lang)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function statusPill(status) {
  if (status === 'approved') return 'pill pill--ok mono';
  if (status === 'edited') return 'pill pill--warn mono';
  return 'pill pill--accent mono';
}

/* C56: embedded = visas inuti den kompakta raden (CalendarTripRow) —
 * rubrik, status och belopp står redan på raden, här bara detaljerna. */
/* C57 — köp + återbetalning av samma bokning: räknas inte, visas hopfällt. */
export function CancelledBookings({ bookings, c, lang, testid = 'cancelled' }) {
  if (!bookings || bookings.length === 0) return null;
  return (
    <>
      {bookings.map((b) => (
        <details key={b.booking} className="trip-card__cancelled" data-testid={`${testid}-${b.booking}`}>
          <summary>
            <span className="muted">{c.cancelledTitle}</span>
            {' · '}
            <span>{b.vendor || '—'}</span>
            {b.booking_ref ? <span className="mono"> {fill(c.cancelledBooking, { ref: b.booking_ref })}</span> : null}
            {' · '}
            <span className="mono trip-card__cancelled-amount">{fmtAmount(b.amount, b.currency, lang)}</span>
          </summary>
          <div className="muted mono nv-small">
            {fill(c.cancelledDates, { buy: b.purchase_date || '—', refund: b.refund_date || '—' })}
          </div>
          <ul className="trip-card__receipts">
            {(b.receipts || []).map((r) => (
              <li key={r.message_id} className="trip-card__receipt trip-card__receipt--dup">
                <span className="vchip-mini">{r.vendor || '—'}</span>
                <span className="mono muted">{r.receipt_date || '—'}</span>
                <span className="mono">{fmtAmount(r.amount, r.currency, lang)}</span>
              </li>
            ))}
          </ul>
        </details>
      ))}
    </>
  );
}

export default function CalendarTripCard({ trip, embedded = false, onChecklist }) {
  const { t, lang } = useI18n();
  const c = t.trips.calendar;
  const days = trip.days || [];
  const receipts = trip.receipts || [];
  const range = trip.start_date === trip.end_date
    ? trip.start_date
    : `${trip.start_date} – ${trip.end_date}`;

  const body = (
    <>
    <ol className="cal-trip__days">
      {days.map((day) => (
        <li key={day.date} className="cal-trip__day" data-testid={`calendar-trip-day-${day.date}`}>
          <div className="cal-trip__day-head">
            <span className="mono cal-trip__date">{day.date}</span>
            {day.destination ? <span>{cityLabel(c, day.destination)}</span> : null}
            {day.country ? (
              <span className="mono muted">{countryLabel(c, day.country)}</span>
            ) : null}
            {day.airport && day.airport !== 'HEL' ? (
              <span className="mono muted cal-trip__small">{fill(c.airport, { code: day.airport })}</span>
            ) : null}
            {day.start_time ? (
              <span className="mono">{fill(c.departure, { time: day.start_time })}</span>
            ) : null}
            {day.end_time ? (
              <span className="mono">{fill(c.arrival, { time: day.end_time })}</span>
            ) : null}
          </div>
          {day.flight_departure || day.flight_arrival ? (
            <div className="mono muted cal-trip__small" data-testid={`calendar-trip-flight-${day.date}`}>
              {day.flight_departure && day.flight_arrival
                ? fill(c.flightTimes, { dep: day.flight_departure, arr: day.flight_arrival })
                : day.flight_departure
                  ? fill(c.flightDeparture, { time: day.flight_departure })
                  : fill(c.flightArrival, { time: day.flight_arrival })}
            </div>
          ) : null}
          {day.hotel ? (
            <div className="cal-trip__line muted">
              {c.hotel}: <span className="cal-trip__strong">{day.hotel.name}</span>
            </div>
          ) : null}
          {day.purpose ? (
            <div className="cal-trip__line muted">
              {c.purpose}: <span className="cal-trip__strong">{day.purpose}</span>
            </div>
          ) : null}
          {(day.meetings || []).length ? (
            <ul className="cal-trip__meetings">
              {day.meetings.map((m) => (
                <li key={m.id} className="cal-trip__meeting">
                  <span className="mono muted">
                    {m.start_time ? `${m.start_time}–${m.end_time || ''}` : '—'}
                  </span>
                  <span>{m.summary}</span>
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>

    <Checklist c={c} trip={trip} lang={lang} onChecklist={onChecklist} />

    {/* C49 — Netvisor-underlag (bara läsning) */}
    <NetvisorPanel tripId={trip.id} />

    <section className="cal-trip__receipts">
      <div className="cal-trip__label muted mono">{c.receiptsTitle}</div>
      {receipts.length === 0 ? (
        <div className="muted cal-trip__line">{c.noReceipts}</div>
      ) : (
        <ul className="trip-card__receipts">
          {receipts.map((r) => (
            <li
              key={r.message_id}
              className={`trip-card__receipt${r.counted === false ? ' trip-card__receipt--dup' : ''}`}
              data-testid={`calendar-trip-receipt-${r.message_id}`}
            >
              <span className="vchip-mini">
                {r.vendor || '—'}
                {r.counted === false ? <span className="muted"> · {c.countedOnce}</span> : null}
              </span>
              <span className="mono muted">{r.receipt_date || '—'}</span>
              <span className="mono">{fmtAmount(r.amount, r.currency, lang)}</span>
            </li>
          ))}
        </ul>
      )}
      <CancelledBookings bookings={trip.cancelled_bookings} c={c} lang={lang} />
    </section>
    </>
  );

  const flagPills = (
    <>
      {trip.open_return || (trip.flags || []).includes('missing_return') ? (
        <span className="pill pill--warn mono" data-testid={`calendar-trip-open-${trip.id}`}>
          {c.openReturn}
        </span>
      ) : null}
      {(trip.flags || []).includes('destination_unconfirmed') ? (
        <span className="pill pill--warn mono" data-testid={`calendar-trip-unconfirmed-${trip.id}`}>
          {c.unconfirmed}
        </span>
      ) : null}
      {(trip.flags || []).includes('needs_review') ? (
        <span className="pill pill--warn mono" data-testid={`calendar-trip-review-${trip.id}`}>
          {c.needsReview}
        </span>
      ) : null}
    </>
  );

  if (embedded) {
    return (
      <div className="cal-trip cal-trip--embedded" data-testid={`calendar-trip-${trip.id}`}>
        <div className="cal-trip__pills">{flagPills}</div>
        <div className="trip-card__meta mono muted">
          {range}{trip.country ? ` · ${countryLabel(c, trip.country)}` : ''}
          {' · '}
          {days.length === 1 ? c.dayCount : fill(c.daysCount, { count: days.length })}
        </div>
        {trip.purpose ? (
          <div className="cal-trip__line" data-testid={`calendar-trip-purpose-${trip.id}`}>
            {c.purpose}: <span className="cal-trip__strong">{trip.purpose}</span>
          </div>
        ) : null}
        {body}
      </div>
    );
  }

  return (
    <article
      className="card card-pad trip-card cal-trip"
      data-testid={`calendar-trip-${trip.id}`}
    >
      <header className="trip-card__head">
        <div className="trip-card__title-block">
          <div className="cal-trip__pills">
            <span className={statusPill(trip.status)}>
              {c[trip.status] || trip.status}
            </span>
            {trip.open_return || (trip.flags || []).includes('missing_return') ? (
              <span className="pill pill--warn mono" data-testid={`calendar-trip-open-${trip.id}`}>
                {c.openReturn}
              </span>
            ) : null}
            {(trip.flags || []).includes('destination_unconfirmed') ? (
              <span className="pill pill--warn mono" data-testid={`calendar-trip-unconfirmed-${trip.id}`}>
                {c.unconfirmed}
              </span>
            ) : null}
            {(trip.flags || []).includes('needs_review') ? (
              <span className="pill pill--warn mono" data-testid={`calendar-trip-review-${trip.id}`}>
                {c.needsReview}
              </span>
            ) : null}
            <CompletenessPill c={c} trip={trip} />
          </div>
          <h4 className="trip-card__title">
            {cityLabel(c, trip.destination) || c.unknownDestination}
          </h4>
          <div className="trip-card__meta mono muted">
            {range}{trip.country ? ` · ${countryLabel(c, trip.country)}` : ''}
          </div>
          {trip.purpose ? (
            <div className="cal-trip__line" data-testid={`calendar-trip-purpose-${trip.id}`}>
              {c.purpose}: <span className="cal-trip__strong">{trip.purpose}</span>
            </div>
          ) : null}
        </div>
        <div className="trip-card__totals">
          <div className="pill pill--muted mono">
            {days.length === 1 ? c.dayCount : fill(c.daysCount, { count: days.length })}
          </div>
        </div>
      </header>

      {body}
    </article>
  );
}
