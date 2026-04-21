import ErrorBoundary from './ErrorBoundary.jsx';
import { useI18n } from '../i18n/useI18n.jsx';

function Fallback({ error, reset }) {
  const { t } = useI18n();
  return (
    <div className="error-boundary" data-testid="error-boundary">
      <h2 className="error-boundary__title">{t.errors.viewTitle}</h2>
      <p className="muted">
        {error?.message ? error.message : t.errors.viewGeneric}
      </p>
      <button type="button" className="btn" onClick={reset}>
        {t.errors.reload}
      </button>
    </div>
  );
}

/* Per-vy-wrapper: använder i18n från context. */
export default function ViewErrorBoundary({ children, viewKey }) {
  return (
    <ErrorBoundary
      fallback={(ctx) => <Fallback {...ctx} viewKey={viewKey} />}
    >
      {children}
    </ErrorBoundary>
  );
}
