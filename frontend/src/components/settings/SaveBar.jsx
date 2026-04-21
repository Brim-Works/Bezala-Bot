import { useI18n } from '../../i18n/useI18n.jsx';

/* Sticky sparrad. Visas bara när formuläret är dirty, men tar upp
 * plats hela tiden för att undvika CLS när den dyker upp. */
export default function SaveBar({ dirty, saving, onSave, onReset }) {
  const { t } = useI18n();
  return (
    <div
      className={`save-bar ${dirty ? 'save-bar--dirty' : ''}`}
      data-testid="save-bar"
    >
      <div className="save-bar__status">
        {dirty ? (
          <span className="save-bar__dirty">
            <span className="pill__dot" aria-hidden="true" />
            {t.settings.unsaved}
          </span>
        ) : (
          <span className="muted">{t.settings.allSaved}</span>
        )}
      </div>
      <div className="save-bar__actions">
        <button
          type="button"
          className="btn ghost"
          onClick={onReset}
          disabled={!dirty || saving}
        >
          {t.settings.discard}
        </button>
        <button
          type="button"
          className="btn primary"
          onClick={onSave}
          disabled={!dirty || saving}
          data-testid="save-settings"
        >
          {saving ? t.settings.saving : t.settings.save}
        </button>
      </div>
    </div>
  );
}
