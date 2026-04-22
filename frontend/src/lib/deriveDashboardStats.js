/* Härleder dashboard-stat-kortens värden från meddelande-listan.
 * Ren funktion — krävs eftersom backend /api/stats inte har
 * pending_count, transferred_today eller total_this_week.
 *
 * "Total denna vecka" exkluderar skipped-rader — vi räknar bara
 * kvitton som faktiskt bearbetats, inte skräp som hoppades över. */

import { isToday, isWithinDays } from './format.js';

export function deriveDashboardStats(messages, backendStats) {
  const list = messages || [];
  const pending = list.filter((m) => m.bezala_status === 'pending').length;
  const transferredToday = list.filter(
    (m) => m.bezala_status === 'transferred' && isToday(m.processed_at),
  ).length;
  const totalThisWeek = list.filter(
    (m) => m.file_status !== 'skipped' && isWithinDays(m.processed_at, 7),
  ).length;

  return {
    pending,
    transferredToday,
    errors: backendStats?.errors ?? null,
    totalThisWeek,
    lastRun: backendStats?.last_run || null,
  };
}
