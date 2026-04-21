// Finnish vendors + realistic data
const VENDORS = [
  { name: 'Finnair',           category: 'travel',  logo: 'FI', hue: 210 },
  { name: 'VR',                category: 'travel',  logo: 'VR', hue: 140 },
  { name: 'HSL',               category: 'travel',  logo: 'HS', hue: 30  },
  { name: 'Helsingin Taksi',   category: 'travel',  logo: 'HT', hue: 50  },
  { name: 'Scandic Grand Marina', category: 'lodging', logo: 'SC', hue: 200 },
  { name: 'Hotel Kämp',        category: 'lodging', logo: 'KÄ', hue: 20  },
  { name: 'Ravintola Savotta', category: 'meals',   logo: 'SA', hue: 10  },
  { name: 'Fazer Café',        category: 'meals',   logo: 'FA', hue: 340 },
  { name: 'Paulig Kulma',      category: 'meals',   logo: 'PA', hue: 25  },
  { name: 'K-Market Töölö',    category: 'supplies',logo: 'K',  hue: 350 },
  { name: 'Stockmann',         category: 'supplies',logo: 'ST', hue: 340 },
  { name: 'Verkkokauppa.com',  category: 'supplies',logo: 'VK', hue: 260 },
  { name: 'Clas Ohlson',       category: 'supplies',logo: 'CO', hue: 200 },
  { name: 'Neste',             category: 'travel',  logo: 'NE', hue: 145 },
  { name: 'ABC Asema',         category: 'travel',  logo: 'AB', hue: 15  },
];

const SUBJECT_TEMPLATES = {
  travel: [
    'Kuitti matkastasi — {vendor}',
    'Your receipt from {vendor}',
    'Lentokuitti / E-ticket confirmation',
    'Booking confirmation #{num}',
  ],
  lodging: [
    'Majoituskuitti — {vendor}',
    'Thank you for staying at {vendor}',
    'Hotel folio #{num}',
  ],
  meals: [
    'Kiitos käynnistäsi — {vendor}',
    'Receipt from {vendor}',
    'Lounas {date}',
  ],
  supplies: [
    'Tilausvahvistus #{num}',
    'Your order from {vendor}',
    'Kuitti — {vendor}',
  ],
};

const SENDERS = {
  'Finnair': 'noreply@finnair.com',
  'VR': 'asiakaspalvelu@vr.fi',
  'HSL': 'liput@hsl.fi',
  'Helsingin Taksi': 'kuitit@taksihelsinki.fi',
  'Scandic Grand Marina': 'receipts@scandichotels.com',
  'Hotel Kämp': 'info@hotelkamp.fi',
  'Ravintola Savotta': 'kuitti@savotta.fi',
  'Fazer Café': 'kuitit@fazer.fi',
  'Paulig Kulma': 'kulma@paulig.fi',
  'K-Market Töölö': 'sposti@k-market.fi',
  'Stockmann': 'verkkokauppa@stockmann.com',
  'Verkkokauppa.com': 'tilaukset@verkkokauppa.com',
  'Clas Ohlson': 'order@clasohlson.fi',
  'Neste': 'kuitit@neste.fi',
  'ABC Asema': 'kuitit@abcasemat.fi',
};

function seeded(n) {
  // Deterministic pseudo-random for stable mock data
  let x = Math.sin(n) * 10000;
  return x - Math.floor(x);
}

function pad(n, w = 2) { return String(n).padStart(w, '0'); }

function makeDate(daysAgo, hour, minute) {
  const d = new Date(2026, 3, 21, 9, 0, 0); // April 21, 2026
  d.setDate(d.getDate() - daysAgo);
  d.setHours(hour, minute, 0, 0);
  return d;
}

function fmtDateKey(d) {
  return `${d.getFullYear()}${pad(d.getMonth()+1)}${pad(d.getDate())}`;
}

function buildFilename(vendor, date, tail) {
  return `${fmtDateKey(date)} ${vendor.name}${tail ? ' ' + tail : ''}.pdf`;
}

function makeMessages() {
  const rows = [];
  const now = new Date(2026, 3, 21, 10, 30, 0);

  const entries = [
    // Needs review (pending)
    { v: 'Finnair',          amount: 284.50, vat: 10, tail: 'HEL-CPH', status: 'pending',  conf: 96, daysAgo: 0, hour: 9,  min: 12, note: 'Edestakainen lento Köpenhamninkongressiin' },
    { v: 'Hotel Kämp',       amount: 612.00, vat: 10, tail: '2 yötä', status: 'pending',  conf: 92, daysAgo: 0, hour: 8,  min: 45 },
    { v: 'Helsingin Taksi',  amount:  38.20, vat: 10, tail: 'HEL-keskusta', status: 'pending', conf: 88, daysAgo: 0, hour: 7,  min: 55 },
    { v: 'Ravintola Savotta',amount:  94.80, vat: 14, tail: 'illallinen 2hlö', status: 'pending', conf: 71, daysAgo: 1, hour: 21, min: 14 },
    { v: 'Paulig Kulma',     amount:   6.40, vat: 14, tail: '',        status: 'pending',  conf: 64, daysAgo: 1, hour: 8,  min: 20 },
    { v: 'Neste',            amount:  72.10, vat: 25.5, tail: 'polttoaine', status: 'pending', conf: 98, daysAgo: 1, hour: 17, min: 3 },
    { v: 'K-Market Töölö',   amount:  18.95, vat: 14, tail: '',        status: 'pending',  conf: 58, daysAgo: 2, hour: 12, min: 2 },
    { v: 'VR',               amount:  49.00, vat: 10, tail: 'HEL-TKU', status: 'pending',  conf: 94, daysAgo: 2, hour: 6,  min: 40 },
    // Already saved
    { v: 'Stockmann',        amount: 129.90, vat: 25.5, tail: '',      status: 'saved',    conf: 99, daysAgo: 3, hour: 14, min: 10 },
    { v: 'Fazer Café',       amount:  14.20, vat: 14, tail: '',        status: 'saved',    conf: 95, daysAgo: 3, hour: 9,  min: 32 },
    { v: 'Verkkokauppa.com', amount: 899.00, vat: 25.5, tail: 'USB-C hub', status: 'saved', conf: 99, daysAgo: 4, hour: 16, min: 58 },
    { v: 'Scandic Grand Marina', amount: 845.00, vat: 10, tail: '3 yötä', status: 'saved', conf: 97, daysAgo: 5, hour: 11, min: 22 },
    { v: 'ABC Asema',        amount:  58.40, vat: 25.5, tail: 'polttoaine', status: 'saved', conf: 96, daysAgo: 6, hour: 18, min: 5 },
    { v: 'Clas Ohlson',      amount:  34.50, vat: 25.5, tail: '',      status: 'saved',    conf: 94, daysAgo: 7, hour: 13, min: 45 },
    { v: 'HSL',              amount:  63.80, vat: 10, tail: 'kuukausilippu', status: 'saved', conf: 98, daysAgo: 8, hour: 7, min: 15 },
    // Errors / skipped
    { v: 'Finnair',          amount:   0.00, vat: 0, tail: '', status: 'error',   conf: 0,  daysAgo: 2, hour: 10, min: 5, error: 'Ei liitettä / No PDF attachment' },
    { v: 'VR',               amount:   0.00, vat: 0, tail: '', status: 'skipped', conf: 0,  daysAgo: 4, hour: 10, min: 5, note: 'Duplikat — redan i Drive' },
  ];

  entries.forEach((e, i) => {
    const vendor = VENDORS.find(v => v.name === e.v);
    const date = makeDate(e.daysAgo, e.hour, e.min);
    const filename = e.status === 'error' ? null : buildFilename(vendor, date, e.tail);
    const tmpls = SUBJECT_TEMPLATES[vendor.category];
    const subj = tmpls[Math.floor(seeded(i+7) * tmpls.length)]
      .replace('{vendor}', vendor.name)
      .replace('{num}', String(100000 + Math.floor(seeded(i+11) * 899999)))
      .replace('{date}', `${pad(date.getDate())}.${pad(date.getMonth()+1)}`);
    rows.push({
      id: i + 1,
      message_id: `m${100000 + i}`,
      vendor,
      sender: SENDERS[vendor.name],
      subject: subj,
      received_at: date,
      processed_at: new Date(date.getTime() + 1000 * 60 * (5 + (i % 20))),
      file_name: filename,
      amount: e.amount,
      vat_rate: e.vat,
      currency: 'EUR',
      confidence: e.conf,
      status: e.status,
      error_message: e.error || null,
      note: e.note || null,
      tail: e.tail,
      drive_link: e.status === 'saved' ? `https://drive.google.com/file/d/abc${i}/view` : null,
      category: vendor.category,
      project: i % 3 === 0 ? 'Kongressi 2026 Q2' : (i % 3 === 1 ? 'Toimisto' : 'Myynti'),
      payment: i % 2 === 0 ? 'Yritysluotto •••• 4417' : 'Yritysluotto •••• 0192',
    });
  });

  return rows;
}

function makeScanRuns() {
  const runs = [];
  // Associate each message with a run by ordering messages newest-first and bucketing
  for (let i = 0; i < 14; i++) {
    const started = new Date(2026, 3, 21, 10 - i, 0, 0);
    started.setHours(started.getHours() - i);
    const found = Math.floor(seeded(i+3) * 7);
    const errors = seeded(i+9) > 0.88 ? 1 : 0;
    const processed = Math.max(0, found - errors);
    // Per-stage timing (ms) — realistic pipeline breakdown
    const gmailMs = 200 + Math.floor(seeded(i+11) * 400);
    const aiMs = processed > 0 ? 800 + Math.floor(seeded(i+13) * 1800) : 0;
    const driveMs = processed > 0 ? 300 + Math.floor(seeded(i+17) * 500) : 0;
    const bezalaMs = processed > 0 ? 400 + Math.floor(seeded(i+19) * 700) : 0;
    const totalMs = gmailMs + aiMs + driveMs + bezalaMs;
    runs.push({
      id: i + 1,
      started_at: started,
      finished_at: new Date(started.getTime() + totalMs),
      messages_found: found,
      messages_processed: processed,
      messages_skipped: Math.floor(seeded(i+1) * 3),
      errors,
      status: errors ? 'partial' : (found === 0 ? 'idle' : 'ok'),
      stages: {
        gmail: { duration_ms: gmailMs, status: 'ok', note: `${found} ${found===1?'meddelande':'meddelanden'} hittade` },
        ai: { duration_ms: aiMs, status: processed>0?'ok':'idle',
              note: processed>0 ? `${processed} analyserade · ${Math.round(250 + seeded(i+2)*180)} tokens/st` : 'Inget att analysera',
              tokens_in: processed * Math.round(500 + seeded(i+4)*200),
              tokens_out: processed * Math.round(180 + seeded(i+5)*80),
              cost_cents: processed * (2 + Math.floor(seeded(i+6)*3)) },
        drive: { duration_ms: driveMs, status: processed>0?'ok':'idle',
                 note: processed>0 ? `${processed} ${processed===1?'fil':'filer'} uppladdade` : 'Inget att ladda upp' },
        bezala: { duration_ms: bezalaMs,
                  status: errors ? 'error' : (processed>0?'ok':'idle'),
                  note: errors ? 'Överföring misslyckades — 422 Unprocessable' :
                        (processed>0 ? `${Math.max(0,processed-1)} auto · ${processed>2?1:0} väntar granskning` : 'Inget att överföra'),
                  auto: Math.max(0, processed - (seeded(i+23)>0.6?1:0)),
                  queued: processed > 2 ? 1 : 0 },
      },
      message_ids: [],  // populated below
    });
  }
  // Distribute mock messages into runs (newest-first)
  const msgs = [...window.MOCK_MESSAGES].sort((a,b) => b.processed_at - a.processed_at);
  let mIdx = 0;
  for (const r of runs) {
    const take = Math.min(r.messages_processed + (r.errors||0), msgs.length - mIdx);
    r.message_ids = msgs.slice(mIdx, mIdx + take).map(m => m.id);
    mIdx += take;
  }
  return runs;
}

window.MOCK_MESSAGES = makeMessages();
window.MOCK_RUNS = makeScanRuns();
window.VENDORS = VENDORS;
