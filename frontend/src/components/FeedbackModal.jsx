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
  const [checked, setChecked] = useState(() => new Set());
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) setChecked(new Set());
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
      await api.feedbackThumbs({
        messageId,
        isPositive: false,
        fields: Array.from(checked),
      });
      toast.show({ kind: 'ok', message: t.feedback.modal.saved });
      onSaved?.();
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
        <ul className="feedback-fields">
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
            {t.feedback.modal.save}
          </button>
        </div>
      </div>
    </div>
  );
}
