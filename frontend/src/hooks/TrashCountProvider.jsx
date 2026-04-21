import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';

/* Global trash-count så Sidebar alltid kan visa badge utan duplicated
 * polling per vy. Vyer som soft-deletar kallar bump(delta) för optimistic
 * uppdatering så badgen reagerar direkt. */

const TrashCountContext = createContext(null);
const POLL_INTERVAL_MS = 60_000;

export function TrashCountProvider({ children }) {
  const [count, setCount] = useState(0);
  // Global "messages changed"-signal som vyer kan lyssna på för att refetcha
  // efter t.ex. drawer-delete. Ökas av bumpMessagesVersion().
  const [messagesVersion, setMessagesVersion] = useState(0);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const data = await api.trashCount();
      if (mounted.current) setCount(Number(data?.count || 0));
    } catch {
      // tyst — räknare ska inte bryta appen
    }
  }, []);

  const bump = useCallback((delta) => {
    setCount((c) => Math.max(0, c + delta));
  }, []);

  const bumpMessagesVersion = useCallback(() => {
    setMessagesVersion((v) => v + 1);
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

  return (
    <TrashCountContext.Provider
      value={{ count, refresh, bump, messagesVersion, bumpMessagesVersion }}
    >
      {children}
    </TrashCountContext.Provider>
  );
}

export function useTrashCountContext() {
  const ctx = useContext(TrashCountContext);
  if (!ctx) throw new Error('useTrashCountContext måste användas inuti <TrashCountProvider>');
  return ctx;
}
