import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import CalendarTripCard from './CalendarTripCard.jsx';
import { cityLabel, fill, fmtRange, sumByCurrency } from './tripFormat.js';

/* C56 — en kalenderresa som en hopfälld rad: stad, datum, belopp, status,
 * km, checklista. Netvisor-texten och Godkänn står på raden; dagar,
 * checklista och kvitton visas först när man klickar. */

function statusPill(status) {
  if (status === 'approved') return 'pill pill--ok mono';
  if (status === 'edited') return 'pill pill--warn mono';
  return 'pill pill--accent mono';
}

export default function CalendarTripRow({ trip, nvText, km, onApprove, onUnapprove }) {
  const { t, lang } = useI18n();
  const c = t.trips.calendar;
  const [open, setOpen] = useState(false);
  const amount = sumByCurrency(trip.receipts, lang);
  const comp = trip.completeness;
  const approved = trip.status === 'approved';

  return (
    <article className="card trip-row" data-testid={`trip-row-${trip.id}`}>
      <div className="trip-row__main">
        <button
          type="button"
          className="trip-row__toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          data-testid={`trip-row-toggle-${trip.id}`}
        >
          <span className="trip-row__chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
          <span className="trip-row__city">
            {cityLabel(c, trip.destination) || c.unknownDestination}
          </span>
          <span className="trip-row__date mono muted">
            {fmtRange(trip.start_date, trip.end_date, lang)}
          </span>
        </button>
        <span className="trip-row__amount mono">{amount || <span className="muted">{t.trips.noAmount}</span>}</span>
        <span className="trip-row__pills">
          <span className={statusPill(trip.status)} data-testid={`trip-row-status-${trip.id}`}>
            {c[trip.status] || trip.status}
          </span>
          {km ? (
            km.missing ? (
              <span className="pill pill--err mono" data-testid={`trip-row-km-${trip.id}`}>
                {t.trips.kmMissing}
              </span>
            ) : (
              <span className="pill pill--muted mono" data-testid={`trip-row-km-${trip.id}`}>
                {fill(t.trips.kmValue, { km: Math.round(km.km).toLocaleString(lang === 'en' ? 'en-GB' : 'sv-SE') })}
              </span>
            )
          ) : null}
          {comp ? (
            <span
              className={comp.status === 'complete' ? 'pill pill--ok mono' : 'pill pill--err mono'}
              data-testid={`calendar-trip-completeness-${trip.id}`}
            >
              {comp.status === 'complete'
                ? c.check.complete
                : fill(c.check.incomplete, { count: comp.missing })}
            </span>
          ) : null}
        </span>
        <span className="trip-row__actions">
          {approved ? null : (
            <button
              type="button"
              className="btn primary btn-sm"
              onClick={() => onApprove(trip, nvText)}
              data-testid={`trip-approve-${trip.id}`}
            >
              ✓ {t.trips.approve}
            </button>
          )}
        </span>
      </div>
      {nvText ? (
        <div className="trip-row__nv mono" data-testid={`trip-row-nv-${trip.id}`}>
          <span className="muted">{t.trips.netvisorText}:</span> {nvText}
        </div>
      ) : null}
      {open ? (
        <div className="trip-row__details">
          <CalendarTripCard trip={trip} embedded />
          {approved && onUnapprove ? (
            <div className="trip-card__actions">
              <button
                type="button"
                className="btn"
                onClick={() => onUnapprove(trip)}
                data-testid={`trip-unapprove-${trip.id}`}
              >
                {t.trips.unapprove}
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}
