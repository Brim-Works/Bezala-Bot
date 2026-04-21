import { useCallback, useMemo, useRef, useState } from 'react';

/* Enkel id-selection-hook. Använder Set för O(1) toggle/has.
 * Tillhandahåller toggle, clear, selectAll, has, size och en stabil
 * ids-array för att posta till backend.
 *
 * selectRange(orderedIds, targetId) implementerar Gmail/Finder-stil
 * shift-klick: lägger till allt mellan senast togglade id (ankare) och
 * target, inklusive båda ändpunkterna. Befintliga val bevaras (range-ADD,
 * inte range-REPLACE). Om inget ankare finns faller vi tillbaka till
 * att bara lägga till target. */
export function useSelection() {
  const [set, setSet] = useState(() => new Set());
  const anchorRef = useRef(null);

  const toggle = useCallback((id) => {
    setSet((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    anchorRef.current = id;
  }, []);

  const selectRange = useCallback((orderedIds, targetId) => {
    // Läs ankaret synkront innan setSet — setSet:s updater körs efter
    // att callbacken returnerat, så om vi uppdaterar anchorRef först
    // skulle updatern läsa det nya värdet. React 18 batchar också
    // updates, så varje läsning av anchorRef inuti setSet är ur sync.
    const anchor = anchorRef.current;
    anchorRef.current = targetId;
    setSet((prev) => {
      const next = new Set(prev);
      const aIdx = anchor == null ? -1 : orderedIds.indexOf(anchor);
      const bIdx = orderedIds.indexOf(targetId);
      if (bIdx === -1) return next;
      if (aIdx === -1) {
        next.add(targetId);
        return next;
      }
      const [lo, hi] = aIdx <= bIdx ? [aIdx, bIdx] : [bIdx, aIdx];
      for (let i = lo; i <= hi; i++) next.add(orderedIds[i]);
      return next;
    });
  }, []);

  const clear = useCallback(() => {
    setSet(new Set());
    anchorRef.current = null;
  }, []);

  const selectAll = useCallback((ids) => {
    setSet(new Set(ids));
    anchorRef.current = null;
  }, []);

  const has = useCallback((id) => set.has(id), [set]);

  const ids = useMemo(() => Array.from(set), [set]);

  return { toggle, selectRange, clear, selectAll, has, ids, size: set.size };
}
