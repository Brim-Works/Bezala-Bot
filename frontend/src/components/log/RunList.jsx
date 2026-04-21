import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtDate } from '../../lib/format.js';
import { formatDuration, runDuration, runStatusKind } from '../../lib/runNarrative.js';

/* Vänsterkolumn i Logg-vyn. Varje rad = en körning. */
export default function RunList({ runs, selectedId, onSelect, onClearErrors, clearingErrors }) {
  const { t, lang } = useI18n();

  return (
    <div className="card log-list">
      <div className="log-list__head">
        <span className="log-list__title">{t.log.runsTitle}</span>
        <div className="log-list__head-right">
          <span className="mono log-list__count">{runs.length}</span>
          <button
            type="button"
            className="btn btn--sm"
            onClick={onClearErrors}
            disabled={clearingErrors}
            data-testid="clear-errors"
          >
            {clearingErrors ? t.log.clearing : t.log.clearErrors}
          </button>
        </div>
      </div>
      <div className="log-list__scroll" data-testid="run-list">
        {runs.length === 0 ? (
          <div className="log-list__empty muted">{t.log.empty}</div>
        ) : (
          runs.map((run) => {
            const tone = runStatusKind(run);
            const isActive = run.id === selectedId;
            const processed = run.messages_processed || 0;
            return (
              <button
                key={run.id}
                type="button"
                className={`log-run log-run--${tone} ${isActive ? 'is-active' : ''}`}
                onClick={() => onSelect(run.id)}
                aria-pressed={isActive}
                data-testid={`run-item-${run.id}`}
              >
                <span className="log-run__dot" aria-hidden="true" />
                <div className="log-run__body">
                  <div className="log-run__time mono">
                    {fmtDate(run.started_at, lang)}
                  </div>
                  <div className="log-run__summary">
                    {processed > 0
                      ? `${processed} ${t.log.processed}`
                      : t.log.noNewMail}
                    {(run.errors || 0) > 0 ? ` · ${run.errors} ${t.log.errors}` : ''}
                  </div>
                </div>
                <span className="log-run__dur mono">
                  {formatDuration(runDuration(run))}
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
