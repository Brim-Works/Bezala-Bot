import { useEffect } from 'react';

/* Minimal toast — visar ett meddelande och stänger sig själv efter ~2.5s.
 * Centraliseras i Commit 6 polish. */
export default function Toast({ toast, onDismiss }) {
  useEffect(() => {
    if (!toast) return undefined;
    const id = setTimeout(onDismiss, 2500);
    return () => clearTimeout(id);
  }, [toast, onDismiss]);

  if (!toast) return null;
  return (
    <div className="toast-wrap" role="status" aria-live="polite">
      <div className={`toast toast--${toast.kind || 'ok'}`}>
        <span>{toast.message}</span>
      </div>
    </div>
  );
}
