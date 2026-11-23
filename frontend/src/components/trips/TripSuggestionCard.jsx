import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';
import { fill, fmtRange, suggestionCity } from './tripFormat.js';

/* C56 — AI-förslag (kvittogrupp) som en hopfälld rad. Acceptera/Avvisa
 * står på samma rad som rubriken; beskrivning, kvitton, Justera och Visa
 * syns först när man klickar. */

function warningText(t, w, lang) {
  const [kind, vendor, amount] = String(w).split(':');
  const amt = fmtAmount(amount, null, lang);
  if (kind === 'same_amount_twice') return fill(t.trips.warnSameAmountTwice, { vendor, amount: amt });
  if (kind === 'tiny_amount') return fill(t.trips.warnTinyAmount, { vendor, amount: amt });
  return w;
}

export default function TripSuggestionCard({
  trip,
  onAccept,
  onReject,
  onEdit,
  onShow,
}) {
  const { t, lang } = useI18n();
  const [open, setOpen] = useState(false);
  const c = t.trips.calendar;
  const messages = trip.messages || [];
  const receiptsCount = trip.receipt_count ?? messages.length;
  const totalLabel = fmtAmount(trip.total_amount, trip.base_currency || trip.currency || 'EUR', lang);
  const warnings = trip.receipt_warnings || [];
  const dupes = (trip.duplicates || []).length;

  return (
    <article
      className="card trip-row trip-row--suggestion"
      data-testid={`trip-suggestion-${trip.id}`}
    >
      <div className="trip-row__main">
        <button
          type="button"
          className="trip-row__toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          data-testid={`trip-suggestion-toggle-${trip.id}`}
        >
          <span className="trip-row__chevron" aria-hidden="true">{open ? '▾' : '▸'}</span>
          <span className="trip-row__city">{suggestionCity(c, trip)}</span>
          <span className="trip-row__date mono muted">
            {fmtRange(trip.start_date, trip.end_date, lang)}
          </span>
        </button>
        <span className="trip-row__amount mono">{totalLabel}</span>
        <span className="trip-row__pills">
          <span className="pill pill--accent mono">{t.trips.aiSuggestionLabel}</span>
          <span className="pill pill--muted mono">
            {fill(t.trips.receiptsCount, { count: receiptsCount })}
          </span>
          <span className="pill pill--muted mono" data-testid={`trip-confidence-${trip.id}`}>
            {fill(t.trips.confidenceLabel, { percent: trip.ai_confidence ?? 0 })}
          </span>
          {dupes ? (
            <span className="pill pill--muted mono" data-testid={`trip-duplicates-${trip.id}`}>
              {fill(t.trips.duplicatesMerged, { count: dupes })}
            </span>
          ) : null}
          {warnings.length ? (
            <span className="pill pill--warn mono" title={warnings.map((w) => warningText(t, w, lang)).join('\n')}>
              ⚠ {warnings.length}
            </span>
          ) : null}
        </span>
        <span className="trip-row__actions">
          <button
            type="button"
            className="btn primary btn-sm"
            onClick={onAccept}
            data-testid={`trip-accept-${trip.id}`}
          >
            ✓ {t.trips.accept}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onReject}
            data-testid={`trip-reject-${trip.id}`}
          >
            ✗ {t.trips.reject}
          </button>
        </span>
      </div>

      {open ? (
        <div className="trip-row__details">
          {trip.description ? (
            <p className="trip-card__description">{trip.description}</p>
          ) : null}
          {warnings.length ? (
            <ul className="trip-row__warnings">
              {warnings.map((w) => (
                <li key={w} className="muted">⚠ {warningText(t, w, lang)}</li>
              ))}
            </ul>
          ) : null}
          <ul className="trip-card__receipts">
            {messages.map((m) => (
              <li key={m.message_id} className="trip-card__receipt">
                <span className="vchip-mini">{m.vendor || '—'}</span>
                <span className="mono muted">{m.receipt_date || '—'}</span>
                <span className="mono">{fmtAmount(m.amount, m.currency, lang)}</span>
              </li>
            ))}
          </ul>
          <div className="trip-card__actions">
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
          </div>
        </div>
      ) : null}
    </article>
  );
}
