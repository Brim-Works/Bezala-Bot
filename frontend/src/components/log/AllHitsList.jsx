import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtDate } from '../../lib/format.js';

/* Platt lista över träffar från ALLA körningar. Används när listMode='all'
 * i Log.jsx. Varje rad visar avsändare, ämne, datum och en liten badge
 * med körnings-id + starttid. Processed-rader är klickbara (öppnar
 * drawer); filtered-rader är informativa (visar reason).
 */
export default function AllHitsList({ hits, searchText, onOpenMessage }) {
  const { t, lang } = useI18n();
  const hasSearch = (searchText || '').trim().length > 0;

  return (
    <div className="card log-list log-list--full">
      <div className="log-list__head">
        <span className="log-list__title">{t.log.allHits.title}</span>
        <span className="mono log-list__count">
          {hasSearch ? `${hits.length} ${t.log.allHits.hitCount}` : ''}
        </span>
      </div>
      <div className="log-list__scroll" data-testid="all-hits-list">
        {!hasSearch ? (
          <div className="log-list__empty muted">
            {t.log.allHits.emptyPrompt}
          </div>
        ) : hits.length === 0 ? (
          <div className="log-list__empty muted">{t.log.allHits.noMatch}</div>
        ) : (
          hits.map((h) => {
            const clickable = h.kind === 'processed' && h.id != null;
            const reasonLabel =
              h.kind === 'filtered' && h.reason
                ? (t.log.filtered && t.log.filtered[h.reason]) || h.reason
                : null;
            return (
              <button
                key={h.key}
                type="button"
                className={`log-hit ${clickable ? '' : 'log-hit--static'}`}
                onClick={() => clickable && onOpenMessage(h.id)}
                aria-disabled={!clickable}
                disabled={!clickable}
                data-testid={`all-hit-${h.key}`}
              >
                <div className="log-hit__body">
                  <div className="log-hit__sender">{h.sender || '—'}</div>
                  <div className="log-hit__subject">{h.subject || '—'}</div>
                  <div className="log-hit__meta mono muted">
                    {t.log.allHits.runLabel} #{h.runId}
                    {h.runStartedAt
                      ? ` · ${fmtDate(h.runStartedAt, lang)}`
                      : ''}
                    {reasonLabel ? ` · ${reasonLabel}` : ''}
                  </div>
                </div>
                <span className="log-hit__date mono">
                  {h.date ? fmtDate(h.date, lang) : ''}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
