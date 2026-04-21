import { useI18n } from '../../i18n/useI18n.jsx';

/* 14 senaste körningarna som horisontell stapelgraf.
 * Höjd: 10 + min(50, processed × 8). Färg: röd vid fel, dimmad vid 0,
 * accent annars. */
export default function RunBars({ runs }) {
  const { t } = useI18n();
  const chronological = (runs || []).slice(0, 14).reverse();

  if (chronological.length === 0) {
    return (
      <div className="card card-pad runbars runbars--empty">
        <p className="muted">{t.runs.empty}</p>
      </div>
    );
  }

  return (
    <div className="card card-pad runbars">
      <div className="runbars__bars">
        {chronological.map((run) => {
          const processed = run.messages_processed || 0;
          const hasErrors = (run.errors || 0) > 0 || run.status === 'error';
          const empty = processed === 0 && !hasErrors;
          const height = 10 + Math.min(50, processed * 8);
          const variant = hasErrors ? 'err' : empty ? 'empty' : 'ok';
          const title = `${processed} ${t.runs.processed}${
            hasErrors ? ` · ${run.errors} ${t.runs.errors}` : ''
          }`;
          return (
            <div
              key={run.id}
              className={`runbars__bar runbars__bar--${variant}`}
              style={{ height }}
              title={title}
              aria-label={title}
            />
          );
        })}
      </div>
      <div className="runbars__axis mono">
        <span>−14h</span>
        <span>{t.runs.now}</span>
      </div>
    </div>
  );
}
