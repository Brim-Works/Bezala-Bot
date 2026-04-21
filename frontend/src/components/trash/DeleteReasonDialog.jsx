import { useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';

const REASONS = ['manual', 'calendar', 'spam', 'misclassified'];

/* Modal som ber om orsak innan soft-delete. Används både för en rad
 * och för bulk-delete. Esc stänger. */
export default function DeleteReasonDialog({
  open,
  count = 1,
  onCancel,
  onConfirm,
  busy,
}) {
  const { t } = useI18n();
  const [reason, setReason] = useState('manual');

  useEffect(() => {
    if (!open) return undefined;
    function onKey(e) {
      if (e.key === 'Escape') onCancel?.();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const title =
    count > 1
      ? t.trash.dialog.titleMany.replace('{count}', String(count))
      : t.trash.dialog.titleOne;

  return (
    <>
      <div className="modal-overlay" onClick={onCancel} aria-hidden="true" />
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="delete-dialog-title"
        data-testid="delete-reason-dialog"
      >
        <h2 id="delete-dialog-title" className="modal__title">{title}</h2>
        <p className="modal__body muted">{t.trash.dialog.body}</p>
        <fieldset className="modal__reasons">
          <legend className="visually-hidden">{t.trash.dialog.reasonLegend}</legend>
          {REASONS.map((key) => (
            <label key={key} className="modal__reason">
              <input
                type="radio"
                name="delete-reason"
                value={key}
                checked={reason === key}
                onChange={() => setReason(key)}
                data-testid={`reason-${key}`}
              />
              <span>
                <strong>{t.trash.reasons[key]}</strong>
                <span className="muted modal__reason-hint">
                  {t.trash.reasonHints[key]}
                </span>
              </span>
            </label>
          ))}
        </fieldset>
        <div className="modal__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onCancel}
            disabled={busy}
          >
            {t.common.cancel}
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={() => onConfirm?.(reason)}
            disabled={busy}
            data-testid="confirm-delete"
          >
            {busy ? t.trash.dialog.deleting : t.trash.dialog.confirm}
          </button>
        </div>
      </div>
    </>
  );
}
