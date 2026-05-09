import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';

function formatString(template, params) {
  if (!template) return '';
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    params && params[key] != null ? params[key] : '',
  );
}

export default function TripActiveCard({ trip, onShow }) {
  const { t, lang } = useI18n();
  const totalLabel = fmtAmount(
    trip.total_amount,
    trip.base_currency || 'EUR',
    lang,
  );
  const count = (trip.messages || []).length;
  const dateLabel = trip.start_date && trip.end_date
    ? `${trip.start_date} – ${trip.end_date}`
    : '—';

  return (
    <button
      type="button"
      className="card card-pad trip-card trip-card--active"
      onClick={onShow}
      data-testid={`trip-active-${trip.id}`}
    >
      <div className="trip-card__head">
        <div className="trip-card__title-block">
          <h4 className="trip-card__title">{trip.title}</h4>
          <div className="trip-card__meta mono muted">
            {dateLabel}
            {trip.destination ? ` · ${trip.destination}` : ''}
          </div>
        </div>
        <div className="trip-card__totals">
          <div className="trip-card__amount mono">{totalLabel}</div>
          <div className="trip-card__count mono muted">
            {formatString(t.trips.receiptsCount, { count })}
          </div>
        </div>
      </div>
    </button>
  );
}
