/* Stabil hue + initialer från leverantörsnamn — rena funktioner.
 * Backend levererar inget logo-data, så vi deriverar visuell identitet
 * från strängen. Samma input ger alltid samma output. */

const FALLBACK = '??';
const STOPWORDS = new Set([
  'ab',
  'oy',
  'oyj',
  'inc',
  'llc',
  'ltd',
  'plc',
  'gmbh',
  'sa',
  'aps',
  'as',
  'sas',
]);

export function initials(name) {
  if (!name || typeof name !== 'string') return FALLBACK;
  const cleaned = name.replace(/[^\p{L}\p{N}\s]+/gu, ' ').trim();
  if (!cleaned) return FALLBACK;
  const words = cleaned
    .split(/\s+/)
    .filter((w) => !STOPWORDS.has(w.toLowerCase()));
  if (words.length === 0) return cleaned.slice(0, 2).toUpperCase();
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[1][0]).toUpperCase();
}

export function hueFromName(name) {
  if (!name || typeof name !== 'string') return 220;
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return hash % 360;
}
