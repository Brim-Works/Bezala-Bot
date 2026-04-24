/* Härled leverantörsnamn från sender-domän när backend inte har
 * extraherat vendor än (t.ex. link_fetch-rader innan PDFen hämtas, eller
 * error-rader där AI kraschat). Håller en hårdkodad lista över kända
 * avsändardomäner → visningsnamn. Returnerar null om ingen matchar.
 *
 * Accepterar både rena adresser (noreply@skanetrafiken.se) och
 * RFC-format ("Skånetrafiken <noreply@skanetrafiken.se>").
 */

const VENDOR_BY_DOMAIN = {
  'arlandaexpress.se': 'Arlanda Express',
  'skanetrafiken.se': 'Skånetrafiken',
  'moovy.fi': 'Moovy',
  'finnair.com': 'Finnair',
  'amadeus.com': 'Amadeus',
  'anthropic.com': 'Anthropic',
  'mail.anthropic.com': 'Anthropic',
  'strawberry.se': 'Strawberry',
  'flytoget.no': 'Flytoget',
  'scandichotels.com': 'Scandic Hotels',
};

function extractDomain(sender) {
  if (!sender || typeof sender !== 'string') return null;
  const match = /@([^>\s]+)/.exec(sender);
  if (!match) return null;
  return match[1].toLowerCase().replace(/[>\s.,;]+$/, '');
}

export function vendorFromSender(sender) {
  const domain = extractDomain(sender);
  if (!domain) return null;
  if (VENDOR_BY_DOMAIN[domain]) return VENDOR_BY_DOMAIN[domain];
  const parts = domain.split('.');
  for (let i = 0; i < parts.length - 1; i += 1) {
    const suffix = parts.slice(i).join('.');
    if (VENDOR_BY_DOMAIN[suffix]) return VENDOR_BY_DOMAIN[suffix];
  }
  return null;
}

export function displayVendor(row) {
  if (!row) return null;
  return row.vendor || vendorFromSender(row.sender) || null;
}
