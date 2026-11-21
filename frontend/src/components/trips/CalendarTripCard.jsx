import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';

/* Resor R1 — en resa upptäckt i Google Calendar.
 * Read-only: visar tider, destination, möten, hotell och kvitton som
 * redan är kopplade till överlappande kvittoresor. Ingen redigering här —
 * C40: resor granskas/redigeras/godkänns i Claude-chatten (MCP). Kortet
 * visar status, flaggor, land och syfte så ändringarna syns.
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

function statusPill(status) {
  if (status === 'approved') return 'pill pill--ok mono';
  if (status === 'edited') return 'pill pill--warn mono';
  return 'pill pill--accent mono';
}

export default function CalendarTripCard({ trip }) {
  const { t, lang } = useI18n();
  const c = t.trips.calendar;
  const days = trip.days || [];
  const receipts = trip.receipts || [];
  const range = trip.start_date === trip.end_date
    ? trip.start_date
    : `${trip.start_date} – ${trip.end_date}`;

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

      <section className="cal-trip__receipts">
        <div className="cal-trip__label muted mono">{c.receiptsTitle}</div>
        {receipts.length === 0 ? (
          <div className="muted cal-trip__line">{c.noReceipts}</div>
        ) : (
          <ul className="trip-card__receipts">
            {receipts.map((r) => (
              <li key={r.message_id} className="trip-card__receipt">
                <span className="vchip-mini">{r.vendor || '—'}</span>
                <span className="mono muted">{r.receipt_date || '—'}</span>
                <span className="mono">{fmtAmount(r.amount, r.currency, lang)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}
