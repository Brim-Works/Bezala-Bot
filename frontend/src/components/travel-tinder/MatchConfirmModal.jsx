import { useEffect } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';

/* Bekräftelsemodal — när användaren klickar ett okopplat kvitto eller
 * Tinder-kortets Match-knapp. Esc/click-utanför stänger.
 *
 * Snabb dubbel-klick debouncas implicit av att "loading"-flaggan
 * disablar Confirm — modal hålls öppen tills POST returnerar. */
export default function MatchConfirmModal({
  payment,
  message,
  onCancel,
  onConfirm,
  loading,
}) {
  const { t, lang } = useI18n();

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && !loading) onCancel();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel, loading]);

  const receiptLabel =
    (message?.vendor || message?.file_name || '') +
    (message?.amount != null
      ? ' (' + fmtAmount(message.amount, message.currency, lang) + ')'
      : '');
  const paymentLabel =
    (payment?.description || '') +
    (payment?.amount != null
      ? ' (' + fmtAmount(payment.amount, payment.currency, lang) + ')'
      : '');

  return (
    <div
      className="tt-modal-overlay"
      role="presentation"
      onClick={() => {
        if (!loading) onCancel();
      }}
      data-testid="tt-modal-overlay"
    >
      <div
        className="tt-modal"
        role="dialog"
        aria-modal="true"
        aria-label={t.travelTinder.confirmTitle}
        onClick={(e) => e.stopPropagation()}
        data-testid="tt-modal"
      >
        <h2 className="tt-modal__title">{t.travelTinder.confirmTitle}</h2>
        <p className="tt-modal__body">
          {t.travelTinder.confirmBody
            .replace('{receipt}', receiptLabel)
            .replace('{payment}', paymentLabel)}
        </p>
        <div className="tt-modal__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onCancel}
            disabled={loading}
            data-testid="tt-modal-cancel"
          >
            {t.travelTinder.confirmCancel}
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={onConfirm}
            disabled={loading}
            data-testid="tt-modal-confirm"
          >
            {loading ? t.match.matching : t.travelTinder.confirmCouple}
          </button>
        </div>
      </div>
    </div>
  );
}
