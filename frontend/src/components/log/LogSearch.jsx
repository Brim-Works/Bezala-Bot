import { useI18n } from '../../i18n/useI18n.jsx';

const DATE_OPTIONS = ['all', 'last24h', 'last7d', 'last30d'];
const STATUS_OPTIONS = ['all', 'ok', 'partial', 'idle', 'error'];
const MODE_OPTIONS = ['runs', 'all'];

/* Sök + filter ovanför Log-split-vyn.
 * Följer Dashboard-mönstret: .fbar för layout, .fbar__tab för
 * status-knappar, .fbar__search för text-input, settings-field__select
 * för datum-dropdown. Ingen egen styling — allt via befintliga tokens.
 *
 * Radfördelning:
 *   Rad 1: Status-tabs + sökfält + listMode-toggle + datum-dropdown
 *
 * Sök-inputen filtrerar BÅDE körningslistan och meddelanden inom
 * vald körning (se Log.jsx). listMode='all' växlar vänsterkolumnen
 * till en platt lista med alla träffar över alla körningar.
 */
export default function LogSearch({
  searchText,
  onSearchText,
  dateFilter,
  onDateFilter,
  statusFilter,
  onStatusFilter,
  listMode,
  onListMode,
}) {
  const { t } = useI18n();

  return (
    <div className="fbar" data-testid="log-search">
      {STATUS_OPTIONS.map((k) => {
        const active = statusFilter === k;
        return (
          <button
            key={k}
            type="button"
            className={`fbar__tab ${active ? 'is-active' : ''}`}
            onClick={() => onStatusFilter(k)}
            aria-pressed={active}
            data-testid={`log-search-status-${k}`}
          >
            {t.log.search.status[k]}
          </button>
        );
      })}

      <label className="fbar__search">
        <span className="visually-hidden">{t.log.search.placeholder}</span>
        <input
          type="search"
          value={searchText}
          onChange={(e) => onSearchText(e.target.value)}
          placeholder={t.log.search.placeholder}
          data-testid="log-search-input"
        />
      </label>

      <div
        className="fbar__segmented"
        role="radiogroup"
        aria-label={t.log.search.mode.all}
      >
        {MODE_OPTIONS.map((k) => {
          const active = listMode === k;
          return (
            <button
              key={k}
              type="button"
              className={`fbar__tab ${active ? 'is-active' : ''}`}
              onClick={() => onListMode(k)}
              aria-pressed={active}
              role="radio"
              aria-checked={active}
              data-testid={`log-search-mode-${k}`}
            >
              {t.log.search.mode[k]}
            </button>
          );
        })}
      </div>

      <select
        className="settings-field__select"
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
    </div>
  );
}
