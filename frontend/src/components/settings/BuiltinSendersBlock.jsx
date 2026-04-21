import { useI18n } from '../../i18n/useI18n.jsx';

/* Read-only pills över alltid-aktiva avsändare. Inga × — listan kommer
 * från backend (settings.builtin_senders) och styrs i kod. */
export default function BuiltinSendersBlock({ senders }) {
  const { t } = useI18n();
  if (!senders || senders.length === 0) return null;
  return (
    <div className="chip-editor" data-testid="builtin-senders">
      <label className="chip-editor__label">
        {t.settings.builtinSenders.title}
      </label>
      <div className="chip-editor__box chip-editor__box--readonly">
        {senders.map((s) => (
          <span key={s} className="chip chip--builtin">
            <span className="mono chip__text">{s}</span>
          </span>
        ))}
      </div>
      <div className="chip-editor__hint muted">
        {t.settings.builtinSenders.hint}
      </div>
    </div>
  );
}
