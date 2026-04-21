import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

/* Centraliserat toast-system.
 * - Flera toastar kan visas samtidigt (staplas i en kolumn)
 * - Auto-dismiss efter 3 s (konfigurerbart per anrop)
 * - Hand-dismiss via close-knapp eller explicit dismiss(id)
 * - Keyboard: close-knappen fokuserbar, Esc stänger inte (vi vill inte
 *   att toast-dismiss krockar med drawer-Esc). */

const ToastContext = createContext(null);

const DEFAULT_TIMEOUT_MS = 3000;

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);
  const timers = useRef(new Map());

  const dismiss = useCallback((id) => {
    setToasts((list) => list.filter((t) => t.id !== id));
    const timer = timers.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timers.current.delete(id);
    }
  }, []);

  const show = useCallback(
    (toast) => {
      if (!toast || !toast.message) return -1;
      const id = nextId.current++;
      const entry = {
        id,
        kind: toast.kind || 'ok',
        message: toast.message,
        action: toast.action || null, // { label, onClick }
      };
      setToasts((list) => [...list, entry]);
      const timeout = toast.timeout ?? DEFAULT_TIMEOUT_MS;
      if (timeout > 0) {
        const timer = setTimeout(() => dismiss(id), timeout);
        timers.current.set(id, timer);
      }
      return id;
    },
    [dismiss],
  );

  useEffect(
    () => () => {
      timers.current.forEach((timer) => clearTimeout(timer));
      timers.current.clear();
    },
    [],
  );

  const value = useMemo(
    () => ({ show, dismiss, toasts }),
    [show, dismiss, toasts],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastStack toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function ToastStack({ toasts, onDismiss }) {
  if (toasts.length === 0) return null;
  return (
    <div className="toast-wrap" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`toast toast--${toast.kind}`}
          data-testid={`toast-${toast.kind}`}
        >
          <span className="toast__msg">{toast.message}</span>
          {toast.action ? (
            <button
              type="button"
              className="toast__action"
              onClick={() => {
                try {
                  toast.action.onClick?.();
                } finally {
                  onDismiss(toast.id);
                }
              }}
              data-testid="toast-action"
            >
              {toast.action.label}
            </button>
          ) : null}
          <button
            type="button"
            className="toast__close"
            onClick={() => onDismiss(toast.id)}
            aria-label="Close"
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

export function useToast() {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast måste användas inuti <ToastProvider>');
  return ctx;
}
