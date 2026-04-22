import { useI18n } from '../../i18n/useI18n.jsx';

const DATE_OPTIONS = ['all', 'last24h', 'last7d', 'last30d'];
const STATUS_OPTIONS = ['all', 'ok', 'partial', 'idle', 'error'];

/* Sök + filter ovanför Log-split-vyn.
 * - Text-sökning filtrerar meddelanden inom vald körning
 * - Datum-dropdown filtrerar körningslistan
 * - Status-pills filtrerar körningslistan */
export default function LogSearch({
  searchText,
  onSearchText,
  dateFilter,
  onDateFilter,
  statusFilter,
  onStatusFilter,
}) {
  const { t } = useI18n();

  return (
    <div className="log-search" data-testid="log-search">
      <input
        type="search"
        className="log-search__input"
        placeholder={t.log.search.placeholder}
        value={searchText}
        onChange={(e) => onSearchText(e.target.value)}
        data-testid="log-search-input"
      />

      <select
        className="log-search__select"
        value={dateFilter}
        onChange={(e) => onDateFilter(e.target.value)}
        data-testid="log-search-date"
        aria-label={t.log.search.date.all}
      >
        {DATE_OPTIONS.map((k) => (
          <option key={k} value={k}>
            {t.log.search.date[k]}
          </option>
        ))}
      </select>

      <div className="log-search__pills" role="radiogroup">
        {STATUS_OPTIONS.map((k) => {
          const active = statusFilter === k;
          return (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={active}
              className={`log-search__pill ${active ? 'is-active' : ''}`}
              onClick={() => onStatusFilter(k)}
              data-testid={`log-search-status-${k}`}
            >
              {t.log.search.status[k]}
            </button>
          );
        })}
      </div>
    </div>
  );
}
