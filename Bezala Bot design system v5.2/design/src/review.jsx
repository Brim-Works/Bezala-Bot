const { useState: useStateR, useMemo: useMemoR } = React;

function PdfMock({ msg, lang }) {
  if (!msg) return <div className="pdf-body"><div style={{color:'var(--muted)', fontSize:12}}>{lang==='sv'?'Inget valt':'Nothing selected'}</div></div>;
  const v = msg.vendor;
  const d = msg.received_at;
  const net = msg.amount ? (msg.amount / (1 + (msg.vat_rate||0)/100)) : 0;
  const vat = msg.amount - net;
  const dateStr = `${String(d.getDate()).padStart(2,'0')}.${String(d.getMonth()+1).padStart(2,'0')}.${d.getFullYear()}`;
  return (
    <div className="pdf-body">
      <div className="pdf-page">
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', borderBottom:'1px solid #d8d1c0', paddingBottom: 12, marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, letterSpacing: -0.5 }}>{v.name}</div>
            <div style={{ fontSize: 9, color: '#666', marginTop: 2 }}>Y-tunnus 1234567-8</div>
            <div style={{ fontSize: 9, color: '#666' }}>Mannerheimintie 12, 00100 Helsinki</div>
          </div>
          <div style={{ width: 38, height: 38, background: `oklch(55% 0.12 ${v.hue})`, borderRadius: 3, display: 'grid', placeItems: 'center', color: 'white', fontSize: 12, fontWeight: 700 }}>
            {v.logo}
          </div>
        </div>

        <div style={{ fontSize: 10, color: '#333', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 2 }}>KUITTI / RECEIPT</div>
        <div style={{ fontSize: 9, color: '#666', marginBottom: 14 }}>Nro {100000 + msg.id} · {dateStr} · {String(d.getHours()).padStart(2,'0')}:{String(d.getMinutes()).padStart(2,'0')}</div>

        <div style={{ borderTop: '1px dashed #c9c0a8', paddingTop: 10 }}>
          <div style={{ display:'flex', justifyContent:'space-between', fontSize: 10.5, marginBottom: 4 }}>
            <span>{msg.tail || (v.category === 'meals' ? 'Lounas' : v.category === 'travel' ? 'Matkustus' : 'Tuotteet')}</span>
            <span style={{ fontFamily: 'IBM Plex Mono, monospace' }}>{net.toFixed(2)}</span>
          </div>
          <div style={{ display:'flex', justifyContent:'space-between', fontSize: 10.5, marginBottom: 4, color:'#555' }}>
            <span>ALV {msg.vat_rate}%</span>
            <span style={{ fontFamily: 'IBM Plex Mono, monospace' }}>{vat.toFixed(2)}</span>
          </div>
          <div style={{ borderTop: '1px solid #bbb', marginTop: 8, paddingTop: 6, display:'flex', justifyContent:'space-between', fontSize: 12, fontWeight: 700 }}>
            <span>Yhteensä EUR</span>
            <span style={{ fontFamily: 'IBM Plex Mono, monospace' }}>{msg.amount.toFixed(2)}</span>
          </div>
        </div>

        <div style={{ marginTop: 18, fontSize: 9, color: '#777' }}>
          Maksettu: Mastercard **** 4417 · Hyväksytty
        </div>

        <div style={{ position: 'absolute', bottom: 22, left: 28, right: 28, fontSize: 8, color: '#999', textAlign: 'center', borderTop: '1px dashed #d8d1c0', paddingTop: 8 }}>
          Kiitos käynnistäsi — {v.name}
        </div>
      </div>
    </div>
  );
}

function ReviewScreen({ t, lang, messages, onTransfer, selectedId, setSelectedId }) {
  const queue = useMemoR(() => messages.filter(m => m.status === 'pending'), [messages]);
  const activeId = (selectedId && queue.some(m => m.id === selectedId)) ? selectedId : queue[0]?.id;
  const setActiveId = (id) => setSelectedId(id);
  const [edits, setEdits] = useStateR({});
  const active = queue.find(m => m.id === activeId) || queue[0];
  const idx = queue.findIndex(m => m.id === active?.id);

  function formFromMsg(m) {
    if (!m) return null;
    return {
      vendor: m.vendor.name,
      date: `${m.received_at.getFullYear()}-${String(m.received_at.getMonth()+1).padStart(2,'0')}-${String(m.received_at.getDate()).padStart(2,'0')}`,
      amount: m.amount.toFixed(2),
      currency: m.currency,
      vatRate: m.vat_rate,
      category: m.category,
      project: m.project,
      payment: m.payment,
      note: m.note || '',
      filename: m.file_name || '',
    };
  }
  const [form, setForm] = useStateR(() => formFromMsg(active));
  const [editedKeys, setEditedKeys] = useStateR(new Set());

  React.useEffect(() => {
    setForm(formFromMsg(active));
    setEditedKeys(new Set());
  }, [active?.id]);

  function update(k, v) {
    setForm(f => ({ ...f, [k]: v }));
    setEditedKeys(s => new Set(s).add(k));
  }

  function prev() {
    if (!active) return;
    const i = Math.max(0, idx - 1);
    setActiveId(queue[i].id);
  }
  function next() {
    if (!active) return;
    const i = Math.min(queue.length - 1, idx + 1);
    setActiveId(queue[i].id);
  }

  if (!active) {
    return (
      <div className="content">
        <div className="rev-head"><div className="intro"><h1>{t.review.title}</h1><p>{t.review.subtitle}</p></div></div>
        <div className="card card-pad" style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
          <I.Check size={28} /><div style={{marginTop:8}}>{t.review.empty}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="content" style={{ paddingBottom: 24 }}>
      <div className="rev-head">
        <div className="intro">
          <h1>{t.review.title}</h1>
          <p>{t.review.subtitle}</p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="rev-progress">{idx + 1} / {queue.length}</span>
          <button className="btn" onClick={prev} disabled={idx === 0}><I.ArrowL size={14}/>{t.review.prev}</button>
          <button className="btn" onClick={next} disabled={idx === queue.length - 1}>{t.review.next}<I.Arrow size={14}/></button>
          <button className="btn primary" style={{ marginLeft: 6 }}>{t.review.approveAll}</button>
        </div>
      </div>

      <div className="review-grid">
        {/* QUEUE */}
        <div className="queue">
          <div className="queue-head">
            <div style={{ fontSize: 13, fontWeight: 500 }}>{t.sections.queue}</div>
            <span className="pill warn" style={{ fontSize: 11 }}>{queue.length}</span>
          </div>
          <div className="queue-list">
            {queue.map(m => (
              <div key={m.id} className={`q-item ${active.id === m.id ? 'active' : ''}`} onClick={() => setActiveId(m.id)}>
                <VendorLogo vendor={m.vendor} size={26} />
                <div style={{ minWidth: 0 }}>
                  <div className="vendor" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.vendor.name}</div>
                  <div className="meta">{fmtDateTime(m.received_at, lang)} · <Confidence value={m.confidence} /></div>
                </div>
                <div className="amt">{fmtAmount(m.amount, m.currency, lang)}</div>
              </div>
            ))}
          </div>
        </div>

        {/* PDF */}
        <div className="pdf-pane">
          <div className="pdf-head">
            <span className="mono" style={{ fontSize: 11.5 }}>{active.file_name}</span>
            <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <span className="pill muted" style={{ fontSize: 10.5 }}><I.Mail size={10}/> Gmail</span>
              <a href="#" className="btn ghost sm"><I.Download size={12}/></a>
            </span>
          </div>
          <PdfMock msg={active} lang={lang} />
        </div>

        {/* FORM */}
        <div className="form-pane">
          <div className="form-head">
            <div>
              <div style={{ fontSize: 13, fontWeight: 600 }}>{active.vendor.name}</div>
              <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 2, display:'flex', gap: 6, alignItems:'center' }}>
                <I.Sparkle size={11}/> {t.review.aiExtracted} · <Confidence value={active.confidence}/>
              </div>
            </div>
            <a className="btn ghost sm" style={{ color: 'var(--text-2)' }} href="#" target="_blank" rel="noopener">
              <I.Mail size={12}/> {lang==='sv'?'Öppna i Gmail':'Open in Gmail'} <I.ExternalLink size={11}/>
            </a>
          </div>
          <div className="form-body">
            {!form ? null : (<>
            <div className="fld-row">
              <div className={`fld ${editedKeys.has('vendor') ? 'edited' : ''}`}>
                <label>{t.review.form.vendor}</label>
                <input value={form.vendor} onChange={e => update('vendor', e.target.value)} />
              </div>
              <div className={`fld ${editedKeys.has('date') ? 'edited' : ''}`}>
                <label>{t.review.form.date}</label>
                <input type="date" value={form.date} onChange={e => update('date', e.target.value)} />
              </div>
            </div>

            <div className="fld-row-3">
              <div className={`fld ${editedKeys.has('amount') ? 'edited' : ''}`}>
                <label>{t.review.form.amount}</label>
                <input className="mono" value={form.amount} onChange={e => update('amount', e.target.value)} />
              </div>
              <div className={`fld ${editedKeys.has('currency') ? 'edited' : ''}`}>
                <label>{t.review.form.currency}</label>
                <select value={form.currency} onChange={e => update('currency', e.target.value)}>
                  <option>EUR</option><option>SEK</option><option>USD</option><option>GBP</option>
                </select>
              </div>
              <div className={`fld ${editedKeys.has('vatRate') ? 'edited' : ''}`}>
                <label>{t.review.form.vatRate}</label>
                <select value={form.vatRate} onChange={e => update('vatRate', parseFloat(e.target.value))}>
                  <option value="0">0 %</option>
                  <option value="10">10 %</option>
                  <option value="14">14 %</option>
                  <option value="25.5">25,5 %</option>
                </select>
              </div>
            </div>

            <div className="fld-row">
              <div className={`fld ${editedKeys.has('category') ? 'edited' : ''}`}>
                <label>{t.review.form.category}</label>
                <select value={form.category} onChange={e => update('category', e.target.value)}>
                  <option value="travel">{t.categories.travel}</option>
                  <option value="lodging">{t.categories.lodging}</option>
                  <option value="meals">{t.categories.meals}</option>
                  <option value="supplies">{t.categories.supplies}</option>
                </select>
              </div>
              <div className={`fld ${editedKeys.has('project') ? 'edited' : ''}`}>
                <label>{t.review.form.project}</label>
                <select value={form.project} onChange={e => update('project', e.target.value)}>
                  <option>Kongressi 2026 Q2</option><option>Toimisto</option><option>Myynti</option>
                </select>
              </div>
            </div>

            <div className={`fld ${editedKeys.has('payment') ? 'edited' : ''}`}>
              <label>{t.review.form.payment}</label>
              <input value={form.payment} onChange={e => update('payment', e.target.value)} />
            </div>

            <div className={`fld ${editedKeys.has('filename') ? 'edited' : ''}`}>
              <label>{t.review.form.filename}</label>
              <input className="mono" style={{ fontSize: 11.5 }} value={form.filename} onChange={e => update('filename', e.target.value)} />
              <div className="hint"><I.Sparkle size={10}/> {lang==='sv'?'AI-genererat namn':'AI-generated'}</div>
            </div>

            <div className="fld">
              <label>{t.review.form.note}</label>
              <textarea rows={2} value={form.note} onChange={e => update('note', e.target.value)} />
            </div>

            {editedKeys.size > 0 && (
              <div style={{ fontSize: 11.5, color: 'var(--warn)', display: 'flex', alignItems: 'center', gap: 5, marginTop: 4 }}>
                <I.Dot size={8}/> {editedKeys.size} {lang==='sv'?'fält manuellt redigerade':'fields edited manually'}
              </div>
            )}
            </>)}
          </div>
          <div className="form-footer">
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn ghost"><I.X size={13}/>{t.review.reject}</button>
              <button className="btn">{t.review.skip}</button>
            </div>
            <button className="btn primary" onClick={() => onTransfer(active)} style={{ padding: '8px 14px' }}>
              <I.Check size={14}/> {t.review.approve}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

window.ReviewScreen = ReviewScreen;
window.PdfMock = PdfMock;
