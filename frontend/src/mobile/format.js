export function money(amount, currency) {
  if (amount === null || amount === undefined || amount === '') return '—';
  const n = Number(amount);
  if (Number.isNaN(n)) return String(amount);
  return `${n.toLocaleString('sv-SE', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${currency || ''}`.trim();
}

export function day(value) {
  if (!value) return '—';
  return String(value).slice(0, 10);
}

export function errorText(err) {
  if (!err) return '';
  return err.message || String(err);
}
