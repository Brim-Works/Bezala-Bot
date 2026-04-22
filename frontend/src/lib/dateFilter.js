/* Dashboard datumfilter. Ren funktion — filterar på receipt_date med
 * fallback till processed_at. Persistent via localStorage. */

import { parseBackendDate } from './format.js';

export const DATE_FILTER_KEYS = ['all', 'last30d', 'last90d', 'last365d'];
export const DEFAULT_DATE_FILTER = 'all';
export const DATE_FILTER_STORAGE_KEY = 'bb_date_filter';

const DAY_MS = 24 * 60 * 60 * 1000;

const FILTER_DAYS = {
  all: null,
  last30d: 30,
  last90d: 90,
  last365d: 365,
};

function rowDateMs(row) {
  return (
    parseBackendDate(row.receipt_date)?.getTime() ??
    parseBackendDate(row.processed_at)?.getTime() ??
    null
  );
}

export function applyDateFilter(messages, filterKey, now = Date.now()) {
  const days = FILTER_DAYS[filterKey];
  if (days == null) return messages;
  const cutoff = now - days * DAY_MS;
  return messages.filter((m) => {
    const ts = rowDateMs(m);
    if (ts == null) return false; // rader utan datum faller bort under tidsfilter
    return ts >= cutoff;
  });
}

export function readStoredDateFilter() {
  try {
    if (typeof window === 'undefined' || !window.localStorage) {
      return DEFAULT_DATE_FILTER;
    }
    const val = window.localStorage.getItem(DATE_FILTER_STORAGE_KEY);
    return DATE_FILTER_KEYS.includes(val) ? val : DEFAULT_DATE_FILTER;
  } catch {
    return DEFAULT_DATE_FILTER;
  }
}

export function writeStoredDateFilter(key) {
  try {
    if (typeof window === 'undefined' || !window.localStorage) return;
    window.localStorage.setItem(DATE_FILTER_STORAGE_KEY, key);
  } catch {
    // ignorera tyst
  }
}
