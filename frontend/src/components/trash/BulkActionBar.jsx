import { useI18n } from '../../i18n/useI18n.jsx';
import { IconTrash } from '../../icons/index.jsx';

/* Sticky bar ovanför tabeller i Dashboard/Review/Log när 1+ rader är
 * markerade. Visar antal + knappen "Ta bort" + "Avmarkera". */
export default function BulkActionBar({ count, onDelete, onClear, busy }) {
  const { t } = useI18n();
  if (count === 0) return null;
  return (
    <div className="bulk-bar" data-testid="bulk-bar" role="region" aria-label={t.trash.bulk.label}>
      <span className="bulk-bar__count">
        <span className="mono">{count}</span> {t.trash.bulk.selected}
      </span>
      <div className="bulk-bar__spacer" />
      <button
        type="button"
        className="btn ghost"
        onClick={onClear}
        disabled={busy}
        data-testid="bulk-bar-clear"
      >
        {t.trash.bulk.clear}
      </button>
      <button
        type="button"
        className="btn"
        onClick={onDelete}
        disabled={busy}
        data-testid="bulk-bar-delete"
      >
        <IconTrash className="icon sm" />
        {busy ? t.trash.bulk.deleting : t.trash.bulk.delete}
      </button>
    </div>
  );
}
