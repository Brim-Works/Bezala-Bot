// Drawer that shows what happened to a selected message at each pipeline step.
const { useEffect: useEffectDr } = React;

function Drawer({ step, msg, lang, onClose, onGoReview }) {
  useEffectDr(() => {
    if (!step) return;
    function onKey(e) { if (e.key === 'Escape') onClose(); }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [step, onClose]);

  if (!step || !msg) return null;
  const d = msg.received_at;
  const dateStr = fmtDateTime(d, lang);

  const headers = {
    gmail:  { icon: I.Mail,     title: lang==='sv'?'Gmail-meddelande':'Gmail message', sub: msg.sender },
    ai:     { icon: I.Sparkle,  title: lang==='sv'?'AI-analys':'AI analysis',           sub: lang==='sv'?'Claude Haiku 4.5':'Claude Haiku 4.5' },
    drive:  { icon: I.Drive,    title: lang==='sv'?'Google Drive':'Google Drive',       sub: msg.file_name || (lang==='sv'?'Ingen bilaga':'No attachment') },
    bezala: { icon: I.Bezala,   title: 'Bezala',                                       sub: bezalaSub(msg, lang) },
  };
  const H = headers[step];
  const HIcon = H.icon;

  return (
    <>
      <div onClick={onClose} style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.35)', zIndex: 800, animation: 'fade .15s ease' }} />
      <aside style={{ position:'fixed', top:0, right:0, height:'100vh', width: 520, maxWidth:'92vw', background:'var(--surface)', borderLeft:'1px solid var(--border)', zIndex: 801, display:'flex', flexDirection:'column', boxShadow:'-24px 0 60px -20px rgba(0,0,0,0.45)' }}>
        <div style={{ padding:'16px 20px', borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', gap: 12 }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, display:'grid', placeItems:'center', background:'color-mix(in oklch, var(--accent) 14%, var(--surface-2))', color:'var(--accent)' }}>
            <HIcon size={18}/>
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{H.title}</div>
            <div style={{ fontSize: 12, color: 'var(--muted)', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{H.sub}</div>
          </div>
          <button className="btn ghost sm" onClick={onClose}><I.X size={14}/></button>
        </div>

        <div style={{ padding: 20, overflowY:'auto', flex: 1 }}>
          {step === 'gmail' && <GmailView msg={msg} lang={lang} dateStr={dateStr}/>}
          {step === 'ai' && <AIView msg={msg} lang={lang}/>}
          {step === 'drive' && <DriveView msg={msg} lang={lang}/>}
          {step === 'bezala' && <BezalaView msg={msg} lang={lang} onGoReview={onGoReview} onClose={onClose}/>}
        </div>
      </aside>
    </>
  );
}

function KV({ k, v }) {
  return (
    <div style={{ display:'grid', gridTemplateColumns:'110px 1fr', gap: 10, padding:'6px 0', borderBottom:'1px solid var(--border)', fontSize: 12.5 }}>
      <div style={{ color:'var(--muted)' }}>{k}</div>
      <div style={{ color:'var(--text)' }}>{v}</div>
    </div>
  );
}

function GmailView({ msg, lang, dateStr }) {
  const body = lang==='sv'
    ? `Hej,\n\nTack för köpet! Bifogat hittar du kuittin från ${msg.vendor.name}${msg.tail? ' ('+msg.tail+')': ''}.\n\nSumma: ${msg.amount.toFixed(2)} ${msg.currency}\nMoms: ${msg.vat_rate}%\nDatum: ${dateStr}\n\nKäyttäjä voi ladata kuitin myös palvelustamme.\n\nYstävällisin terveisin,\n${msg.vendor.name}`
    : `Hello,\n\nThank you for your purchase. Please find attached the receipt from ${msg.vendor.name}${msg.tail? ' ('+msg.tail+')': ''}.\n\nTotal: ${msg.amount.toFixed(2)} ${msg.currency}\nVAT: ${msg.vat_rate}%\nDate: ${dateStr}\n\nBest regards,\n${msg.vendor.name}`;
  return (
    <div>
      <KV k={lang==='sv'?'Från':'From'} v={<span className="mono" style={{fontSize:12}}>{msg.sender}</span>} />
      <KV k={lang==='sv'?'Till':'To'} v={<span className="mono" style={{fontSize:12}}>mikko.keinonen@visma.com</span>} />
      <KV k={lang==='sv'?'Ämne':'Subject'} v={<span style={{fontWeight:500}}>{msg.subject}</span>} />
      <KV k={lang==='sv'?'Tid':'Received'} v={<span className="mono" style={{fontSize:12}}>{dateStr}</span>} />
      <KV k={lang==='sv'?'Etiketter':'Labels'} v={
        <span style={{ display:'inline-flex', gap:4 }}>
          <Pill kind="accent">Inbox</Pill>
          {msg.status==='saved' && <Pill kind="ok">Bezala-Klar</Pill>}
        </span>
      } />
      <KV k={lang==='sv'?'Bilagor':'Attachments'} v={msg.file_name
        ? <span className="mono" style={{fontSize:12}}>📎 {msg.file_name}</span>
        : <span style={{color:'var(--muted)'}}>{lang==='sv'?'Inga':'None'}</span>} />

      <div style={{ marginTop: 16, fontSize: 11.5, color:'var(--muted)', textTransform:'uppercase', letterSpacing: 0.06 }}>
        {lang==='sv'?'Meddelandetext':'Message body'}
      </div>
      <pre style={{ marginTop: 8, padding: 14, background:'var(--bg-2)', border:'1px solid var(--border)', borderRadius: 6, fontSize: 12, lineHeight: 1.6, whiteSpace:'pre-wrap', fontFamily:'inherit', color:'var(--text-2)' }}>
        {body}
      </pre>

      <div style={{ marginTop: 16, display:'flex', gap: 8 }}>
        <button className="btn"><I.Mail size={13}/> {lang==='sv'?'Öppna i Gmail':'Open in Gmail'}</button>
      </div>
    </div>
  );
}

function AIView({ msg, lang }) {
  const extracted = [
    [lang==='sv'?'Leverantör':'Vendor', msg.vendor.name],
    [lang==='sv'?'Datum':'Date', fmtDateTime(msg.received_at, lang).split(',')[0]],
    [lang==='sv'?'Belopp':'Amount', fmtAmount(msg.amount, msg.currency, lang)],
    [lang==='sv'?'ALV / moms':'VAT', `${msg.vat_rate}%`],
    [lang==='sv'?'Kategori':'Category', msg.category],
    [lang==='sv'?'Filnamn':'Filename', msg.file_name || '—'],
  ];
  const reasoning = lang==='sv'
    ? `Identifierade avsändaren "${msg.sender}" som tillhörande ${msg.vendor.name}. Extraherade totalbelopp från raden "Yhteensä EUR" i PDF:en. Momssatsen ${msg.vat_rate}% matchar finsk standard för ${msg.category}. Byggde filnamn enligt mallen "YYYYMMDD Leverantör Detalj.pdf".`
    : `Identified sender "${msg.sender}" as belonging to ${msg.vendor.name}. Extracted total from the "Yhteensä EUR" line in the PDF. VAT rate ${msg.vat_rate}% matches Finnish standard for ${msg.category}. Built filename using template "YYYYMMDD Vendor Detail.pdf".`;
  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', gap: 10, padding:'12px 14px', background:'color-mix(in oklch, var(--accent) 10%, var(--surface-2))', border:'1px solid var(--border)', borderRadius: 8, marginBottom: 16 }}>
        <I.Sparkle size={16} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12.5, fontWeight: 500 }}>Claude Haiku 4.5</div>
          <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{lang==='sv'?'Analys slutförd':'Analysis complete'} · <Confidence value={msg.confidence}/></div>
        </div>
      </div>

      <div style={{ fontSize: 11.5, color:'var(--muted)', textTransform:'uppercase', letterSpacing: 0.06, marginBottom: 6 }}>
        {lang==='sv'?'Extraherade fält':'Extracted fields'}
      </div>
      {extracted.map(([k,v]) => <KV key={k} k={k} v={<span className="mono" style={{fontSize:12}}>{v}</span>} />)}

      <div style={{ marginTop: 18, fontSize: 11.5, color:'var(--muted)', textTransform:'uppercase', letterSpacing: 0.06 }}>
        {lang==='sv'?'Resonemang':'Reasoning'}
      </div>
      <div style={{ marginTop: 8, padding: 14, background:'var(--bg-2)', border:'1px solid var(--border)', borderRadius: 6, fontSize: 12.5, lineHeight: 1.6, color:'var(--text-2)' }}>
        {reasoning}
      </div>

      <div style={{ marginTop: 14, fontSize: 11, color:'var(--muted)' }}>
        <span className="mono">input: 1 PDF + email headers · output: JSON · {Math.round(50 + msg.id * 7)} tokens</span>
      </div>
    </div>
  );
}

function DriveView({ msg, lang }) {
  if (!msg.file_name) {
    return (
      <div style={{ padding: 40, textAlign:'center', color:'var(--muted)', fontSize: 13 }}>
        {lang==='sv'?'Ingen fil laddades upp — mailet saknade giltig PDF-bilaga.':'No file uploaded — the email had no valid PDF attachment.'}
      </div>
    );
  }
  return (
    <div>
      <KV k={lang==='sv'?'Filnamn':'Filename'} v={<span className="mono" style={{fontSize:12}}>{msg.file_name}</span>} />
      <KV k={lang==='sv'?'Mapp':'Folder'} v={<span className="mono" style={{fontSize:12}}>/Kvitton/{msg.received_at.getFullYear()}/Q{Math.floor(msg.received_at.getMonth()/3)+1}</span>} />
      <KV k={lang==='sv'?'Storlek':'Size'} v={<span className="mono" style={{fontSize:12}}>{(120 + msg.id*8)} KB</span>} />
      <KV k={lang==='sv'?'Uppladdat':'Uploaded'} v={<span className="mono" style={{fontSize:12}}>{fmtDateTime(msg.processed_at, lang)}</span>} />

      <div style={{ marginTop: 16 }}>
        {window.PdfMock && <window.PdfMock msg={msg} lang={lang} />}
      </div>

      <div style={{ marginTop: 16, display:'flex', gap: 8 }}>
        <button className="btn"><I.Download size={13}/> {lang==='sv'?'Ladda ner':'Download'}</button>
        <button className="btn"><I.Drive size={13}/> {lang==='sv'?'Öppna i Drive':'Open in Drive'}</button>
      </div>
    </div>
  );
}

function bezalaSub(msg, lang) {
  if (msg.status === 'pending') return lang==='sv'?'Väntar på granskning':'Awaiting review';
  if (msg.status === 'error')   return lang==='sv'?'Överföring misslyckades':'Transfer failed';
  if (msg.status === 'transferred') return lang==='sv'?'Överfört till Bezala':'Transferred to Bezala';
  return lang==='sv'?'Sparat i Bezala':'Saved in Bezala';
}

function BezalaView({ msg, lang, onGoReview, onClose }) {
  const handleGoReview = () => { onGoReview(msg.id); onClose(); };

  if (msg.status === 'pending') {
    return (
      <div>
        <div style={{ display:'flex', alignItems:'center', gap: 10, padding:'12px 14px', background:'color-mix(in oklch, var(--warn) 14%, var(--surface-2))', border:'1px solid var(--border)', borderRadius: 8, marginBottom: 16 }}>
          <I.Clock size={16} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 500 }}>{lang==='sv'?'Väntar på din granskning':'Awaiting your review'}</div>
            <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{lang==='sv'?'AI är osäker på något fält':'AI is uncertain about one or more fields'}</div>
          </div>
        </div>

        <div style={{ fontSize: 13, color:'var(--text-2)', lineHeight: 1.6, marginBottom: 16 }}>
          {lang==='sv'
            ? `Den här raden kunde inte överföras automatiskt eftersom AI:ns konfidens var under tröskeln för ett eller flera fält. Öppna granskningsvyn för att bekräfta fälten och överföra till Bezala.`
            : `This row could not be auto-transferred because the AI confidence was below threshold for one or more fields. Open the review view to confirm fields and transfer to Bezala.`}
        </div>

        <KV k={lang==='sv'?'Leverantör':'Vendor'} v={msg.vendor.name} />
        <KV k={lang==='sv'?'Belopp':'Amount'} v={<span className="mono" style={{fontSize:12}}>{fmtAmount(msg.amount, msg.currency, lang)}</span>} />
        <KV k={lang==='sv'?'Konfidens':'Confidence'} v={<Confidence value={msg.confidence}/>} />
        <KV k={lang==='sv'?'Föreslagen kategori':'Suggested category'} v={<span className="mono" style={{fontSize:12}}>{msg.category}</span>} />

        <div style={{ marginTop: 20, display:'flex', gap: 8 }}>
          <button className="btn primary" onClick={handleGoReview}>
            <I.Check size={13}/> {lang==='sv'?'Öppna granskning':'Open review'} →
          </button>
          <button className="btn ghost" onClick={onClose}>{lang==='sv'?'Senare':'Later'}</button>
        </div>
      </div>
    );
  }

  if (msg.status === 'error') {
    return (
      <div>
        <div style={{ display:'flex', alignItems:'center', gap: 10, padding:'12px 14px', background:'color-mix(in oklch, var(--err) 14%, var(--surface-2))', border:'1px solid var(--border)', borderRadius: 8, marginBottom: 16 }}>
          <I.Alert size={16} />
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5, fontWeight: 500 }}>{lang==='sv'?'Överföring misslyckades':'Transfer failed'}</div>
            <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{lang==='sv'?'Senaste försök':'Last attempt'}: {fmtDateTime(msg.processed_at, lang)}</div>
          </div>
        </div>

        <div style={{ fontSize: 11.5, color:'var(--muted)', textTransform:'uppercase', letterSpacing: 0.06, marginBottom: 6 }}>
          {lang==='sv'?'Felmeddelande':'Error message'}
        </div>
        <pre style={{ padding: 12, background:'var(--bg-2)', border:'1px solid var(--border)', borderRadius: 6, fontSize: 11.5, color:'var(--err)', fontFamily:'var(--font-mono)', whiteSpace:'pre-wrap', margin: 0 }}>
          Bezala API: 422 Unprocessable Entity{'\n'}→ "vat_rate" does not match any registered VAT profile for this organization.
        </pre>

        <div style={{ marginTop: 16 }}>
          <KV k={lang==='sv'?'Leverantör':'Vendor'} v={msg.vendor.name} />
          <KV k={lang==='sv'?'Belopp':'Amount'} v={<span className="mono" style={{fontSize:12}}>{fmtAmount(msg.amount, msg.currency, lang)}</span>} />
          <KV k={lang==='sv'?'Försök':'Attempts'} v={<span className="mono" style={{fontSize:12}}>3</span>} />
        </div>

        <div style={{ marginTop: 20, display:'flex', gap: 8 }}>
          <button className="btn primary" onClick={handleGoReview}><I.Refresh size={13}/> {lang==='sv'?'Åtgärda och försök igen':'Fix and retry'}</button>
          <button className="btn ghost" onClick={onClose}>{lang==='sv'?'Stäng':'Close'}</button>
        </div>
      </div>
    );
  }

  // saved / transferred
  const transferId = `BZL-${String(msg.id).padStart(4,'0')}-${msg.received_at.getFullYear()}`;
  const account = accountFor(msg.category);
  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', gap: 10, padding:'12px 14px', background:'color-mix(in oklch, var(--ok) 14%, var(--surface-2))', border:'1px solid var(--border)', borderRadius: 8, marginBottom: 16 }}>
        <I.Check size={16} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12.5, fontWeight: 500 }}>{lang==='sv'?'Överfört till Bezala':'Transferred to Bezala'}</div>
          <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{fmtDateTime(msg.processed_at, lang)}</div>
        </div>
      </div>

      <div style={{ fontSize: 11.5, color:'var(--muted)', textTransform:'uppercase', letterSpacing: 0.06, marginBottom: 6 }}>
        {lang==='sv'?'Bezala-kvitto':'Bezala receipt'}
      </div>
      <KV k={lang==='sv'?'Transaktions-ID':'Transaction ID'} v={<span className="mono" style={{fontSize:12}}>{transferId}</span>} />
      <KV k={lang==='sv'?'Leverantör':'Vendor'} v={msg.vendor.name} />
      <KV k={lang==='sv'?'Belopp':'Amount'} v={<span className="mono" style={{fontSize:12}}>{fmtAmount(msg.amount, msg.currency, lang)}</span>} />
      <KV k={lang==='sv'?'Moms':'VAT'} v={<span className="mono" style={{fontSize:12}}>{msg.vat_rate}%</span>} />
      <KV k={lang==='sv'?'Kategori':'Category'} v={<span className="mono" style={{fontSize:12}}>{msg.category}</span>} />
      <KV k={lang==='sv'?'Bokföringskonto':'Account'} v={<span className="mono" style={{fontSize:12}}>{account}</span>} />
      <KV k={lang==='sv'?'Godkänt av':'Approved by'} v={msg.status === 'transferred'
        ? (lang==='sv'?'Auto (konfidens ≥ 90%)':'Auto (confidence ≥ 90%)')
        : 'Mikko Keinonen'} />

      <div style={{ marginTop: 20, display:'flex', gap: 8 }}>
        <button className="btn"><I.Bezala size={13}/> {lang==='sv'?'Öppna i Bezala':'Open in Bezala'}</button>
        <button className="btn ghost"><I.Download size={13}/> {lang==='sv'?'Exportera':'Export'}</button>
      </div>
    </div>
  );
}

function accountFor(cat) {
  const map = {
    'Resor': '4411',
    'Travel': '4411',
    'Kontorsmaterial': '4010',
    'Office supplies': '4010',
    'Programvara': '4520',
    'Software': '4520',
    'Representation': '4620',
    'Utbildning': '4710',
    'Training': '4710',
    'Övrigt': '4999',
    'Other': '4999',
  };
  return map[cat] || '4999';
}

window.Drawer = Drawer;
