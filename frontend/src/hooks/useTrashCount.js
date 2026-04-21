import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';

/* Trash-räknare för Sidebar-badge. Polling-frekvens 60 s. Exponerar
 * refresh() för optimistic updates (direkt när user soft-deletar). */

const POLL_INTERVAL_MS = 60_000;

export function useTrashCount() {
  const [count, setCount] = useState(0);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.trashCount();
      if (mounted.current) setCount(Number(data?.count || 0));
    } catch {
      // Silent — räknaren är icke-kritisk.
    }
  }, []);

  const bump = useCallback((delta) => {
    setCount((c) => Math.max(0, c + delta));
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const id = setInterval(refresh, POLL_INTERVAL_MS);
    return () => {
      mounted.current = false;
      clearInterval(id);
    };
  }, [refresh]);

  return { count, refresh, bump };
}
