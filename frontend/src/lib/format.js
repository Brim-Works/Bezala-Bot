/* Formattering via Intl.* — locale läses från i18n-värdet 'sv' eller 'en'.
 * Vi använder sv-FI och en-FI eftersom appen är finsk-svenskt orienterad
 * (Bezala-kunder, EUR som default-valuta). */

function localeFor(lang) {
  return lang === 'en' ? 'en-FI' : 'sv-FI';
}

export function fmtDate(iso, lang) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
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
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
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
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const now = new Date();
  return (
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  );
}

export function isWithinDays(iso, days) {
  if (!iso) return false;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return false;
  const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
  return d.getTime() >= cutoff;
}
