import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';

export default function TripDetailDrawer({
  trip,
  onClose,
  onEdit,
  onArchive,
  onAddReceipt,
  onRemoveReceipt,
  onGoodFeedback,
  onBadFeedback,
}) {
  const { t, lang } = useI18n();
  const totalLabel = fmtAmount(
    trip.total_amount,
    trip.base_currency || 'EUR',
    lang,
  );

  return (
    <div
      className="trip-drawer"
      role="dialog"
      aria-label={t.trips.detailTitle}
      data-testid={`trip-drawer-${trip.id}`}
    >
      <div
        className="trip-drawer__backdrop"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside className="trip-drawer__panel">
        <header className="trip-drawer__head">
          <div>
            <h3 className="trip-drawer__title">{trip.title}</h3>
            <div className="muted mono">
              {trip.start_date} – {trip.end_date}
              {trip.destination ? ` · ${trip.destination}` : ''}
            </div>
          </div>
          <button
            type="button"
            className="btn ghost"
            onClick={onClose}
            aria-label={t.trips.closeDetail}
            data-testid="trip-drawer-close"
          >
            ✕
          </button>
        </header>

        <div className="trip-drawer__totals mono">{totalLabel}</div>

        {trip.description ? (
          <p className="trip-drawer__description">{trip.description}</p>
        ) : null}

        <h4 className="trip-drawer__section-title">
          {t.trips.receiptsHeader}
        </h4>
        <ul className="trip-drawer__receipts">
          {(trip.messages || []).map((m) => (
            <li
              key={m.message_id}
              className="trip-drawer__receipt"
              data-testid={`trip-drawer-receipt-${m.message_id}`}
            >
              <div className="trip-drawer__receipt-main">
                <div className="trip-drawer__receipt-vendor">
                  {m.vendor || '—'}
                </div>
                <div className="muted mono">
                  {m.receipt_date || '—'}
                  {m.category ? ` · ${m.category}` : ''}
                </div>
              </div>
              <div className="trip-drawer__receipt-side mono">
                {fmtAmount(m.amount, m.currency, lang)}
              </div>
              {trip.status !== 'archived' ? (
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => onRemoveReceipt(m.message_id)}
                  data-testid={`trip-drawer-remove-${m.message_id}`}
                  aria-label={t.trips.removeReceipt}
                  title={t.trips.removeReceipt}
                >
                  −
                </button>
              ) : null}
            </li>
          ))}
        </ul>

        <footer className="trip-drawer__actions">
          {trip.status !== 'archived' ? (
            <>
              <button
                type="button"
                className="btn"
                onClick={onEdit}
                data-testid="trip-drawer-edit"
              >
                ✏ {t.trips.edit}
              </button>
              <button
                type="button"
                className="btn"
                onClick={onAddReceipt}
                data-testid="trip-drawer-add-receipt"
              >
                + {t.trips.addReceipt}
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={onGoodFeedback}
                data-testid="trip-drawer-good-feedback"
              >
                👍 {t.trips.feedbackGood}
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={onBadFeedback}
                data-testid="trip-drawer-bad-feedback"
              >
                👎 {t.trips.feedbackBad}
              </button>
              <button
                type="button"
                className="btn ghost"
                onClick={onArchive}
                data-testid="trip-drawer-archive"
              >
                🗑 {t.trips.archive}
              </button>
            </>
          ) : null}
        </footer>
      </aside>
    </div>
  );
}
