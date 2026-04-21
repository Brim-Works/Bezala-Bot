/* Genererar en prosa-sammanfattning av en körning. Försöker matcha
 * design/src/log.jsx:narrative() givet den data backend faktiskt har —
 * ingen auto-rate eller stages-detalj, bara de aggregerade räknarna. */

import { parseBackendDate } from './format.js';

function formatTime(iso, lang) {
  const d = parseBackendDate(iso);
  if (!d) return '';
  const locale = lang === 'en' ? 'en-FI' : 'sv-FI';
  return new Intl.DateTimeFormat(locale, {
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

export function runNarrative(run, lang) {
  if (!run) return '';
  const time = formatTime(run.started_at, lang);
  const found = run.messages_found || 0;
  const processed = run.messages_processed || 0;
  const errors = run.errors || 0;

  if (found === 0) {
    return lang === 'en'
      ? `At ${time} — scan ran, no new mail matched the filter.`
      : `Kl. ${time} — scanning körd, inga nya mail matchade filtret.`;
  }

  if (errors > 0) {
    return lang === 'en'
      ? `At ${time} the bot found ${found} new mail. ${processed} were processed, but ${errors} row${errors === 1 ? '' : 's'} failed.`
      : `Kl. ${time} hittade botten ${found} nya mail. ${processed} bearbetades, men ${errors} rad${errors === 1 ? '' : 'er'} misslyckades.`;
  }

  return lang === 'en'
    ? `At ${time} the bot found ${found} new mail → AI processed ${processed} of them → transferred to Drive.`
    : `Kl. ${time} hittade botten ${found} nya mail → AI bearbetade ${processed} av dem → uppladdade till Drive.`;
}

export function runStatusKind(run) {
  if (!run) return 'muted';
  if ((run.errors || 0) > 0 || run.status === 'error') return 'err';
  if ((run.messages_processed || 0) === 0) return 'muted';
  return 'ok';
}

export function runDuration(run) {
  if (!run?.started_at || !run?.finished_at) return null;
  const startDate = parseBackendDate(run.started_at);
  const endDate = parseBackendDate(run.finished_at);
  if (!startDate || !endDate) return null;
  return Math.max(0, endDate.getTime() - startDate.getTime());
}

export function formatDuration(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}
