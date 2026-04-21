/* Filtrering + sökning för dashboardens tabell. Ren funktion — testbar
 * utan React. Förutsätter att meddelanden redan är dekorerade med
 * file_status + bezala_status (från api/adapters.withStatuses). */

export function applyFilter(messages, filter) {
  switch (filter) {
    case 'pending':
      return messages.filter((m) => m.bezala_status === 'pending');
    case 'auto':
      return messages.filter((m) => m.bezala_status === 'transferred');
    case 'errors':
      return messages.filter(
        (m) => m.file_status === 'error' || m.bezala_status === 'error',
      );
    case 'all':
    default:
      return messages;
  }
}

export function applySearch(messages, query) {
  if (!query) return messages;
  const q = query.trim().toLowerCase();
  if (!q) return messages;
  return messages.filter((m) => {
    const haystack = [
      m.vendor,
      m.file_name,
      m.subject,
      m.sender,
      m.category,
      m.amount != null ? String(m.amount) : '',
      m.currency,
      m.bezala_transaction_id,
    ]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    return haystack.includes(q);
  });
}

export function filterMessages(messages, { filter, query }) {
  return applySearch(applyFilter(messages, filter), query);
}
