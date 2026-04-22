/* Sortering för Dashboard-tabellen. Ren funktion — testbar utan React.
 *
 * Tillåtna kolumner:
 *   receipt_date  — kvittots inköpsdatum (string 'YYYY-MM-DD' eller null)
 *   processed_at  — backend processing-tidsstämpel (ISO-datetime)
 *   amount        — numeriskt belopp
 *   vendor        — leverantörsnamn (alfabetisk)
 *
 * receipt_date-sortering föredrar kvittodatum, faller tillbaka på
 * processed_at om det saknas — så rader utan receipt_date (t.ex.
 * skipped eller link-fetch i pending-state) ändå får en stabil
 * position.
 */

import { parseBackendDate } from './format.js';

export const SORT_COLUMNS = ['receipt_date', 'processed_at', 'amount', 'vendor'];
export const DEFAULT_SORT_COL = 'receipt_date';
export const DEFAULT_SORT_DIR = 'desc';

// localStorage-nycklar (spec:ens namn)
export const SORT_COL_STORAGE_KEY = 'bb_sort_col';
export const SORT_DIR_STORAGE_KEY = 'bb_sort_dir';

function rowTimestamp(row, col) {
  if (col === 'receipt_date') {
    return (
      parseBackendDate(row.receipt_date)?.getTime() ??
      parseBackendDate(row.processed_at)?.getTime() ??
      0
    );
  }
  return parseBackendDate(row.processed_at)?.getTime() ?? 0;
}

function compareRows(a, b, col) {
  if (col === 'receipt_date' || col === 'processed_at') {
    return rowTimestamp(a, col) - rowTimestamp(b, col);
  }
  if (col === 'amount') {
    const va = a.amount == null ? -Infinity : Number(a.amount);
    const vb = b.amount == null ? -Infinity : Number(b.amount);
    return va - vb;
  }
  if (col === 'vendor') {
    const va = (a.vendor || '').toLowerCase();
    const vb = (b.vendor || '').toLowerCase();
    if (va < vb) return -1;
    if (va > vb) return 1;
    return 0;
  }
  return 0;
}

export function sortMessages(messages, col, dir) {
  const safeCol = SORT_COLUMNS.includes(col) ? col : DEFAULT_SORT_COL;
  const safeDir = dir === 'asc' ? 'asc' : 'desc';
  const sign = safeDir === 'asc' ? 1 : -1;
  // Kopiera så originalet inte muteras. Stabilitet via id-tiebreaker.
  return [...messages].sort((a, b) => {
    const cmp = compareRows(a, b, safeCol);
    if (cmp !== 0) return sign * cmp;
    return (a.id ?? 0) - (b.id ?? 0);
  });
}

export function readStoredSort() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return { col: DEFAULT_SORT_COL, dir: DEFAULT_SORT_DIR };
    }
    const col = window.localStorage.getItem(SORT_COL_STORAGE_KEY);
    const dir = window.localStorage.getItem(SORT_DIR_STORAGE_KEY);
    return {
      col: SORT_COLUMNS.includes(col) ? col : DEFAULT_SORT_COL,
      dir: dir === 'asc' ? 'asc' : DEFAULT_SORT_DIR,
    };
  } catch {
    return { col: DEFAULT_SORT_COL, dir: DEFAULT_SORT_DIR };
  }
}

export function writeStoredSort(col, dir) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return;
    window.localStorage.setItem(SORT_COL_STORAGE_KEY, col);
    window.localStorage.setItem(SORT_DIR_STORAGE_KEY, dir);
  } catch {
    // localStorage kan vara full eller blockerad — ignorera tyst
  }
}
