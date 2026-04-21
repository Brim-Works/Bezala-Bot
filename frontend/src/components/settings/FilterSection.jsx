import { useI18n } from '../../i18n/useI18n.jsx';

const TOGGLES = [
  { key: 'require_attachments', labelKey: 'requireAttachments', hintKey: 'requireAttachmentsHint' },
  { key: 'exclude_promotions', labelKey: 'excludePromotions', hintKey: 'excludePromotionsHint' },
  { key: 'exclude_social', labelKey: 'excludeSocial', hintKey: 'excludeSocialHint' },
  { key: 'exclude_calendar', labelKey: 'excludeCalendar', hintKey: 'excludeCalendarHint' },
];

export default function FilterSection({ form, update }) {
  const { t } = useI18n();
  return (
    <section className="settings-section" data-testid="filters">
      <header className="settings-section__head">
        <h2 className="settings-section__title">{t.settings.filters.title}</h2>
        <p className="settings-section__lead muted">
          {t.settings.filters.lead}
        </p>
      </header>

      <div className="settings-toggles">
        {TOGGLES.map((opt) => (
          <label className="settings-toggle" key={opt.key}>
            <input
              type="checkbox"
              checked={Boolean(form[opt.key])}
              onChange={(e) => update({ [opt.key]: e.target.checked })}
              data-testid={`toggle-${opt.key}`}
            />
            <span className="settings-toggle__label">
              {t.settings.filters[opt.labelKey]}
              <span className="settings-toggle__sub muted">
                {t.settings.filters[opt.hintKey]}
              </span>
            </span>
          </label>
        ))}
      </div>
    </section>
  );
}
