import { useEffect, useMemo, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import Confidence from '../Confidence.jsx';
import { IconSparkle } from '../../icons/index.jsx';

/* Alla 10 fält enligt användarens spec.
 *
 * Backend har inte vat_rate, project, payment_method eller user_note —
 * de renderas ändå så att UI-flödet kan testas. POST /upload-to-bezala
 * tar ingen body, så redigeringar persisteras inte i denna commit.
 * Tydlig hint i footern. BACKEND-TODO: utöka schema + endpoint. */

const CURRENCIES = ['EUR', 'SEK', 'USD', 'GBP', 'NOK', 'DKK'];
const VAT_RATES = ['0', '10', '14', '24', '25'];
const CATEGORIES = ['Flyg', 'Hotell', 'Parkering', 'Mat', 'Taxi', 'Annat'];
const PROJECTS = ['—', 'Kongressi 2026 Q2', 'Toimisto', 'Myynti', 'Asiakas'];
const PAYMENT_METHODS = ['Företagskort', 'Privat utlägg', 'Faktura'];

function buildInitial(message) {
  if (!message) return null;
  return {
    vendor: message.vendor || '',
    date: message.receipt_date || '',
    amount: message.amount != null ? String(message.amount) : '',
    currency: message.currency || 'EUR',
    vat_rate: '24',
    category: message.category || 'Annat',
    project: '—',
    payment_method: 'Företagskort',
    filename: message.file_name || '',
    note: '',
  };
}

export default function ReviewForm({
  message,
  onApprove,
  onReject,
  onSkip,
  isUploading,
}) {
  const { t } = useI18n();
  const initial = useMemo(() => buildInitial(message), [message]);
  const [form, setForm] = useState(initial);
  const [editedKeys, setEditedKeys] = useState(() => new Set());

  // När användaren byter rad — nollställ form + edit-tracking.
  useEffect(() => {
    setForm(buildInitial(message));
    setEditedKeys(new Set());
  }, [message?.id]);

  if (!message || !form) {
    return (
      <div className="form-pane form-pane--empty">
        <p className="muted">{t.review.noSelection}</p>
      </div>
    );
  }

  const update = (key, value) => {
    setForm((f) => ({ ...f, [key]: value }));
    setEditedKeys((s) => {
      const next = new Set(s);
      next.add(key);
      return next;
    });
  };

  const editedCount = editedKeys.size;

  const fieldClass = (key) => `fld ${editedKeys.has(key) ? 'fld--edited' : ''}`;

  return (
    <form
      className="form-pane"
      onSubmit={(e) => {
        e.preventDefault();
        onApprove(message);
      }}
      data-testid="review-form"
    >
      <div className="form-pane__head">
        <div>
          <div className="form-pane__vendor">{message.vendor || '—'}</div>
          <div className="form-pane__sub">
            <IconSparkle className="icon sm" />
            {t.review.aiExtracted}
            {' · '}
            <Confidence value={message.ai_confidence} />
          </div>
        </div>
      </div>

      <div className="form-pane__body">
        <div className="fld-row">
          <label className={fieldClass('vendor')}>
            <span className="fld__label">{t.review.form.vendor}</span>
            <input
              type="text"
              value={form.vendor}
              onChange={(e) => update('vendor', e.target.value)}
            />
          </label>
          <label className={fieldClass('date')}>
            <span className="fld__label">{t.review.form.date}</span>
            <input
              type="date"
              value={form.date}
              onChange={(e) => update('date', e.target.value)}
            />
          </label>
        </div>

        <div className="fld-row fld-row--3">
          <label className={fieldClass('amount')}>
            <span className="fld__label">{t.review.form.amount}</span>
            <input
              type="text"
              inputMode="decimal"
              className="mono"
              value={form.amount}
              onChange={(e) => update('amount', e.target.value)}
            />
          </label>
          <label className={fieldClass('currency')}>
            <span className="fld__label">{t.review.form.currency}</span>
            <select
              value={form.currency}
              onChange={(e) => update('currency', e.target.value)}
            >
              {CURRENCIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className={fieldClass('vat_rate')}>
            <span className="fld__label">{t.review.form.vatRate}</span>
            <select
              value={form.vat_rate}
              onChange={(e) => update('vat_rate', e.target.value)}
            >
              {VAT_RATES.map((v) => (
                <option key={v} value={v}>
                  {v} %
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="fld-row">
          <label className={fieldClass('category')}>
            <span className="fld__label">{t.review.form.category}</span>
            <select
              value={form.category}
              onChange={(e) => update('category', e.target.value)}
            >
              {CATEGORIES.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className={fieldClass('project')}>
            <span className="fld__label">{t.review.form.project}</span>
            <select
              value={form.project}
              onChange={(e) => update('project', e.target.value)}
            >
              {PROJECTS.map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>
        </div>

        <label className={fieldClass('payment_method')}>
          <span className="fld__label">{t.review.form.paymentMethod}</span>
          <select
            value={form.payment_method}
            onChange={(e) => update('payment_method', e.target.value)}
          >
            {PAYMENT_METHODS.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        </label>

        <label className={fieldClass('filename')}>
          <span className="fld__label">{t.review.form.filename}</span>
          <input
            type="text"
            className="mono fld__input--mono"
            value={form.filename}
            onChange={(e) => update('filename', e.target.value)}
          />
          <span className="fld__hint">
            <IconSparkle className="icon sm" /> {t.review.form.aiGenerated}
          </span>
        </label>

        <label className={fieldClass('note')}>
          <span className="fld__label">{t.review.form.note}</span>
          <textarea
            rows={2}
            value={form.note}
            onChange={(e) => update('note', e.target.value)}
          />
        </label>

        {editedCount > 0 ? (
          <div className="form-pane__edited" data-testid="edited-count">
            <span className="pill__dot" aria-hidden="true" />
            {editedCount} {t.review.fieldsEdited}
          </div>
        ) : null}
      </div>

      <div className="form-pane__footer">
        <div className="form-pane__footer-left">
          <button
            type="button"
            className="btn ghost"
            onClick={() => onReject(message)}
          >
            {t.review.reject}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => onSkip(message)}
          >
            {t.review.skip}
          </button>
        </div>
        <button
          type="submit"
          className="btn primary"
          disabled={isUploading}
          data-testid="approve-button"
        >
          {isUploading ? t.review.approving : t.review.approve}
        </button>
      </div>
      <div className="form-pane__disclaimer">{t.review.editHint}</div>
    </form>
  );
}
