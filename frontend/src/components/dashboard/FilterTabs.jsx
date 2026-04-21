import { useI18n } from '../../i18n/useI18n.jsx';

const FILTERS = ['all', 'pending', 'auto', 'errors'];

export default function FilterTabs({ filter, setFilter, counts, query, setQuery }) {
  const { t } = useI18n();
  return (
    <div className="fbar">
      {FILTERS.map((id) => (
        <button
          key={id}
          type="button"
          className={`fbar__tab ${filter === id ? 'is-active' : ''}`}
          onClick={() => setFilter(id)}
          aria-pressed={filter === id}
        >
          {t.filters[id]}
          <span className="fbar__count mono">{counts[id] ?? 0}</span>
        </button>
      ))}
      <label className="fbar__search">
        <span className="visually-hidden">{t.search.label}</span>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t.search.placeholder}
        />
      </label>
    </div>
  );
}
