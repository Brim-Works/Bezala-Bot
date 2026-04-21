import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';

/* Generisk chip-editor för en lista av strängar.
 * - Enter eller + lägger till värdet i input-fältet
 * - Klick på × på en chip tar bort den
 * - Duplikatkontroll, trimmar whitespace
 * - Chips renderas i mono-font (backend-värden är ofta e-mailadresser/
 *   domäner/ämnesfragment som tjänar på monospace). */
export default function ChipEditor({
  label,
  values = [],
  placeholder,
  onChange,
  hint,
  testIdPrefix,
}) {
  const { t } = useI18n();
  const [draft, setDraft] = useState('');

  function add() {
    const v = draft.trim();
    if (!v) return;
    if (values.includes(v)) {
      setDraft('');
      return;
    }
    onChange([...values, v]);
    setDraft('');
  }

  function remove(v) {
    onChange(values.filter((x) => x !== v));
  }

  const inputTestId = testIdPrefix ? `${testIdPrefix}-input` : undefined;

  return (
    <div className="chip-editor">
      <label className="chip-editor__label">{label}</label>
      <div className="chip-editor__box">
        {values.map((v) => (
          <span key={v} className="chip">
            <span className="mono chip__text">{v}</span>
            <button
              type="button"
              className="chip__remove"
              aria-label={`${t.common.remove}: ${v}`}
              onClick={() => remove(v)}
              data-testid={testIdPrefix ? `${testIdPrefix}-remove-${v}` : undefined}
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          className="chip-editor__input"
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }}
          data-testid={inputTestId}
        />
        <button
          type="button"
          className="btn btn--sm chip-editor__add"
          onClick={add}
          disabled={!draft.trim()}
        >
          + {t.common.add}
        </button>
      </div>
      {hint ? <div className="chip-editor__hint muted">{hint}</div> : null}
    </div>
  );
}
