import { useI18n } from '../../i18n/useI18n.jsx';

const INTERVALS = [
  { value: 15, labelKey: 'interval15' },
  { value: 30, labelKey: 'interval30' },
  { value: 60, labelKey: 'interval60' },
  { value: 240, labelKey: 'interval240' },
];

/* Scannings- och AI-inställningar.
 *
 * Confidence-tröskeln är en slider 0-100 — bara relevant när
 * auto-upload är på, men vi disablar inte slidern (det skulle dölja
 * värdet för användaren). Istället nedtonas hint-texten. */
export default function AutomationSection({ form, update }) {
  const { t } = useI18n();

  return (
    <section className="settings-section" data-testid="automation">
      <header className="settings-section__head">
        <h2 className="settings-section__title">{t.settings.automation.title}</h2>
        <p className="settings-section__lead muted">
          {t.settings.automation.lead}
        </p>
      </header>

      <label className="settings-field">
        <span className="settings-field__label">
          {t.settings.automation.scanInterval}
        </span>
        <select
          className="settings-field__select"
          value={form.scan_interval_minutes}
          onChange={(e) =>
            update({ scan_interval_minutes: parseInt(e.target.value, 10) })
          }
          data-testid="scan-interval"
        >
          {INTERVALS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {t.settings.automation[opt.labelKey]}
            </option>
          ))}
        </select>
      </label>

      <label className="settings-toggle">
        <input
          type="checkbox"
          checked={form.ai_naming_enabled}
          onChange={(e) => update({ ai_naming_enabled: e.target.checked })}
          data-testid="toggle-ai"
        />
        <span className="settings-toggle__label">
          {t.settings.automation.aiNaming}
          <span className="settings-toggle__sub muted">
            {t.settings.automation.aiNamingHint}
          </span>
        </span>
      </label>

      <label className="settings-toggle">
        <input
          type="checkbox"
          checked={form.auto_upload_enabled}
          onChange={(e) => update({ auto_upload_enabled: e.target.checked })}
          data-testid="toggle-auto-upload"
        />
        <span className="settings-toggle__label">
          {t.settings.automation.autoUpload}
          <span className="settings-toggle__sub muted">
            {t.settings.automation.autoUploadHint}
          </span>
        </span>
      </label>

      <div className="settings-slider" data-testid="confidence-slider">
        <div className="settings-slider__head">
          <span className="settings-field__label">
            {t.settings.automation.confidence}
          </span>
          <span className="mono settings-slider__val" data-testid="confidence-value">
            {form.confidence_threshold}%
          </span>
        </div>
        <input
          type="range"
          min="0"
          max="100"
          step="1"
          value={form.confidence_threshold}
          onChange={(e) =>
            update({ confidence_threshold: parseInt(e.target.value, 10) })
          }
          className="settings-slider__range"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={form.confidence_threshold}
          disabled={!form.auto_upload_enabled}
        />
        <div className="settings-slider__hint muted">
          {form.auto_upload_enabled
            ? t.settings.automation.confidenceHint
            : t.settings.automation.confidenceDisabled}
        </div>
      </div>

      <div className="settings-slider" data-testid="min-confidence-slider">
        <div className="settings-slider__head">
          <span className="settings-field__label">
            {t.settings.automation.minConfidence}
          </span>
          <span className="mono settings-slider__val" data-testid="min-confidence-value">
            {form.ai_min_confidence_to_save ?? 40}%
          </span>
        </div>
        <input
          type="range"
          min="0"
          max="100"
          step="1"
          value={form.ai_min_confidence_to_save ?? 40}
          onChange={(e) =>
            update({ ai_min_confidence_to_save: parseInt(e.target.value, 10) })
          }
          className="settings-slider__range"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={form.ai_min_confidence_to_save ?? 40}
        />
        <div className="settings-slider__hint muted">
          {t.settings.automation.minConfidenceHint}
        </div>
      </div>
    </section>
  );
}
