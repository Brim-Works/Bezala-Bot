// FAS 5 prototype — 4 new views built on FAS 4 primitives.
// Pill, StatusCell, VendorLogo, Confidence, I (icons) come from window.* (shared globals).

const { useState: useS5, useMemo: useM5 } = React;

// ============================================================
// SHARED — small helpers used across FAS 5 views
// ============================================================
const fmtDate = (d, lang='sv') => new Date(d).toLocaleString(lang==='sv'?'sv-FI':'en-FI', {year:'numeric', month:'short', day:'numeric'});
const fmtMoney = (n, cur='EUR') => new Intl.NumberFormat('sv-FI', {style:'currency', currency:cur, maximumFractionDigits:2}).format(n);

function SectionHead({ title, sub, actions }) {
  return (
    <div style={{display:'flex', alignItems:'flex-end', justifyContent:'space-between', marginBottom:16, paddingBottom:12, borderBottom:'1px solid var(--border)'}}>
      <div>
        <h2 style={{font:'italic 300 32px/1.1 "Instrument Serif", serif', margin:0, color:'var(--text)'}}>{title}</h2>
        {sub && <div style={{color:'var(--text-dim)', fontSize:13, marginTop:6}}>{sub}</div>}
      </div>
      {actions && <div style={{display:'flex', gap:8}}>{actions}</div>}
    </div>
  );
}

function Btn({ children, onClick, variant='ghost', size='md', disabled }) {
  const sz = size==='sm' ? {padding:'4px 10px', fontSize:12} : {padding:'7px 14px', fontSize:13};
  const styles = {
    primary: {background:'var(--accent)', color:'var(--accent-contrast)', border:'1px solid var(--accent)'},
    ghost: {background:'transparent', color:'var(--text)', border:'1px solid var(--border)'},
    danger: {background:'transparent', color:'var(--err)', border:'1px solid var(--border)'},
  }[variant];
  return <button onClick={onClick} disabled={disabled} style={{...sz, ...styles, borderRadius:6, cursor:disabled?'not-allowed':'pointer', opacity:disabled?0.5:1, fontFamily:'inherit', display:'inline-flex', alignItems:'center', gap:6}}>{children}</button>;
}

function Checkbox({ checked, onChange }) {
  return (
    <div onClick={e=>{e.stopPropagation(); onChange(!checked);}} style={{width:16, height:16, borderRadius:3, border:`1.5px solid ${checked?'var(--accent)':'var(--border-strong)'}`, background:checked?'var(--accent)':'transparent', display:'inline-flex', alignItems:'center', justifyContent:'center', cursor:'pointer', flexShrink:0}}>
      {checked && <svg width="10" height="10" viewBox="0 0 20 20" fill="none" stroke="var(--accent-contrast)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M4 10.5 l3.5 3.5 L16 5.5"/></svg>}
    </div>
  );
}

// ============================================================
// 5.1 — TRASH (soft-delete + papperskorg)
// ============================================================
function TrashScreen({ t, lang, trashed, onRestore, onPurge, onEmptyAll }) {
  const [sel, setSel] = useS5(new Set());
  const toggle = (id) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); setSel(n); };
  const toggleAll = () => setSel(sel.size === trashed.length ? new Set() : new Set(trashed.map(r=>r.id)));

  return (
    <div className="content" style={{maxWidth:1100}}>
      <SectionHead
        title={lang==='sv' ? 'Papperskorg' : 'Trash'}
        sub={lang==='sv'
          ? `${trashed.length} rader. Automatiskt rensas efter 60 dagar.`
          : `${trashed.length} rows. Auto-purged after 60 days.`}
        actions={<>
          <Btn onClick={onEmptyAll} variant="danger" disabled={trashed.length===0}>
            {lang==='sv' ? 'Töm papperskorgen' : 'Empty trash'}
          </Btn>
        </>}
      />

      {sel.size > 0 && (
        <div style={{display:'flex', alignItems:'center', gap:12, padding:'10px 14px', background:'var(--bg-elev)', border:'1px solid var(--accent)', borderRadius:8, marginBottom:14}}>
          <span style={{fontSize:13, color:'var(--text)'}}>{sel.size} {lang==='sv'?'markerade':'selected'}</span>
          <div style={{flex:1}} />
          <Btn size="sm" onClick={()=>{ sel.forEach(id=>onRestore(id)); setSel(new Set()); }}>
            <window.I.ArrowL size={14}/> {lang==='sv'?'Återställ':'Restore'}
          </Btn>
          <Btn size="sm" variant="danger" onClick={()=>{ sel.forEach(id=>onPurge(id)); setSel(new Set()); }}>
            <window.I.X size={14}/> {lang==='sv'?'Radera permanent':'Delete permanently'}
          </Btn>
        </div>
      )}

      {trashed.length === 0 ? (
        <EmptyState icon="trash" title={lang==='sv'?'Papperskorgen är tom':'Trash is empty'} sub={lang==='sv'?'Borttagna rader visas här i 60 dagar innan de rensas automatiskt.':'Deleted rows appear here for 60 days before auto-purge.'} />
      ) : (
        <div style={{background:'var(--surface)', border:'1px solid var(--border)', borderRadius:10, overflow:'hidden'}}>
          <table className="tbl" style={{width:'100%', borderCollapse:'collapse'}}>
            <thead>
              <tr style={{background:'var(--bg-elev)', borderBottom:'1px solid var(--border)'}}>
                <th style={th(40)}><Checkbox checked={sel.size===trashed.length && trashed.length>0} onChange={toggleAll}/></th>
                <th style={th()}>{lang==='sv'?'Borttagen':'Deleted'}</th>
                <th style={th()}>{lang==='sv'?'Leverantör':'Vendor'}</th>
                <th style={th()}>{lang==='sv'?'Ämne':'Subject'}</th>
                <th style={{...th(), textAlign:'right'}}>{lang==='sv'?'Belopp':'Amount'}</th>
                <th style={th()}>{lang==='sv'?'Anledning':'Reason'}</th>
                <th style={th(180)}></th>
              </tr>
            </thead>
            <tbody>
              {trashed.map(r=>(
                <tr key={r.id} style={{borderBottom:'1px solid var(--border)'}}>
                  <td style={td()}><Checkbox checked={sel.has(r.id)} onChange={()=>toggle(r.id)}/></td>
                  <td style={{...td(), fontFamily:'"IBM Plex Mono", monospace', fontSize:12, color:'var(--text-dim)'}}>{fmtDate(r.deleted_at, lang)}</td>
                  <td style={td()}>
                    <div style={{display:'flex', alignItems:'center', gap:8}}>
                      <window.VendorLogo vendor={r.vendor}/>
                      <span>{r.vendor.name}</span>
                    </div>
                  </td>
                  <td style={{...td(), color:'var(--text-dim)', maxWidth:260, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{r.subject}</td>
                  <td style={{...td(), textAlign:'right', fontFamily:'"IBM Plex Mono", monospace'}}>{fmtMoney(r.amount, r.currency)}</td>
                  <td style={{...td(), fontSize:12, color:'var(--text-dim)'}}>
                    <window.Pill kind="neutral">{reasonLabel(r.reason, lang)}</window.Pill>
                  </td>
                  <td style={{...td(), textAlign:'right'}}>
                    <Btn size="sm" onClick={()=>onRestore(r.id)}>{lang==='sv'?'Återställ':'Restore'}</Btn>
                    {' '}
                    <Btn size="sm" variant="danger" onClick={()=>onPurge(r.id)}><window.I.X size={12}/></Btn>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const reasonLabel = (r, lang) => {
  const map = { calendar:{sv:'Kalenderinbjudan', en:'Calendar invite'}, spam:{sv:'Spam', en:'Spam'}, misclassified:{sv:'Felklassad', en:'Misclassified'}, manual:{sv:'Manuell', en:'Manual'} };
  return (map[r]||map.manual)[lang];
};

// ============================================================
// 5.2 — RULES (regelbaserad scanning)
// ============================================================
function RulesScreen({ t, lang, rules, onToggle, onEdit, onDuplicate, onDelete, onReorder, onNew, onTest }) {
  const [editing, setEditing] = useS5(null); // rule being edited in side-panel

  return (
    <div className="content" style={{maxWidth:1200, display:'grid', gridTemplateColumns: editing ? '1fr 420px' : '1fr', gap:20}}>
      <div>
        <SectionHead
          title={lang==='sv'?'Scan-regler':'Scan rules'}
          sub={lang==='sv'?'Regler körs i prioritetsordning. Första matchande regel vinner.':'Rules run in priority order. First match wins.'}
          actions={<>
            <Btn onClick={onNew} variant="primary"><window.I.Plus size={14}/> {lang==='sv'?'Ny regel':'New rule'}</Btn>
          </>}
        />

        <div style={{display:'flex', flexDirection:'column', gap:10}}>
          {rules.map((r, i)=>(
            <RuleCard key={r.id} rule={r} idx={i} lang={lang}
              onToggle={()=>onToggle(r.id)}
              onEdit={()=>setEditing(r)}
              onDuplicate={()=>onDuplicate(r.id)}
              onDelete={()=>onDelete(r.id)}
              onTest={()=>onTest(r.id)}
              onUp={i>0 ? ()=>onReorder(i, i-1) : null}
              onDown={i<rules.length-1 ? ()=>onReorder(i, i+1) : null}
            />
          ))}
        </div>

        <div style={{marginTop:28, padding:14, background:'var(--bg-elev)', border:'1px dashed var(--border)', borderRadius:10}}>
          <div style={{fontSize:13, fontWeight:500, marginBottom:6}}>{lang==='sv'?'Global fallback':'Global fallback'}</div>
          <div style={{color:'var(--text-dim)', fontSize:12.5, lineHeight:1.5}}>
            {lang==='sv'
              ? 'Mail som inte matchar någon regel fångas av globala inställningar (default-kategori, auto-tröskel 90%, ingen auto-upload).'
              : 'Mail matching no rule falls back to global settings (default category, auto-threshold 90%, no auto-upload).'}
          </div>
        </div>
      </div>

      {editing && (
        <RuleEditor rule={editing} lang={lang}
          onClose={()=>setEditing(null)}
          onSave={(r)=>{ onEdit(r); setEditing(null); }}
          onTest={()=>onTest(editing.id)}
        />
      )}
    </div>
  );
}

function RuleCard({ rule, idx, lang, onToggle, onEdit, onDuplicate, onDelete, onTest, onUp, onDown }) {
  const matchCount = rule.stats?.matches_30d ?? 0;
  const stale = matchCount === 0 && !rule.is_template;
  return (
    <div style={{background:'var(--surface)', border:`1px solid ${rule.active?'var(--border)':'var(--border)'}`, borderRadius:10, padding:14, opacity:rule.active?1:0.6}}>
      <div style={{display:'flex', alignItems:'center', gap:12}}>
        <div style={{display:'flex', flexDirection:'column', gap:2}}>
          <button onClick={onUp} disabled={!onUp} style={iconBtn}><svg width="12" height="8" viewBox="0 0 12 8" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="M2 6 L6 2 L10 6"/></svg></button>
          <div style={{fontFamily:'"IBM Plex Mono", monospace', fontSize:11, color:'var(--text-muted)', textAlign:'center'}}>#{idx+1}</div>
          <button onClick={onDown} disabled={!onDown} style={iconBtn}><svg width="12" height="8" viewBox="0 0 12 8" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="M2 2 L6 6 L10 2"/></svg></button>
        </div>

        <div style={{flex:1, minWidth:0}}>
          <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:6}}>
            <div style={{fontSize:14, fontWeight:500}}>{rule.name}</div>
            {rule.active
              ? <window.Pill kind="ok">{lang==='sv'?'Aktiv':'Active'}</window.Pill>
              : <window.Pill kind="neutral">{lang==='sv'?'Pausad':'Paused'}</window.Pill>}
            {stale && <window.Pill kind="warn">{lang==='sv'?'0 matchningar på 30 dagar':'0 matches in 30 days'}</window.Pill>}
          </div>
          <div style={{fontSize:12.5, color:'var(--text-dim)', lineHeight:1.5}}>
            {summarizeRule(rule, lang)}
          </div>
          <div style={{display:'flex', gap:14, marginTop:8, fontSize:11.5, color:'var(--text-muted)', fontFamily:'"IBM Plex Mono", monospace'}}>
            <span>{matchCount} {lang==='sv'?'matchningar senaste 30d':'matches last 30d'}</span>
            <span>→ {rule.action.category}</span>
            {rule.action.bezala_account && <span>· konto {rule.action.bezala_account}</span>}
            {rule.action.auto_upload && <window.Pill kind="accent">Auto</window.Pill>}
          </div>
        </div>

        <div style={{display:'flex', gap:6}}>
          <Btn size="sm" onClick={onTest}><window.I.Sparkle size={13}/>{lang==='sv'?'Testa':'Test'}</Btn>
          <Btn size="sm" onClick={onEdit}>{lang==='sv'?'Redigera':'Edit'}</Btn>
          <Btn size="sm" onClick={onToggle}>{rule.active ? (lang==='sv'?'Pausa':'Pause') : (lang==='sv'?'Aktivera':'Activate')}</Btn>
          <Btn size="sm" onClick={onDuplicate}>{lang==='sv'?'Duplicera':'Duplicate'}</Btn>
          <Btn size="sm" variant="danger" onClick={onDelete}><window.I.X size={12}/></Btn>
        </div>
      </div>
    </div>
  );
}

const summarizeRule = (r, lang) => {
  const parts = [];
  if (r.match.from) parts.push(`${lang==='sv'?'Från':'From'} ${r.match.from}`);
  if (r.match.subject_any?.length) parts.push(`${lang==='sv'?'Ämne innehåller':'Subject contains'} "${r.match.subject_any.join('" eller "')}"`);
  if (r.match.has_attachment) parts.push(lang==='sv'?'har PDF-bilaga':'has PDF attachment');
  if (r.match.min_amount) parts.push(`≥ ${r.match.min_amount} ${r.match.currency||'EUR'}`);
  return parts.join(' · ') || (lang==='sv'?'(inga villkor)':'(no conditions)');
};

function RuleEditor({ rule, lang, onClose, onSave, onTest }) {
  const [r, setR] = useS5(rule);
  const [advanced, setAdvanced] = useS5(false);
  const set = (path, v) => {
    const n = JSON.parse(JSON.stringify(r));
    const keys = path.split('.');
    let o = n; for (let i=0;i<keys.length-1;i++) o = o[keys[i]];
    o[keys[keys.length-1]] = v;
    setR(n);
  };
  return (
    <div style={{position:'sticky', top:0, alignSelf:'start', background:'var(--surface)', border:'1px solid var(--border)', borderRadius:10, padding:18, maxHeight:'calc(100vh - 120px)', overflow:'auto'}}>
      <div style={{display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:14}}>
        <div style={{font:'italic 300 22px/1.2 "Instrument Serif", serif'}}>{lang==='sv'?'Regel':'Rule'}</div>
        <button onClick={onClose} style={iconBtn}><window.I.X size={14}/></button>
      </div>

      <Field label={lang==='sv'?'Namn':'Name'}>
        <input value={r.name} onChange={e=>set('name', e.target.value)} style={inp}/>
      </Field>

      <Divider label={lang==='sv'?'Matchning':'Match'}/>

      <Field label={lang==='sv'?'Avsändare innehåller':'From contains'}>
        <input value={r.match.from||''} onChange={e=>set('match.from', e.target.value)} placeholder="eticket@amadeus.com" style={inp}/>
      </Field>

      <Field label={lang==='sv'?'Ämne innehåller någon av (kommaseparerat)':'Subject contains any of (comma-separated)'}>
        <input value={(r.match.subject_any||[]).join(', ')} onChange={e=>set('match.subject_any', e.target.value.split(',').map(s=>s.trim()).filter(Boolean))} placeholder="Finnair, FLIGHT, ETICKET" style={inp}/>
      </Field>

      <Field label={lang==='sv'?'Har bilaga':'Has attachment'}>
        <select value={r.match.has_attachment ? 'yes':'any'} onChange={e=>set('match.has_attachment', e.target.value==='yes')} style={inp}>
          <option value="any">{lang==='sv'?'Valfritt':'Any'}</option>
          <option value="yes">PDF</option>
        </select>
      </Field>

      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:10}}>
        <Field label={lang==='sv'?'Min-belopp':'Min amount'}>
          <input type="number" value={r.match.min_amount||''} onChange={e=>set('match.min_amount', +e.target.value||0)} style={inp}/>
        </Field>
        <Field label={lang==='sv'?'Valuta':'Currency'}>
          <select value={r.match.currency||'EUR'} onChange={e=>set('match.currency', e.target.value)} style={inp}>
            <option>EUR</option><option>SEK</option><option>USD</option>
          </select>
        </Field>
      </div>

      <div style={{marginTop:8}}>
        <label style={{display:'flex', alignItems:'center', gap:8, fontSize:12, color:'var(--text-dim)', cursor:'pointer'}}>
          <Checkbox checked={advanced} onChange={setAdvanced}/>
          {lang==='sv'?'Avancerat (regex, språk, datum-intervall)':'Advanced (regex, language, date range)'}
        </label>
      </div>

      <Divider label={lang==='sv'?'Åtgärd':'Action'}/>

      <Field label={lang==='sv'?'Kategori':'Category'}>
        <select value={r.action.category} onChange={e=>set('action.category', e.target.value)} style={inp}>
          <option>Resa</option><option>Boende</option><option>Mat</option><option>Kontor</option><option>Övrigt</option>
        </select>
      </Field>
      <Field label={lang==='sv'?'Bezala-konto':'Bezala account'}>
        <input value={r.action.bezala_account||''} onChange={e=>set('action.bezala_account', e.target.value)} placeholder="5811" style={inp}/>
      </Field>
      <label style={{display:'flex', alignItems:'center', gap:8, fontSize:13, margin:'10px 0', cursor:'pointer'}}>
        <Checkbox checked={!!r.action.auto_upload} onChange={v=>set('action.auto_upload', v)}/>
        {lang==='sv'?'Auto-upload till Bezala':'Auto-upload to Bezala'}
      </label>
      <label style={{display:'flex', alignItems:'center', gap:8, fontSize:13, margin:'10px 0', cursor:'pointer'}}>
        <Checkbox checked={!!r.action.notify_first} onChange={v=>set('action.notify_first', v)}/>
        {lang==='sv'?'Notifiera mig innan upload':'Notify me before upload'}
      </label>

      <div style={{display:'flex', gap:8, justifyContent:'space-between', marginTop:18}}>
        <Btn onClick={onTest}><window.I.Sparkle size={14}/>{lang==='sv'?'Testa mot befintliga mail':'Test against existing mail'}</Btn>
        <div style={{display:'flex', gap:8}}>
          <Btn onClick={onClose}>{lang==='sv'?'Avbryt':'Cancel'}</Btn>
          <Btn variant="primary" onClick={()=>onSave(r)}>{lang==='sv'?'Spara':'Save'}</Btn>
        </div>
      </div>
    </div>
  );
}

// ============================================================
// 5.3 — AI LEARNING (feedback + vendor patterns)
// ============================================================
function FeedbackInline({ fieldName, value, aiValue, lang, onFeedback }) {
  const [comment, setComment] = useS5(null);
  const diff = value !== aiValue;
  return (
    <div style={{display:'inline-flex', alignItems:'center', gap:4, marginLeft:6}}>
      <button title={lang==='sv'?'AI hade rätt':'AI was right'} onClick={()=>onFeedback(fieldName, 'positive')} style={{...iconBtn, color:'var(--text-muted)'}}>
        <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75"><path d="M6 10 v6 h-2 v-6 z M6 10 l3-5 c0.5-1 2-1 2 0 v3 h4 c1 0 1.5 1 1.2 2 l-1.5 5 c-0.2 0.7-0.8 1-1.5 1 H6"/></svg>
      </button>
      <button title={lang==='sv'?'AI hade fel':'AI was wrong'} onClick={()=>setComment('')} style={{...iconBtn, color:'var(--text-muted)'}}>
        <svg width="12" height="12" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.75" style={{transform:'rotate(180deg)'}}><path d="M6 10 v6 h-2 v-6 z M6 10 l3-5 c0.5-1 2-1 2 0 v3 h4 c1 0 1.5 1 1.2 2 l-1.5 5 c-0.2 0.7-0.8 1-1.5 1 H6"/></svg>
      </button>
      {comment!==null && (
        <input autoFocus placeholder={lang==='sv'?'Vad var fel? (valfritt)':'What was wrong? (optional)'} value={comment}
          onChange={e=>setComment(e.target.value)}
          onBlur={()=>{ onFeedback(fieldName, 'negative', comment); setComment(null); }}
          onKeyDown={e=>{ if (e.key==='Enter'){ onFeedback(fieldName, 'negative', comment); setComment(null); } }}
          style={{...inp, width:220, padding:'4px 8px', fontSize:12, marginLeft:6}}/>
      )}
    </div>
  );
}

function PatternsScreen({ t, lang, patterns, feedback, onForget }) {
  const stats = useM5(()=>{
    const recent = feedback.filter(f=>Date.now()-new Date(f.ts).getTime() < 7*86400*1000);
    const pos = recent.filter(f=>f.type==='positive').length;
    const neg = recent.filter(f=>f.type==='negative').length;
    const pct = pos+neg===0 ? 0 : Math.round(pos*100/(pos+neg));
    return { pos, neg, pct };
  }, [feedback]);

  return (
    <div className="content" style={{maxWidth:1100}}>
      <SectionHead
        title={lang==='sv'?'AI-inlärning':'AI learning'}
        sub={lang==='sv'?'Vad systemet har lärt sig från dina korrigeringar.':'What the system has learned from your corrections.'}
      />

      {/* Stats strip */}
      <div style={{display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:12, marginBottom:22}}>
        <StatTile label={lang==='sv'?'Positiv feedback (7d)':'Positive feedback (7d)'} value={stats.pos} mono/>
        <StatTile label={lang==='sv'?'Negativ feedback (7d)':'Negative feedback (7d)'} value={stats.neg} mono/>
        <StatTile label={lang==='sv'?'Hit-rate':'Hit rate'} value={`${stats.pct}%`} mono accent/>
      </div>

      <div style={{font:'italic 300 22px/1.2 "Instrument Serif", serif', marginBottom:10}}>{lang==='sv'?'Lärda leverantörs-mönster':'Learned vendor patterns'}</div>
      {patterns.length === 0 ? (
        <EmptyState icon="sparkle" title={lang==='sv'?'Inga lärda mönster än':'No learned patterns yet'} sub={lang==='sv'?'När du ändrar AI:ns gissning i granska-vyn föreslår systemet att spara ändringen som standard för den leverantören.':'When you edit the AI guess in Review, the system suggests saving the change as default for that vendor.'}/>
      ) : (
        <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(320px, 1fr))', gap:12}}>
          {patterns.map(p=>(
            <div key={p.id} style={{background:'var(--surface)', border:'1px solid var(--border)', borderRadius:10, padding:14}}>
              <div style={{display:'flex', alignItems:'center', gap:10, marginBottom:10}}>
                <window.VendorLogo vendor={{name:p.vendor, logo:p.vendor.slice(0,2).toUpperCase(), hue:200}}/>
                <div style={{flex:1, fontWeight:500}}>{p.vendor}</div>
                <button onClick={()=>onForget(p.id)} style={{...iconBtn, fontSize:11, color:'var(--text-muted)'}} title={lang==='sv'?'Glöm detta mönster':'Forget this pattern'}>
                  <window.I.X size={12}/>
                </button>
              </div>
              <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'6px 12px', fontSize:12.5}}>
                {Object.entries(p.fields).map(([k,v])=>(
                  <React.Fragment key={k}>
                    <span style={{color:'var(--text-muted)'}}>{k}</span>
                    <span style={{fontFamily:'"IBM Plex Mono", monospace'}}>{v}</span>
                  </React.Fragment>
                ))}
              </div>
              <div style={{marginTop:10, fontSize:11, color:'var(--text-muted)'}}>
                {lang==='sv'?`Lärt från ${p.learned_from} ändringar`:`Learned from ${p.learned_from} edits`}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Inline banner shown in Review after a user edits a field
function LearnPromptBanner({ vendor, field, oldVal, newVal, lang, onConfirm, onDismiss }) {
  return (
    <div style={{display:'flex', alignItems:'center', gap:12, padding:'10px 14px', background:'var(--bg-elev)', border:'1px solid var(--info)', borderRadius:8, marginTop:10}}>
      <window.I.Sparkle size={16}/>
      <div style={{flex:1, fontSize:12.5, lineHeight:1.5}}>
        {lang==='sv'
          ? <>Ska <b>{field}=<span className="mono">{newVal}</span></b> användas som standard för <b>{vendor}</b> framöver?</>
          : <>Use <b>{field}=<span className="mono">{newVal}</span></b> as default for <b>{vendor}</b> going forward?</>}
      </div>
      <Btn size="sm" onClick={onDismiss}>{lang==='sv'?'Nej':'No'}</Btn>
      <Btn size="sm" variant="primary" onClick={onConfirm}>{lang==='sv'?'Ja, lär':'Yes, learn'}</Btn>
    </div>
  );
}

// ============================================================
// 5.4 — CARD MATCHING
// ============================================================
function CardMatchScreen({ t, lang, cardRows, onConfirm, onIgnore }) {
  const [sel, setSel] = useS5(cardRows[0]?.id);
  const row = cardRows.find(r=>r.id===sel);

  const groups = useM5(()=>({
    suggested: cardRows.filter(r=>r.status==='suggested'),
    auto: cardRows.filter(r=>r.status==='auto'),
    orphan: cardRows.filter(r=>r.status==='orphan'),
  }), [cardRows]);

  return (
    <div className="content" style={{maxWidth:1300}}>
      <SectionHead
        title={lang==='sv'?'Kortmatchning':'Card matching'}
        sub={lang==='sv'?'Kortrader i Bezala utan bifogat kvitto, matchade mot Bezala Bot-databasen.':'Card transactions in Bezala without receipts, matched against the Bezala Bot database.'}
      />

      <div style={{display:'grid', gridTemplateColumns:'380px 1fr', gap:20}}>
        {/* LEFT list */}
        <div style={{display:'flex', flexDirection:'column', gap:16, maxHeight:'calc(100vh - 160px)', overflow:'auto', paddingRight:6}}>
          <CardGroup title={lang==='sv'?'Föreslagna matchningar':'Suggested matches'} kind="warn" rows={groups.suggested} sel={sel} setSel={setSel} lang={lang}/>
          <CardGroup title={lang==='sv'?'Auto-matchade idag':'Auto-matched today'} kind="ok" rows={groups.auto} sel={sel} setSel={setSel} lang={lang}/>
          <CardGroup title={lang==='sv'?'Orphaned (ingen match)':'Orphaned (no match)'} kind="err" rows={groups.orphan} sel={sel} setSel={setSel} lang={lang}/>
        </div>

        {/* RIGHT detail */}
        <div>
          {row ? <CardDetail row={row} lang={lang} onConfirm={onConfirm} onIgnore={onIgnore}/> : <EmptyState icon="search" title={lang==='sv'?'Välj en kortrad':'Select a card row'}/>}
        </div>
      </div>
    </div>
  );
}

function CardGroup({ title, kind, rows, sel, setSel, lang }) {
  if (rows.length === 0) return null;
  return (
    <div>
      <div style={{display:'flex', alignItems:'center', gap:8, marginBottom:8, fontSize:12, color:'var(--text-dim)'}}>
        <window.Pill kind={kind}>{rows.length}</window.Pill>
        <span>{title}</span>
      </div>
      <div style={{display:'flex', flexDirection:'column', gap:6}}>
        {rows.map(r=>(
          <div key={r.id} onClick={()=>setSel(r.id)}
            style={{padding:'10px 12px', background: sel===r.id?'var(--bg-elev)':'var(--surface)', border:`1px solid ${sel===r.id?'var(--accent)':'var(--border)'}`, borderRadius:8, cursor:'pointer', borderLeft:`3px solid ${sel===r.id?'var(--accent)':'transparent'}`}}>
            <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline', gap:8}}>
              <div style={{fontSize:13, fontWeight:500, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>{r.card_vendor}</div>
              <div style={{fontFamily:'"IBM Plex Mono", monospace', fontSize:12.5}}>{fmtMoney(r.amount, r.currency)}</div>
            </div>
            <div style={{display:'flex', justifyContent:'space-between', marginTop:4, fontSize:11, color:'var(--text-muted)', fontFamily:'"IBM Plex Mono", monospace'}}>
              <span>{fmtDate(r.card_date, lang)}</span>
              {r.best_match && <span>· {Math.round(r.best_match.confidence*100)}%</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CardDetail({ row, lang, onConfirm, onIgnore }) {
  return (
    <div style={{background:'var(--surface)', border:'1px solid var(--border)', borderRadius:12, padding:22}}>
      {/* Card row header */}
      <div style={{display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:16, padding:16, background:'var(--bg-elev)', border:'1px solid var(--border)', borderRadius:10, marginBottom:18}}>
        <KV k={lang==='sv'?'Kortrad från Bezala':'Card row from Bezala'} v={row.card_vendor}/>
        <KV k={lang==='sv'?'Debiterad':'Charged'} v={fmtDate(row.card_date, lang)} mono/>
        <KV k={lang==='sv'?'Belopp':'Amount'} v={fmtMoney(row.amount, row.currency)} mono/>
      </div>

      <div style={{font:'italic 300 22px/1.2 "Instrument Serif", serif', marginBottom:10}}>
        {row.status==='orphan'
          ? (lang==='sv'?'Inget kvitto matchade':'No receipt matched')
          : row.status==='auto'
            ? (lang==='sv'?'Auto-matchat kvitto':'Auto-matched receipt')
            : (lang==='sv'?'Föreslagna kvitton':'Suggested receipts')}
      </div>

      {row.candidates.length === 0 && (
        <div style={{padding:16, background:'var(--bg-elev)', border:'1px dashed var(--border)', borderRadius:10, color:'var(--text-dim)', fontSize:13}}>
          {lang==='sv'?'Ingen matchning hittades i Bezala Bots databas inom ±3 dagar. Hantera manuellt i Bezala.':'No match found in the Bezala Bot database within ±3 days. Handle manually in Bezala.'}
        </div>
      )}

      {row.candidates.map((c, i)=>(
        <div key={c.id} style={{background:'var(--bg-elev)', border:'1px solid var(--border)', borderRadius:10, padding:14, marginBottom:10}}>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:10}}>
            <div style={{display:'flex', alignItems:'center', gap:10}}>
              <window.VendorLogo vendor={c.vendor}/>
              <div>
                <div style={{fontWeight:500}}>{c.vendor.name}</div>
                <div style={{fontSize:11, color:'var(--text-muted)', fontFamily:'"IBM Plex Mono", monospace'}}>{c.file_name}</div>
              </div>
            </div>
            <div style={{textAlign:'right'}}>
              <div style={{fontFamily:'"IBM Plex Mono", monospace', fontSize:14}}>{fmtMoney(c.amount, c.currency)}</div>
              <div style={{fontSize:11, color:'var(--text-muted)', fontFamily:'"IBM Plex Mono", monospace'}}>{fmtDate(c.receipt_date, lang)}</div>
            </div>
          </div>

          <div style={{display:'grid', gridTemplateColumns:'auto 1fr', gap:'4px 10px', fontSize:11.5, marginBottom:10}}>
            <span style={{color:'var(--text-muted)'}}>{lang==='sv'?'Match':'Match'}</span>
            <window.Confidence value={c.confidence}/>
            <span style={{color:'var(--text-muted)'}}>{lang==='sv'?'Skäl':'Why'}</span>
            <span style={{color:'var(--text-dim)'}}>{c.reasons.join(' · ')}</span>
          </div>

          {row.status==='suggested' && (
            <div style={{display:'flex', gap:8, justifyContent:'flex-end'}}>
              <Btn size="sm" onClick={()=>onIgnore(row.id, c.id)}>{lang==='sv'?'Inte denna':'Not this'}</Btn>
              <Btn size="sm" variant="primary" onClick={()=>onConfirm(row.id, c.id)}><window.I.Check size={13}/>{lang==='sv'?'Bekräfta och bifoga':'Confirm & attach'}</Btn>
            </div>
          )}
          {row.status==='auto' && c.confidence >= 0.97 && i===0 && (
            <window.Pill kind="ok"><window.I.Check size={11}/> {lang==='sv'?'Auto-bifogat':'Auto-attached'}</window.Pill>
          )}
        </div>
      ))}

      {row.status==='orphan' && (
        <div style={{display:'flex', gap:8, justifyContent:'flex-end', marginTop:10}}>
          <Btn onClick={()=>onIgnore(row.id, null)}>{lang==='sv'?'Markera för manuell hantering':'Mark for manual handling'}</Btn>
        </div>
      )}
    </div>
  );
}

// ============================================================
// SHARED PRIMITIVES
// ============================================================
function StatTile({ label, value, mono, accent }) {
  return (
    <div style={{background:'var(--surface)', border:`1px solid ${accent?'var(--accent)':'var(--border)'}`, borderRadius:10, padding:'14px 16px', ...(accent?{borderLeft:'3px solid var(--accent)'}:{})}}>
      <div style={{fontSize:11, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.04em'}}>{label}</div>
      <div style={{fontSize:26, marginTop:4, fontFamily: mono?'"IBM Plex Mono", monospace':'inherit', fontWeight:500, color: accent?'var(--accent)':'var(--text)'}}>{value}</div>
    </div>
  );
}

function EmptyState({ icon, title, sub }) {
  const Ico = { trash: window.I.X, sparkle: window.I.Sparkle, search: window.I.Search }[icon] || window.I.Dot;
  return (
    <div style={{padding:48, textAlign:'center', background:'var(--surface)', border:'1px dashed var(--border)', borderRadius:12}}>
      <div style={{display:'inline-flex', width:48, height:48, alignItems:'center', justifyContent:'center', borderRadius:'50%', background:'var(--bg-elev)', color:'var(--text-muted)', marginBottom:12}}><Ico size={20}/></div>
      <div style={{font:'italic 300 22px/1.2 "Instrument Serif", serif', marginBottom:6}}>{title}</div>
      {sub && <div style={{color:'var(--text-dim)', fontSize:13, maxWidth:400, margin:'0 auto'}}>{sub}</div>}
    </div>
  );
}

function Field({ label, children }) {
  return (
    <div style={{marginBottom:12}}>
      <div style={{fontSize:11, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.04em', marginBottom:4}}>{label}</div>
      {children}
    </div>
  );
}

function Divider({ label }) {
  return <div style={{display:'flex', alignItems:'center', gap:8, margin:'16px 0 10px', fontSize:10, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.08em'}}>
    <div style={{flex:1, height:1, background:'var(--border)'}}/>{label}<div style={{flex:1, height:1, background:'var(--border)'}}/>
  </div>;
}

function KV({ k, v, mono }) {
  return (
    <div>
      <div style={{fontSize:10.5, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.04em'}}>{k}</div>
      <div style={{fontSize:14, marginTop:2, fontFamily: mono?'"IBM Plex Mono", monospace':'inherit'}}>{v}</div>
    </div>
  );
}

const th = (w) => ({padding:'10px 14px', textAlign:'left', fontSize:11, color:'var(--text-muted)', textTransform:'uppercase', letterSpacing:'0.04em', fontWeight:500, ...(w?{width:w}:{})});
const td = (w) => ({padding:'11px 14px', fontSize:13, color:'var(--text)', verticalAlign:'middle', ...(w?{width:w}:{})});
const inp = {width:'100%', padding:'6px 10px', fontSize:13, background:'var(--bg-elev)', border:'1px solid var(--border)', borderRadius:6, color:'var(--text)', fontFamily:'inherit'};
const iconBtn = {background:'transparent', border:'none', color:'var(--text-dim)', cursor:'pointer', padding:4, display:'inline-flex', alignItems:'center', justifyContent:'center'};

// Export all to window
Object.assign(window, {
  TrashScreen, RulesScreen, PatternsScreen, CardMatchScreen,
  FeedbackInline, LearnPromptBanner,
});
