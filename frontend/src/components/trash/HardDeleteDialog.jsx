import { useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';

/* Hard-delete (permanent) — dubbel-confirm utan undo-möjlighet.
 * Inkluderar toggle för "radera även från Drive" (default av). */
export default function HardDeleteDialog({
  open,
  count = 1,
  onCancel,
  onConfirm,
  busy,
  mode = 'row', // 'row' | 'empty-trash'
}) {
  const { t } = useI18n();
  const [purgeDrive, setPurgeDrive] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    setPurgeDrive(false);
    function onKey(e) {
      if (e.key === 'Escape') onCancel?.();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!open) return null;

  const title =
    mode === 'empty-trash'
      ? t.trash.hardDialog.titleEmpty.replace('{count}', String(count))
      : count > 1
        ? t.trash.hardDialog.titleMany.replace('{count}', String(count))
        : t.trash.hardDialog.titleOne;

  return (
    <>
      <div className="modal-overlay" onClick={onCancel} aria-hidden="true" />
      <div
        className="modal modal--danger"
        role="dialog"
        aria-modal="true"
        aria-labelledby="hard-dialog-title"
        data-testid="hard-delete-dialog"
      >
        <h2 id="hard-dialog-title" className="modal__title">{title}</h2>
        <p className="modal__body">{t.trash.hardDialog.body}</p>
        <label className="modal__purge-toggle">
          <input
            type="checkbox"
            checked={purgeDrive}
            onChange={(e) => setPurgeDrive(e.target.checked)}
            data-testid="purge-drive-toggle"
          />
          <span>
            {t.trash.hardDialog.purgeDrive}
            <span className="muted modal__reason-hint">
              {t.trash.hardDialog.purgeDriveHint}
            </span>
          </span>
        </label>
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
            className="btn danger"
            onClick={() => onConfirm?.({ purge_drive: purgeDrive })}
            disabled={busy}
            data-testid="confirm-hard-delete"
          >
            {busy
              ? t.trash.hardDialog.deleting
              : mode === 'empty-trash'
                ? t.trash.hardDialog.confirmEmpty
                : t.trash.hardDialog.confirm}
          </button>
        </div>
      </div>
    </>
  );
}
