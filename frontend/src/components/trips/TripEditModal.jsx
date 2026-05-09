import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';

export default function TripEditModal({ trip, onClose, onSave }) {
  const { t } = useI18n();
  const [title, setTitle] = useState(trip.title || '');
  const [destination, setDestination] = useState(trip.destination || '');
  const [startDate, setStartDate] = useState(trip.start_date || '');
  const [endDate, setEndDate] = useState(trip.end_date || '');
  const [description, setDescription] = useState(trip.description || '');
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setSaving(true);
    try {
      await onSave({
        title,
        destination,
        start_date: startDate,
        end_date: endDate,
        description,
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-label={t.trips.editTitle}
      data-testid={`trip-edit-modal-${trip.id}`}
    >
      <form className="modal-card card-pad" onSubmit={submit}>
        <h3 className="modal-card__title">{t.trips.editTitle}</h3>

        <label className="form-row">
          <span>{t.trips.editTitleField}</span>
          <input
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            data-testid="trip-edit-title"
          />
        </label>

        <label className="form-row">
          <span>{t.trips.editDestination}</span>
          <input
            type="text"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            data-testid="trip-edit-destination"
          />
        </label>

        <div className="form-row-grid">
          <label className="form-row">
            <span>{t.trips.editStartDate}</span>
            <input
              type="date"
              value={startDate}
              onChange={(event) => setStartDate(event.target.value)}
              data-testid="trip-edit-start-date"
            />
          </label>
          <label className="form-row">
            <span>{t.trips.editEndDate}</span>
            <input
              type="date"
              value={endDate}
              onChange={(event) => setEndDate(event.target.value)}
              data-testid="trip-edit-end-date"
            />
          </label>
        </div>

        <label className="form-row">
          <span>{t.trips.editDescription}</span>
          <textarea
            rows={3}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            data-testid="trip-edit-description"
          />
        </label>

        <footer className="modal-card__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onClose}
            disabled={saving}
            data-testid="trip-edit-cancel"
          >
            {t.trips.editCancel}
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={saving}
            data-testid="trip-edit-save"
          >
            {saving ? t.trips.saving : t.trips.editSave}
          </button>
        </footer>
      </form>
    </div>
  );
}
