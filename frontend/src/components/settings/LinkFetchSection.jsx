import ChipEditor from './ChipEditor.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';

/* Leverantörer där Bezala Bot ignorerar bilagor och istället letar efter
 * en kvitto-länk i mailets body. Användaren kan lägga till och ta bort.
 * Default-värdet (noreply@arlandaexpress.se) sätts av backend vid första
 * load — UI:t är ett vanligt chip-editor. */
export default function LinkFetchSection({ form, update }) {
  const { t } = useI18n();
  return (
    <section className="settings-section" data-testid="link-fetch">
      <header className="settings-section__head">
        <h2 className="settings-section__title">{t.settings.linkFetch.title}</h2>
        <p className="settings-section__lead muted">{t.settings.linkFetch.lead}</p>
      </header>
      <ChipEditor
        label={t.settings.linkFetch.label}
        placeholder={t.settings.linkFetch.placeholder}
        values={form.link_fetch_senders || []}
        onChange={(v) => update({ link_fetch_senders: v })}
        hint={t.settings.linkFetch.hint}
        testIdPrefix="link-fetch-senders"
      />
    </section>
  );
}
