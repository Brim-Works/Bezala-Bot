const I18N = {
  sv: {
    app: 'Bezala Bot',
    tagline: 'Livet på en pinne :-)',
    nav: { dashboard: 'Översikt', review: 'Granska', log: 'Logg', settings: 'Inställningar' },
    scan: 'Kör scanning nu',
    scanning: 'Scannar…',
    logout: 'Logga ut',
    stats: {
      pending: 'Väntar på granskning',
      saved: 'Sparade till Drive',
      transferred: 'Överförda till Bezala',
      errors: 'Fel',
      lastRun: 'Senaste scanning',
      nextRun: 'Nästa scanning om',
      mins: 'min',
    },
    sections: {
      processed: 'Senast bearbetade',
      queue: 'Granska-kö',
      runs: 'Scanning-historik',
      details: 'Detaljer',
    },
    cols: {
      time: 'Tid', from: 'Från', subject: 'Ämne', vendor: 'Leverantör',
      file: 'Filnamn', amount: 'Belopp', status: 'Status', confidence: 'Säkerhet',
      category: 'Kategori', project: 'Projekt',
    },
    status: {
      pending: 'väntar', saved: 'sparad', error: 'fel', skipped: 'hoppad',
      transferred: 'överförd',
    },
    fileStatus: {
      saved: 'Sparad',
      error: 'Fel',
      skipped: 'Hoppad',
    },
    bezalaStatus: {
      transferred: 'Uppladdad',
      pending: 'Väntar',
      error: 'Fel',
    },
    statusHeaders: {
      file: 'Fil',
      bezala: 'Bezala',
    },
    filters: {
      all: 'Alla', needsReview: 'Behöver granskning', auto: 'Auto-klara', errors: 'Fel',
    },
    review: {
      title: 'Granska innan överföring',
      subtitle: 'AI har döpt och extraherat data. Godkänn för att överföra till Bezala.',
      empty: 'Inga kvitton väntar på granskning.',
      form: {
        vendor: 'Leverantör', date: 'Datum', amount: 'Belopp', currency: 'Valuta',
        vat: 'Moms', vatRate: 'Momssats', category: 'Kategori', project: 'Projekt',
        payment: 'Betalningssätt', note: 'Kommentar', filename: 'Filnamn',
      },
      approve: 'Godkänn och överför',
      approveAll: 'Godkänn alla',
      skip: 'Hoppa över',
      reject: 'Avvisa',
      next: 'Nästa',
      prev: 'Föregående',
      source: 'Källa',
      aiExtracted: 'AI extraherade',
      manualEdit: 'Manuellt redigerad',
    },
    settings: {
      title: 'Inställningar',
      interval: 'Scanningsintervall',
      rules: 'Scanning-regler',
      include: 'Inkludera avsändare',
      exclude: 'Exkludera avsändare',
      includeSubjects: 'Inkludera ämnen (substring)',
      excludeSubjects: 'Exkludera ämnen (substring)',
      ai: 'AI-namngivning',
      auto: 'Auto-upload till Bezala',
      threshold: 'Säkerhetströskel',
      save: 'Spara',
    },
    categories: { travel: 'Resa', lodging: 'Boende', meals: 'Representation', supplies: 'Material' },
  },
  en: {
    app: 'Bezala Bot',
    tagline: 'Receipts from Gmail → Drive → Bezala',
    nav: { dashboard: 'Overview', review: 'Review', log: 'Log', settings: 'Settings' },
    scan: 'Run scan now',
    scanning: 'Scanning…',
    logout: 'Log out',
    stats: {
      pending: 'Awaiting review',
      saved: 'Saved to Drive',
      transferred: 'Sent to Bezala',
      errors: 'Errors',
      lastRun: 'Last scan',
      nextRun: 'Next scan in',
      mins: 'min',
    },
    sections: {
      processed: 'Recently processed',
      queue: 'Review queue',
      runs: 'Scan history',
      details: 'Details',
    },
    cols: {
      time: 'Time', from: 'From', subject: 'Subject', vendor: 'Vendor',
      file: 'Filename', amount: 'Amount', status: 'Status', confidence: 'Confidence',
      category: 'Category', project: 'Project',
    },
    status: {
      pending: 'pending', saved: 'saved', error: 'error', skipped: 'skipped',
      transferred: 'sent',
    },
    fileStatus: {
      saved: 'Saved',
      error: 'Failed',
      skipped: 'Skipped',
    },
    bezalaStatus: {
      transferred: 'Uploaded',
      pending: 'Queued',
      error: 'Failed',
    },
    statusHeaders: {
      file: 'File',
      bezala: 'Bezala',
    },
    filters: {
      all: 'All', needsReview: 'Needs review', auto: 'Auto-cleared', errors: 'Errors',
    },
    review: {
      title: 'Review before transfer',
      subtitle: 'AI named and extracted the data. Approve to send to Bezala.',
      empty: 'No receipts waiting for review.',
      form: {
        vendor: 'Vendor', date: 'Date', amount: 'Amount', currency: 'Currency',
        vat: 'VAT', vatRate: 'VAT rate', category: 'Category', project: 'Project',
        payment: 'Payment method', note: 'Note', filename: 'Filename',
      },
      approve: 'Approve & send',
      approveAll: 'Approve all',
      skip: 'Skip',
      reject: 'Reject',
      next: 'Next',
      prev: 'Previous',
      source: 'Source',
      aiExtracted: 'AI extracted',
      manualEdit: 'Manually edited',
    },
    settings: {
      title: 'Settings',
      interval: 'Scan interval',
      rules: 'Scan rules',
      include: 'Include senders',
      exclude: 'Exclude senders',
      includeSubjects: 'Include subjects (substring)',
      excludeSubjects: 'Exclude subjects (substring)',
      ai: 'AI naming',
      auto: 'Auto-upload to Bezala',
      threshold: 'Confidence threshold',
      save: 'Save',
    },
    categories: { travel: 'Travel', lodging: 'Lodging', meals: 'Meals', supplies: 'Supplies' },
  },
};

function useI18n(lang) {
  return I18N[lang] || I18N.sv;
}

function fmtAmount(n, currency, lang) {
  const locale = lang === 'sv' ? 'sv-FI' : 'en-FI';
  return new Intl.NumberFormat(locale, { style: 'currency', currency: currency || 'EUR', minimumFractionDigits: 2 }).format(n);
}

function fmtDateTime(d, lang) {
  if (!d) return '—';
  const locale = lang === 'sv' ? 'sv-FI' : 'en-GB';
  return new Intl.DateTimeFormat(locale, { dateStyle: 'short', timeStyle: 'short' }).format(d);
}

function fmtRelative(d, lang, now) {
  if (!d) return '—';
  const ref = now || new Date(2026, 3, 21, 10, 30, 0);
  const diff = Math.round((ref - d) / 1000);
  const rtf = new Intl.RelativeTimeFormat(lang === 'sv' ? 'sv' : 'en', { numeric: 'auto' });
  if (diff < 60) return rtf.format(-diff, 'second');
  if (diff < 3600) return rtf.format(-Math.round(diff/60), 'minute');
  if (diff < 86400) return rtf.format(-Math.round(diff/3600), 'hour');
  return rtf.format(-Math.round(diff/86400), 'day');
}

window.I18N = I18N;
window.useI18n = useI18n;
window.fmtAmount = fmtAmount;
window.fmtDateTime = fmtDateTime;
window.fmtRelative = fmtRelative;
