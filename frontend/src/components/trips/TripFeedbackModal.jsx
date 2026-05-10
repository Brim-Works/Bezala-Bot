import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';

const FEEDBACK_TYPES = [
  { id: 'wrong_grouping', labelKey: 'feedbackWrongGrouping' },
  { id: 'missing_receipts', labelKey: 'feedbackMissingReceipts' },
  { id: 'wrong_dates', labelKey: 'feedbackWrongDates' },
  { id: 'wrong_destination', labelKey: 'feedbackWrongDestination' },
];

export default function TripFeedbackModal({ trip, onClose, onSave }) {
  const { t } = useI18n();
  const [selected, setSelected] = useState('wrong_grouping');
  const [comment, setComment] = useState('');
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSave({
        feedback_type: selected,
        details: comment ? { comment } : {},
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="modal-shell"
      role="dialog"
      aria-label={t.trips.feedbackTitle}
      data-testid={`trip-feedback-modal-${trip.id}`}
    >
      <form className="modal-card card-pad" onSubmit={submit}>
        <h3 className="modal-card__title">{t.trips.feedbackTitle}</h3>

        <fieldset className="form-row">
          <legend className="muted">{trip.title}</legend>
          {FEEDBACK_TYPES.map((opt) => (
            <label key={opt.id} className="radio-row">
              <input
                type="radio"
                name="trip-feedback"
                value={opt.id}
                checked={selected === opt.id}
                onChange={() => setSelected(opt.id)}
                data-testid={`trip-feedback-option-${opt.id}`}
              />
              <span>{t.trips[opt.labelKey]}</span>
            </label>
          ))}
        </fieldset>

        <label className="form-row">
          <span>{t.trips.feedbackComment}</span>
          <textarea
            rows={2}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            data-testid="trip-feedback-comment"
          />
        </label>

        <footer className="modal-card__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onClose}
            disabled={saving}
            data-testid="trip-feedback-cancel"
          >
            {t.trips.editCancel}
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={saving}
            data-testid="trip-feedback-submit"
          >
            {saving ? t.trips.saving : t.trips.feedbackSubmit}
          </button>
        </footer>
      </form>
    </div>
  );
}
