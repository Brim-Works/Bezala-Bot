import { useCallback, useMemo, useState } from 'react';

/* Enkel id-selection-hook. Använder Set för O(1) toggle/has.
 * Tillhandahåller toggle, clear, selectAll, has, size och en stabil
 * ids-array för att posta till backend. */
export function useSelection() {
  const [set, setSet] = useState(() => new Set());

  const toggle = useCallback((id) => {
    setSet((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const clear = useCallback(() => setSet(new Set()), []);

  const selectAll = useCallback((ids) => {
    setSet(new Set(ids));
  }, []);

  const has = useCallback((id) => set.has(id), [set]);

  const ids = useMemo(() => Array.from(set), [set]);

  return { toggle, clear, selectAll, has, ids, size: set.size };
}
