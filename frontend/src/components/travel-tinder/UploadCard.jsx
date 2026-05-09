import { useI18n } from '../../i18n/useI18n.jsx';

/* Manuell upload-card. Manuell upload är FAS 7-funktionalitet och
 * implementeras inte i denna FAS — knappen är disabled med tooltip. */
export default function UploadCard({ payment }) {
  const { t } = useI18n();
  const label = payment
    ? t.travelTinder.uploadForSelected
    : t.travelTinder.uploadManual;

  return (
    <button
      type="button"
      className="tt-upload-card"
      disabled
      title={t.travelTinder.uploadComingSoon}
      data-testid="tt-upload-card"
    >
      <span className="tt-upload-card__icon" aria-hidden="true">📷</span>
      <span className="tt-upload-card__label">{label}</span>
      <span className="muted mono tt-upload-card__hint">
        {t.travelTinder.uploadComingSoon}
      </span>
    </button>
  );
}
