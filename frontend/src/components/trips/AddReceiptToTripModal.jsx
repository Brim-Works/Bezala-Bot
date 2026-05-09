import { useEffect, useMemo, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { api } from '../../api/client.js';
import { fmtAmount } from '../../lib/format.js';

/* Visar alla kvitton (ProcessedMessage) som inte redan finns i resan
 * och låter användaren välja flera. Hämtar alla messages via
 * /api/messages och filtrerar lokalt — sparar en endpoint, går snabbt
 * eftersom listan typiskt är några hundra rader. */

export default function AddReceiptToTripModal({ trip, onClose, onSave }) {
  const { t, lang } = useI18n();
  const [allMessages, setAllMessages] = useState([]);
  const [selected, setSelected] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .messages(500)
      .then((data) => {
        if (!cancelled) {
          setAllMessages(Array.isArray(data) ? data : []);
        }
      })
      .catch(() => {
        if (!cancelled) setAllMessages([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tripMessageIds = useMemo(
    () => new Set((trip.messages || []).map((m) => m.message_id)),
    [trip.messages],
  );

  const candidates = useMemo(
    () =>
      allMessages.filter(
        (m) => m.message_id && !tripMessageIds.has(m.message_id),
      ),
    [allMessages, tripMessageIds],
  );

  const toggle = (messageId) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(messageId)) next.delete(messageId);
      else next.add(messageId);
      return next;
    });
  };

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSave([...selected]);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-label={t.trips.addReceiptTitle}
      data-testid={`trip-add-receipt-modal-${trip.id}`}
    >
      <form className="modal-card modal-card--wide card-pad" onSubmit={submit}>
        <h3 className="modal-card__title">{t.trips.addReceiptTitle}</h3>

        {loading ? (
          <p className="muted">{t.common.loading}</p>
        ) : candidates.length === 0 ? (
          <p className="muted" data-testid="trip-add-receipt-empty">
            {t.trips.addReceiptEmpty}
          </p>
        ) : (
          <ul className="trip-add-list">
            {candidates.map((m) => {
              const isChecked = selected.has(m.message_id);
              return (
                <li key={m.message_id} className="trip-add-list__row">
                  <label className="trip-add-list__label">
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => toggle(m.message_id)}
                      data-testid={`trip-add-receipt-option-${m.message_id}`}
                    />
                    <span className="trip-add-list__vendor">
                      {m.vendor || '—'}
                    </span>
                    <span className="muted mono">
                      {m.receipt_date || '—'}
                    </span>
                    <span className="mono">
                      {fmtAmount(m.amount, m.currency, lang)}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        )}

        <footer className="modal-card__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onClose}
            disabled={saving}
            data-testid="trip-add-receipt-cancel"
          >
            {t.trips.editCancel}
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={saving || selected.size === 0}
            data-testid="trip-add-receipt-apply"
          >
            {saving ? t.trips.saving : t.trips.addReceiptApply}
          </button>
        </footer>
      </form>
    </div>
  );
}
