import { fmtAmount } from '../../lib/format.js';

/* C56 — gemensamma hjälpare för de kompakta resekorten. */

export function fill(template, params) {
  if (!template) return '';
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    params && params[key] != null ? params[key] : '',
  );
}

function parse(iso) {
  if (!iso) return null;
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

/* '2026-10-21','2026-10-22' → '21–22 okt 2026' / '21–22 Oct 2026'. */
export function fmtRange(start, end, lang) {
  const a = parse(start);
  const b = parse(end || start);
  if (!a) return '—';
  const locale = lang === 'en' ? 'en-GB' : 'sv-SE';
  const day = (d) => d.getUTCDate();
  const mon = (d) =>
    new Intl.DateTimeFormat(locale, { month: 'short', timeZone: 'UTC' })
      .format(d)
      .replace('.', '');
  const year = b.getUTCFullYear();
  if (a.getTime() === b.getTime()) return `${day(a)} ${mon(a)} ${year}`;
  if (a.getUTCMonth() === b.getUTCMonth() && a.getUTCFullYear() === b.getUTCFullYear()) {
    return `${day(a)}–${day(b)} ${mon(a)} ${year}`;
  }
  return `${day(a)} ${mon(a)} – ${day(b)} ${mon(b)} ${year}`;
}

/* Summa per valuta: '668,00 EUR' eller '600,00 EUR + 1 200,00 NOK'. */
export function sumByCurrency(receipts, lang) {
  const sums = new Map();
  for (const r of receipts || []) {
    // C56: dubbletter (samma biljett två gånger) räknas en gång.
    if (r.amount == null || r.counted === false) continue;
    const cur = r.currency || 'EUR';
    sums.set(cur, (sums.get(cur) || 0) + Number(r.amount));
  }
  if (!sums.size) return null;
  return [...sums.entries()].map(([cur, n]) => fmtAmount(n, cur, lang)).join(' + ');
}

/* Databasen har engelska stadsnamn (Copenhagen); UI översätter. */
export function cityLabel(c, value) {
  if (!value) return value;
  return value
    .split(' → ')
    .map((part) => (c.cities && c.cities[part]) || part)
    .join(' → ');
}

const SV_TO_EN = {
  'Köpenhamn': 'Copenhagen', 'Göteborg': 'Gothenburg', 'Helsingfors': 'Helsinki',
  'München': 'Munich', 'Bryssel': 'Brussels', 'Wien': 'Vienna', 'Warszawa': 'Warsaw',
  'Rom': 'Rome', 'Lissabon': 'Lisbon', 'Prag': 'Prague', 'Uleåborg': 'Oulu',
  'Åbo': 'Turku', 'Tammerfors': 'Tampere', 'Århus': 'Aarhus',
};

/* AI-förslagens stad: destination, annars titeln fram till första siffran
 * ('Oslo 21 okt 2026' → 'Oslo'). Visas på valt språk. */
export function suggestionCity(c, s) {
  let raw = s.destination;
  if (!raw) {
    const m = /^([^\d]+?)\s*\d/.exec(s.title || '');
    raw = m ? m[1] : s.title;
  }
  if (!raw) return '—';
  // 'Stockholm, Sverige' → 'Stockholm' (landet står inte på raden).
  return raw
    .split(',')[0]
    .split(/\s*(?:&|\/|→| och | and )\s*/)
    .filter(Boolean)
    .map((p) => {
      const en = SV_TO_EN[p] || p;
      return (c.cities && c.cities[en]) || en;
    })
    .join(' & ');
}

/* C58 — vilka kvitton som saknas, för chippets tooltip:
 * 'Hotell 2026-01-21, Parkering HEL, kortbetalning 2026-01-22'. */
export function missingReceiptsLabel(check, comp) {
  const list = (comp && comp.missing_receipts) || [];
  return list
    .map((m) => {
      if (m.kind === 'card_line') {
        const base = fill(check.cardLineShort, { date: m.slot || '' });
        return m.vendor ? `${base} (${m.vendor})` : base;
      }
      const name = (check.kind && check.kind[m.kind]) || m.kind;
      return m.slot ? `${name} ${m.slot}` : name;
    })
    .join(', ');
}

/* C58 — kvittochippet: text, klass och tooltip. null = inget chip. */
export function receiptsChip(check, comp) {
  if (!comp) return null;
  if (comp.status === 'complete') {
    if (comp.waiting > 0) {
      return { cls: 'pill pill--warn mono', text: fill(check.waiting, { count: comp.waiting }), title: '' };
    }
    return { cls: 'pill pill--ok mono', text: check.complete, title: '' };
  }
  const list = missingReceiptsLabel(check, comp);
  return {
    cls: 'pill pill--err mono',
    text: comp.missing === 1 ? check.incompleteOne : fill(check.incomplete, { count: comp.missing }),
    title: list ? fill(check.missingTitle, { list }) : '',
    list,
  };
}
