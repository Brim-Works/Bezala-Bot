import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';

function formatString(template, params) {
  if (!template) return '';
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    params && params[key] != null ? params[key] : '',
  );
}

function dateRangeLabel(start, end) {
  if (!start || !end) return '—';
  return `${start} – ${end}`;
}

export default function TripSuggestionCard({
  trip,
  onAccept,
  onReject,
  onEdit,
  onShow,
}) {
  const { t, lang } = useI18n();
  const totalLabel = fmtAmount(trip.total_amount, trip.base_currency || 'EUR', lang);
  const receiptsCount = (trip.messages || []).length;

  return (
    <article
      className="card card-pad trip-card trip-card--suggestion"
      data-testid={`trip-suggestion-${trip.id}`}
    >
      <header className="trip-card__head">
        <div className="trip-card__title-block">
          <span className="pill pill--accent mono">
            {t.trips.aiSuggestionLabel}
          </span>
          <h4 className="trip-card__title">{trip.title}</h4>
          <div className="trip-card__meta mono muted">
            {dateRangeLabel(trip.start_date, trip.end_date)}
            {trip.destination ? ` · ${trip.destination}` : ''}
          </div>
        </div>
        <div className="trip-card__totals">
          <div
            className="pill pill--muted mono"
            data-testid={`trip-confidence-${trip.id}`}
          >
            {formatString(t.trips.confidenceLabel, {
              percent: trip.ai_confidence ?? 0,
            })}
          </div>
          <div className="trip-card__amount mono">{totalLabel}</div>
          <div className="trip-card__count mono muted">
            {formatString(t.trips.receiptsCount, { count: receiptsCount })}
          </div>
        </div>
      </header>

      {trip.description ? (
        <p className="trip-card__description">{trip.description}</p>
      ) : null}

      <ul className="trip-card__receipts">
        {(trip.messages || []).slice(0, 5).map((m) => (
          <li key={m.message_id} className="trip-card__receipt">
            <span className="vchip-mini">{m.vendor || '—'}</span>
            <span className="mono muted">{m.receipt_date || '—'}</span>
            <span className="mono">
              {fmtAmount(m.amount, m.currency, lang)}
            </span>
          </li>
        ))}
        {receiptsCount > 5 ? (
          <li className="trip-card__receipt muted mono">
            +{receiptsCount - 5}
          </li>
        ) : null}
      </ul>

      <footer className="trip-card__actions">
        <button
          type="button"
          className="btn primary"
          onClick={onAccept}
          data-testid={`trip-accept-${trip.id}`}
        >
          ✓ {t.trips.accept}
        </button>
        <button
          type="button"
          className="btn"
          onClick={onReject}
          data-testid={`trip-reject-${trip.id}`}
        >
          ✗ {t.trips.reject}
        </button>
        <button
          type="button"
          className="btn"
          onClick={onEdit}
          data-testid={`trip-edit-${trip.id}`}
        >
          ✏ {t.trips.edit}
        </button>
        <button
          type="button"
          className="btn ghost"
          onClick={onShow}
          data-testid={`trip-show-${trip.id}`}
        >
          {t.trips.show}
        </button>
      </footer>
    </article>
  );
}
