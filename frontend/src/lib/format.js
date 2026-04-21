/* Formattering via Intl.* — locale läses från i18n-värdet 'sv' eller 'en'.
 * Vi använder sv-FI och en-FI eftersom appen är finsk-svenskt orienterad
 * (Bezala-kunder, EUR som default-valuta). */

function localeFor(lang) {
  return lang === 'en' ? 'en-FI' : 'sv-FI';
}

/* Backend serialiserar naiva UTC-datetimes via .isoformat() → saknar
 * tz-suffix (t.ex. "2026-04-21T10:30:00"). JavaScripts Date-parser tolkar
 * sådana strängar som LOKAL tid, vilket ger fel tidsstämplar beroende på
 * browserns tidszon. Här normaliserar vi: om strängen saknar Z / ±hh:mm
 * sätter vi 'Z' så den tolkas som UTC.
 *
 * deleted_at använder DateTime(timezone=True) och kommer med offset —
 * regex:en nedan släpper igenom sådana oförändrade. */
export function parseBackendDate(input) {
  if (input == null) return null;
  if (input instanceof Date) {
    return Number.isNaN(input.getTime()) ? null : input;
  }
  if (typeof input !== 'string') return null;
  const trimmed = input.trim();
  if (!trimmed) return null;
  const hasTz = /([zZ]|[+-]\d{2}:?\d{2})$/.test(trimmed);
  const d = new Date(hasTz ? trimmed : `${trimmed}Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function fmtDate(iso, lang) {
  const d = parseBackendDate(iso);
  if (!d) return '—';
  return new Intl.DateTimeFormat(localeFor(lang), {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d);
}

const RT_DIVISIONS = [
  { amount: 60, name: 'seconds' },
  { amount: 60, name: 'minutes' },
  { amount: 24, name: 'hours' },
  { amount: 7, name: 'days' },
  { amount: 4.34524, name: 'weeks' },
  { amount: 12, name: 'months' },
  { amount: Number.POSITIVE_INFINITY, name: 'years' },
];

export function fmtRelative(iso, lang) {
  const d = parseBackendDate(iso);
  if (!d) return '—';
  const rtf = new Intl.RelativeTimeFormat(localeFor(lang), { numeric: 'auto' });
  let duration = (d.getTime() - Date.now()) / 1000;
  for (const division of RT_DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return rtf.format(Math.round(duration), division.name);
    }
    duration /= division.amount;
  }
  return rtf.format(Math.round(duration), 'years');
}

export function fmtAmount(amount, currency, lang) {
  if (amount === null || amount === undefined) return '—';
  const n = Number(amount);
  if (Number.isNaN(n)) return '—';
  const opts = {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  };
  if (currency) {
    opts.style = 'currency';
    opts.currency = currency;
    opts.currencyDisplay = 'code';
  }
  try {
    return new Intl.NumberFormat(localeFor(lang), opts).format(n);
  } catch {
    // Ogiltig valutakod → fall tillbaka på rena siffror + ev. suffix
    const num = new Intl.NumberFormat(localeFor(lang), {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(n);
    return currency ? `${num} ${currency}` : num;
  }
}

export function isToday(iso) {
  const d = parseBackendDate(iso);
  if (!d) return false;
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

export function isWithinDays(iso, days) {
  const d = parseBackendDate(iso);
  if (!d) return false;
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return d.getTime() >= cutoff;
}
