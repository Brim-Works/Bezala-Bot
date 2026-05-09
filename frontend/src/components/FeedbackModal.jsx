import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { fmtAmount } from '../lib/format.js';

const FIELDS = ['vendor', 'receipt_date', 'amount', 'category'];

const TEST_IDS = {
  vendor: 'feedback-field-vendor',
  receipt_date: 'feedback-field-date',
  amount: 'feedback-field-amount',
  category: 'feedback-field-category',
};

export default function FeedbackModal({ open, onClose, onSaved, messageId, message }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [kind, setKind] = useState('field_error');
  const [checked, setChecked] = useState(() => new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) {
      setChecked(new Set());
      setKind('field_error');
    }
  }, [open]);

  if (!open) return null;

  const toggle = (field) => {
    setChecked((s) => {
      const next = new Set(s);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  };

  const onSave = async () => {
    if (saving) return;
    setSaving(true);
    try {
      if (kind === 'not_a_receipt') {
        await api.feedbackNotAReceipt({ messageId });
        toast.show({
          kind: 'ok',
          message: t.feedback.modal.notReceiptToast,
        });
        onSaved?.('not_a_receipt');
      } else {
        await api.feedbackThumbs({
          messageId,
          isPositive: false,
          fields: Array.from(checked),
        });
        toast.show({ kind: 'ok', message: t.feedback.modal.saved });
        onSaved?.('field_error');
      }
    } catch (err) {
      toast.show({ kind: 'err', message: String(err?.message || err) });
    } finally {
      setSaving(false);
    }
  };

  const renderValue = (field) => {
    if (!message) return '—';
    if (field === 'vendor') return message.vendor || '—';
    if (field === 'receipt_date') return message.receipt_date || '—';
    if (field === 'amount') {
      return message.amount != null
        ? fmtAmount(message.amount, message.currency, lang)
        : '—';
    }
    if (field === 'category') return message.category || '—';
    return '—';
  };

  const fieldLabel = (field) => {
    if (field === 'vendor') return t.feedback.modal.fieldVendor;
    if (field === 'receipt_date') return t.feedback.modal.fieldDate;
    if (field === 'amount') return t.feedback.modal.fieldAmount;
    if (field === 'category') return t.feedback.modal.fieldCategory;
    return field;
  };

  const senderForBanner = message?.sender || '';
  const notReceiptInfo = (t.feedback.modal.notReceiptInfo || '').replace(
    '{sender}', senderForBanner,
  );

  const saveLabel =
    kind === 'not_a_receipt'
      ? t.feedback.modal.saveNotReceipt
      : t.feedback.modal.saveFieldError || t.feedback.modal.save;

  return (
    <div
      className="modal-overlay"
      data-testid="feedback-modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose?.();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        data-testid="feedback-modal"
      >
        <h2 className="modal__title">{t.feedback.modal.title}</h2>
        <p className="muted">{t.feedback.modal.lead}</p>

        <div className="feedback-kind" style={{ marginBottom: '16px' }}>
          <label
            className="feedback-kind__row"
            style={{ display: 'flex', gap: '8px', alignItems: 'center' }}
          >
            <input
              type="radio"
              name="feedbackKind"
              value="field_error"
              checked={kind === 'field_error'}
              onChange={() => setKind('field_error')}
              data-testid="feedback-kind-field-error"
            />
            <span>{t.feedback.modal.kindFieldError}</span>
          </label>
          <label
            className="feedback-kind__row"
            style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '8px' }}
          >
            <input
              type="radio"
              name="feedbackKind"
              value="not_a_receipt"
              checked={kind === 'not_a_receipt'}
              onChange={() => setKind('not_a_receipt')}
              data-testid="feedback-kind-not-receipt"
            />
            <span>{t.feedback.modal.kindNotReceipt}</span>
          </label>
        </div>

        {kind === 'not_a_receipt' ? (
          <div
            className="feedback-info-banner"
            data-testid="feedback-not-receipt-info"
            style={{
              padding: '12px',
              background: 'var(--bg-info, #f0f4ff)',
              borderRadius: '6px',
              fontSize: '13px',
              marginBottom: '16px',
            }}
          >
            {notReceiptInfo}
          </div>
        ) : (
          <ul className="feedback-fields" data-testid="feedback-fields-list">
            {FIELDS.map((field) => (
              <li key={field}>
                <label className="feedback-fields__row">
                  <input
                    type="checkbox"
                    checked={checked.has(field)}
                    onChange={() => toggle(field)}
                    data-testid={TEST_IDS[field]}
                  />
                  <span className="feedback-fields__label">{fieldLabel(field)}</span>
                  <span className="feedback-fields__var muted">
                    {' '}
                    ({t.feedback.modal.wasValue} "{renderValue(field)}")
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}

        <div className="modal__footer">
          <button
            type="button"
            className="btn"
            onClick={onClose}
            disabled={saving}
            data-testid="feedback-cancel"
          >
            {t.feedback.modal.cancel}
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={onSave}
            disabled={saving}
            data-testid="feedback-save"
          >
            {saveLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
