import { useI18n } from '../../i18n/useI18n.jsx';
import { DATE_FILTER_KEYS } from '../../lib/dateFilter.js';

/* Dropdown för datumfilter bredvid FilterTabs. Alternativ:
 * Allt / Senaste månaden (30d) / Senaste kvartalet (90d) / Senaste året (365d).
 * Persisterar automatiskt i parent-view (Dashboard) via localStorage. */
export default function DateFilter({ value, onChange }) {
  const { t } = useI18n();
  return (
    <label className="date-filter">
      <span className="visually-hidden">{t.dateFilter.label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid="date-filter"
        aria-label={t.dateFilter.label}
      >
        {DATE_FILTER_KEYS.map((k) => (
          <option key={k} value={k}>
            {t.dateFilter.options[k]}
          </option>
        ))}
      </select>
    </label>
  );
}
