import { useState } from 'react';
import { api, ApiError } from '../../api/client.js';
import { useI18n } from '../../i18n/useI18n.jsx';
import { useToast } from '../../lib/toast.jsx';

/* Cleanup-PR — "Städa befintliga resor".
 *
 * Vendor-listan ägs av ExcludedVendorsSection (FAS 11.1.1) och lagras i
 * `excluded_vendors`-tabellen. Den här sektionen lägger till en
 * permanent städning-knapp som tar bort kvitton från redan-grupperade
 * resor när vendor matchar någon excluded-pattern.
 *
 * Reaktiv filtrering (när du öppnar Resor-vyn) körs redan automatiskt;
 * knappen behövs bara för att frigöra DB-rader och radera tomma resor.
 */
export default function TripCleanupSection() {
  const { t } = useI18n();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const onCleanup = async () => {
    setBusy(true);
    try {
      const result = await api.cleanupExcludedVendors();
      const msg = t.settings.cleanupTrips.successToast
        .replace('{count}', String(result.removed_messages ?? 0))
        .replace('{trips}', String(result.affected_trips ?? 0));
      toast.show({ kind: 'ok', message: msg });
      setConfirming(false);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.settings.cleanupTrips.failed}: ${detail}`,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="settings-section" data-testid="trip-cleanup">
      <header className="settings-section__head">
        <h2 className="settings-section__title">
          {t.settings.cleanupTrips.title}
        </h2>
        <p className="settings-section__lead muted">
          {t.settings.cleanupTrips.lead}
        </p>
      </header>

      <div className="settings-cleanup-row">
        <div className="settings-cleanup-row__text">
          <strong>{t.settings.cleanupTrips.actionTitle}</strong>
          <span className="muted">{t.settings.cleanupTrips.helpText}</span>
        </div>
        <button
          type="button"
          className="btn ghost"
          onClick={() => setConfirming(true)}
          disabled={busy}
          data-testid="trip-cleanup-trigger"
        >
          {t.settings.cleanupTrips.button}
        </button>
      </div>

      {confirming ? (
        <div
          className="modal-shell"
          role="dialog"
          aria-modal="true"
          aria-label={t.settings.cleanupTrips.confirmTitle}
          onClick={(e) => {
            if (e.target === e.currentTarget && !busy) setConfirming(false);
          }}
          data-testid="trip-cleanup-confirm"
        >
          <div className="modal-card card-pad">
            <h3 className="modal-card__title">
              {t.settings.cleanupTrips.confirmTitle}
            </h3>
            <p className="muted">{t.settings.cleanupTrips.confirmText}</p>
            <footer className="modal-card__actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setConfirming(false)}
                disabled={busy}
              >
                {t.common.cancel}
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={onCleanup}
                disabled={busy}
                data-testid="trip-cleanup-confirm-btn"
              >
                {busy
                  ? t.settings.cleanupTrips.busy
                  : t.settings.cleanupTrips.confirmAction}
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </section>
  );
}
