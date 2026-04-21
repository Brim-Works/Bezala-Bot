import ChipEditor from './ChipEditor.jsx';
import BuiltinSendersBlock from './BuiltinSendersBlock.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';

export default function ScanRulesSection({ form, update, builtinSenders }) {
  const { t } = useI18n();
  return (
    <section className="settings-section" data-testid="scan-rules">
      <header className="settings-section__head">
        <h2 className="settings-section__title">{t.settings.scanRules.title}</h2>
        <p className="settings-section__lead muted">
          {t.settings.scanRules.lead}
        </p>
      </header>

      <BuiltinSendersBlock senders={builtinSenders} />

      <ChipEditor
        label={t.settings.scanRules.include}
        placeholder={t.settings.scanRules.includePlaceholder}
        values={form.include_senders}
        onChange={(v) => update({ include_senders: v })}
        hint={t.settings.scanRules.includeHint}
        testIdPrefix="include-senders"
      />

      <ChipEditor
        label={t.settings.scanRules.exclude}
        placeholder={t.settings.scanRules.excludePlaceholder}
        values={form.exclude_senders}
        onChange={(v) => update({ exclude_senders: v })}
        hint={t.settings.scanRules.excludeHint}
        testIdPrefix="exclude-senders"
      />

      <ChipEditor
        label={t.settings.scanRules.excludeSubjects}
        placeholder={t.settings.scanRules.excludeSubjectsPlaceholder}
        values={form.exclude_subjects}
        onChange={(v) => update({ exclude_subjects: v })}
        hint={t.settings.scanRules.excludeSubjectsHint}
        testIdPrefix="exclude-subjects"
      />
    </section>
  );
}
