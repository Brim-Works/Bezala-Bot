import { useI18n } from '../../i18n/useI18n.jsx';

const OPTIONS = [
  { value: 0, labelKey: 'never' },
  { value: 30, labelKey: 'days30' },
  { value: 60, labelKey: 'days60' },
  { value: 90, labelKey: 'days90' },
];

export default function TrashSection({ form, update }) {
  const { t } = useI18n();
  const current = Number(form?.trash_auto_purge_days ?? 0);
  const isNever = current === 0;

  return (
    <section className="settings-section" data-testid="trash-settings">
      <header className="settings-section__head">
        <h2 className="settings-section__title">{t.trash.autoPurge.title}</h2>
        <p className="settings-section__lead muted">{t.trash.autoPurge.lead}</p>
      </header>

      <label className="settings-field">
        <span className="settings-field__label">{t.trash.autoPurge.label}</span>
        <select
          className="settings-field__select"
          value={String(current)}
          onChange={(e) => update({ trash_auto_purge_days: parseInt(e.target.value, 10) })}
          data-testid="trash-auto-purge"
        >
          {OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {t.trash.autoPurge[opt.labelKey]}
            </option>
          ))}
        </select>
        <span className="settings-slider__hint muted">
          {isNever ? t.trash.autoPurge.neverHint : t.trash.autoPurge.enabledHint}
        </span>
      </label>
    </section>
  );
}
