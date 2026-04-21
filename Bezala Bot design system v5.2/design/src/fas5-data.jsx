// FAS 5 mock data — built on FAS 4 VENDORS
const TRASH_ROWS = [
  { id:'t1', deleted_at:'2026-04-19T09:12:00Z', vendor:{name:'Outlook Calendar', logo:'OC', hue:215}, subject:'Kalenderinbjudan: Sprintplanering 2026-05-02', amount:0, currency:'EUR', reason:'calendar' },
  { id:'t2', deleted_at:'2026-04-18T14:03:00Z', vendor:{name:'Prisma', logo:'PR', hue:20},  subject:'Kampanjaerbjudande — 30% rabatt på kontorsmaterial', amount:0, currency:'EUR', reason:'spam' },
  { id:'t3', deleted_at:'2026-04-17T11:22:00Z', vendor:{name:'Finnair', logo:'FI', hue:210}, subject:'Tack för din bokning — men ej kvitto',  amount:489, currency:'EUR', reason:'misclassified' },
  { id:'t4', deleted_at:'2026-04-15T16:44:00Z', vendor:{name:'Newsletter Co', logo:'NC', hue:280}, subject:'Weekly digest — Logistics trends in April',   amount:0, currency:'EUR', reason:'spam' },
  { id:'t5', deleted_at:'2026-04-12T08:30:00Z', vendor:{name:'Teams', logo:'MT', hue:240}, subject:'Möte: 1:1 med Anders',   amount:0, currency:'EUR', reason:'calendar' },
  { id:'t6', deleted_at:'2026-04-10T10:10:00Z', vendor:{name:'K-Market Töölö', logo:'K', hue:350}, subject:'Duplicate confirmation #48120',   amount:34.20, currency:'EUR', reason:'misclassified' },
];

const RULES = [
  {
    id:'r1', name:'Finnair-kvitton', active:true,
    match:{ from:'eticket@amadeus.com', subject_any:['Finnair','FLIGHT','ETICKET'], has_attachment:true, min_amount:0, currency:'EUR' },
    action:{ category:'Resa', bezala_account:'5811', auto_upload:true, notify_first:false },
    stats:{ matches_30d: 14 }
  },
  {
    id:'r2', name:'Scandic-hotell', active:true,
    match:{ from:'reservations@scandichotels.com', subject_any:['folio','kvitto','stay'], has_attachment:true, min_amount:50, currency:'EUR' },
    action:{ category:'Boende', bezala_account:'5831', auto_upload:true, notify_first:false },
    stats:{ matches_30d: 6 }
  },
  {
    id:'r3', name:'VR-tågbiljetter', active:true,
    match:{ from:'vr@vr.fi', subject_any:['Junalippu','Ticket'], has_attachment:true, min_amount:0, currency:'EUR' },
    action:{ category:'Resa', bezala_account:'5811', auto_upload:true, notify_first:false },
    stats:{ matches_30d: 22 }
  },
  {
    id:'r4', name:'Restaurang-kvitton', active:true,
    match:{ from:'', subject_any:['Ravintola','Restaurant','Lunch','Lounas'], has_attachment:true, min_amount:5, currency:'EUR' },
    action:{ category:'Mat', bezala_account:'5872', auto_upload:false, notify_first:true },
    stats:{ matches_30d: 8 }
  },
  {
    id:'r5', name:'Verkkokauppa.com', active:false,
    match:{ from:'noreply@verkkokauppa.com', subject_any:['Tilaus','Order'], has_attachment:true, min_amount:10, currency:'EUR' },
    action:{ category:'Kontor', bezala_account:'4111', auto_upload:false, notify_first:true },
    stats:{ matches_30d: 0 }
  },
];

const PATTERNS = [
  { id:'p1', vendor:'Finnair',     fields:{ kategori:'Resa', konto:'5811', valuta:'EUR', typical_range:'150–800 EUR' }, learned_from:14 },
  { id:'p2', vendor:'Scandic',     fields:{ kategori:'Boende', konto:'5831', valuta:'EUR', typical_range:'120–450 EUR' }, learned_from:6 },
  { id:'p3', vendor:'Neste',       fields:{ kategori:'Resa', konto:'5615', valuta:'EUR', typical_range:'30–90 EUR' }, learned_from:9 },
  { id:'p4', vendor:'Ravintola Savotta', fields:{ kategori:'Mat', konto:'5872', valuta:'EUR', moms:'14%' }, learned_from:5 },
];

// 7 days of feedback
const FEEDBACK = (() => {
  const out = [];
  for (let i=0;i<7;i++) {
    const d = new Date(Date.now() - i*86400*1000).toISOString();
    const pos = 5 + Math.floor(Math.random()*4);
    const neg = Math.floor(Math.random()*2);
    for (let j=0;j<pos;j++) out.push({ type:'positive', field:'amount', ts:d });
    for (let j=0;j<neg;j++) out.push({ type:'negative', field:'vat_rate', ts:d });
  }
  return out;
})();

const CARD_ROWS = [
  // Suggested (mid-confidence)
  { id:'c1', card_vendor:'FINNAIR', card_date:'2026-04-19', amount:489.00, currency:'EUR', status:'suggested', best_match:{confidence:0.92},
    candidates:[
      { id:'m1', vendor:{name:'Finnair', logo:'FI', hue:210}, amount:489.00, currency:'EUR', receipt_date:'2026-04-18', file_name:'20260418 Finnair Resa.pdf', confidence:0.92, reasons:['Belopp ±0€','Datum −1d','Leverantör exakt'] },
      { id:'m2', vendor:{name:'Finnair', logo:'FI', hue:210}, amount:489.50, currency:'EUR', receipt_date:'2026-04-17', file_name:'20260417 Finnair Uppgradering.pdf', confidence:0.71, reasons:['Belopp +0.50€','Datum −2d'] }
    ]},
  { id:'c2', card_vendor:'K-MARKET TÖÖLÖ', card_date:'2026-04-18', amount:34.20, currency:'EUR', status:'suggested', best_match:{confidence:0.88},
    candidates:[
      { id:'m3', vendor:{name:'K-Market Töölö', logo:'K', hue:350}, amount:34.20, currency:'EUR', receipt_date:'2026-04-18', file_name:'20260418 K-Market Kontor.pdf', confidence:0.88, reasons:['Belopp exakt','Datum exakt','Leverantör fuzzy'] }
    ]},
  { id:'c3', card_vendor:'NESTE HEL ARABIA', card_date:'2026-04-17', amount:62.40, currency:'EUR', status:'suggested', best_match:{confidence:0.84},
    candidates:[
      { id:'m4', vendor:{name:'Neste', logo:'NE', hue:145}, amount:62.40, currency:'EUR', receipt_date:'2026-04-16', file_name:'20260416 Neste Resa.pdf', confidence:0.84, reasons:['Belopp exakt','Datum −1d','Leverantör prefix-match'] }
    ]},
  // Auto-matched (high confidence)
  { id:'c4', card_vendor:'SCANDIC GRAND MARINA', card_date:'2026-04-16', amount:345.00, currency:'EUR', status:'auto', best_match:{confidence:0.98},
    candidates:[
      { id:'m5', vendor:{name:'Scandic Grand Marina', logo:'SC', hue:200}, amount:345.00, currency:'EUR', receipt_date:'2026-04-16', file_name:'20260416 Scandic Boende.pdf', confidence:0.98, reasons:['Belopp exakt','Datum exakt','Leverantör exakt'] }
    ]},
  { id:'c5', card_vendor:'VR FI', card_date:'2026-04-15', amount:78.00, currency:'EUR', status:'auto', best_match:{confidence:0.99},
    candidates:[
      { id:'m6', vendor:{name:'VR', logo:'VR', hue:140}, amount:78.00, currency:'EUR', receipt_date:'2026-04-15', file_name:'20260415 VR Resa.pdf', confidence:0.99, reasons:['Belopp exakt','Datum exakt','Leverantör exakt'] }
    ]},
  // Orphans
  { id:'c6', card_vendor:'APPLE.COM/BILL', card_date:'2026-04-12', amount:12.99, currency:'EUR', status:'orphan', best_match:null, candidates:[] },
  { id:'c7', card_vendor:'TAXI HELSINKI', card_date:'2026-04-10', amount:24.80, currency:'EUR', status:'orphan', best_match:null, candidates:[] },
];

window.FAS5_DATA = { TRASH_ROWS, RULES, PATTERNS, FEEDBACK, CARD_ROWS };
