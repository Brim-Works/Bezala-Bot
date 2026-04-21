const { useState: useStateD, useMemo: useMemoD } = React;

function Sidebar({ view, setView, t, counts, selectedMsg, onPipeline, lang }) {
  const items = [
    { id: 'dashboard', label: t.nav.dashboard, icon: I.Dashboard },
    { id: 'review',    label: t.nav.review,    icon: I.Review, count: counts.pending },
    { id: 'log',       label: t.nav.log,       icon: I.Log },
    { id: 'settings',  label: t.nav.settings,  icon: I.Settings },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">B</div>
        <div>
          <div className="brand-name">Bezala Bot</div>
          <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{t.tagline}</div>
        </div>
      </div>
      {items.map(it => {
        const Icon = it.icon;
        return (
          <div key={it.id} className={`nav-item ${view === it.id ? 'active' : ''}`} onClick={() => setView(it.id)}>
            <Icon size={16} />
            <span>{it.label}</span>
            {it.count ? <span className="count">{it.count}</span> : null}
          </div>
        );
      })}
      <div className="nav-sep" />
      <div style={{ padding: '0 10px', fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }}>
        <div className="flow" style={{ padding: '8px 10px', borderRadius: 8 }} title={selectedMsg ? (lang==='sv'?'Klicka ett steg för att utforska':'Click a step to explore') : (lang==='sv'?'Välj en rad först':'Select a row first')}>
          <span className="node done" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('gmail')}><I.Mail size={13}/></span>
          <span className="arr">→</span>
          <span className="node done" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('ai')}><I.Sparkle size={13}/></span>
          <span className="arr">→</span>
          <span className="node done" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('drive')}><I.Drive size={13}/></span>
          <span className="arr">→</span>
          <span className="node active" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('bezala')}><I.Bezala size={13}/></span>
        </div>
        <div style={{ marginTop: 10, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
          Gmail · AI · Drive · Bezala
        </div>
      </div>
    </aside>
  );
}

function TopBar({ title, lang, setLang, variant, setVariant, onScan, scanning, t, selectedMsg, onPipeline }) {
  const hasSel = !!selectedMsg;
  const tip = hasSel
    ? (lang==='sv'?`Vald rad: ${selectedMsg.vendor.name}`:`Selected: ${selectedMsg.vendor.name}`)
    : (lang==='sv'?'Välj en rad i tabellen för att utforska pipeline':'Select a row to explore the pipeline');
  const pipeCls = (k) => `node ${hasSel ? 'done' : ''} ${hasSel?'clickable':''}`;
  const pipeStyle = { cursor: hasSel ? 'pointer' : 'default', opacity: hasSel ? 1 : 0.55, padding: '2px 4px', borderRadius: 4 };
  return (
    <div className="topbar">
      <div className="title">{title}</div>
      <div className="spacer"/>
      <div className="flow" title={tip}>
        <span className={pipeCls('gmail')} style={pipeStyle} onClick={() => hasSel && onPipeline('gmail')}>Gmail</span>
        <span className="arr">→</span>
        <span className={pipeCls('ai')} style={pipeStyle} onClick={() => hasSel && onPipeline('ai')}>AI</span>
        <span className="arr">→</span>
        <span className={pipeCls('drive')} style={pipeStyle} onClick={() => hasSel && onPipeline('drive')}>Drive</span>
        <span className="arr">→</span>
        <span className="node active" style={{ ...pipeStyle, opacity: hasSel ? 1 : 0.55 }} onClick={() => hasSel && onPipeline('bezala')}>Bezala</span>
      </div>
      <div className="tw-opts" style={{ padding: 2 }} title={lang==='sv'?'Växla tema':'Switch theme'}>
        <div className={`opt ${variant==='A'?'active':''}`} onClick={() => setVariant('A')} style={{padding:'4px 10px', display:'inline-flex', gap:5, alignItems:'center'}}>
          <span style={{width:10,height:10,borderRadius:'50%',background:'#f7f7f4',border:'1px solid #cfcec2',display:'inline-block'}}/> Ljust
        </div>
        <div className={`opt ${variant==='B'?'active':''}`} onClick={() => setVariant('B')} style={{padding:'4px 10px', display:'inline-flex', gap:5, alignItems:'center'}}>
          <span style={{width:10,height:10,borderRadius:'50%',background:'#12221c',border:'1px solid #35574a',display:'inline-block'}}/> Skog
        </div>
      </div>
      <div className="tw-opts" style={{ padding: 2 }}>
        <div className={`opt ${lang==='sv'?'active':''}`} onClick={() => setLang('sv')} style={{padding:'4px 10px'}}>SV</div>
        <div className={`opt ${lang==='en'?'active':''}`} onClick={() => setLang('en')} style={{padding:'4px 10px'}}>EN</div>
      </div>
      <button className="btn" onClick={onScan} disabled={scanning}>
        <I.Refresh size={14} />
        {scanning ? t.scanning : t.scan}
      </button>
    </div>
  );
}

function Dashboard({ t, lang, messages, onOpenReview, runs, selected, setSelected }) {
  const [filter, setFilter] = useStateD('all');
  const [query, setQuery] = useStateD('');

  const pending = messages.filter(m => m.status === 'pending');
  const saved = messages.filter(m => m.status === 'saved' || m.status === 'transferred');
  const errors = messages.filter(m => m.status === 'error');

  const filtered = useMemoD(() => {
    let list = messages;
    if (filter === 'needsReview') list = pending;
    else if (filter === 'auto') list = saved;
    else if (filter === 'errors') list = errors;
    if (query) {
      const q = query.toLowerCase();
      list = list.filter(m =>
        (m.vendor.name + m.subject + m.sender + (m.file_name || '')).toLowerCase().includes(q)
      );
    }
    return list;
  }, [messages, filter, query]);

  const totalAmount = saved.reduce((s, m) => s + (m.amount || 0), 0);

  return (
    <div className="content">
      <div className="hero-strip">
        <div style={{ flex: 1 }}>
          <h1>Bezala Bot <em>automates</em> receipts.</h1>
        </div>
        <div className="sub">{t.tagline}. {pending.length} {t.stats.pending.toLowerCase()}.</div>
      </div>

      <div className="stat-grid">
        <div className="stat accent">
          <div className="l">{t.stats.pending}</div>
          <div className="v">{pending.length}</div>
          <div className="sub">
            <a onClick={onOpenReview} style={{ color: 'var(--accent)', cursor: 'pointer', textDecoration: 'underline', textUnderlineOffset: 3 }}>
              {t.nav.review} →
            </a>
          </div>
        </div>
        <div className="stat">
          <div className="l">{t.stats.saved}</div>
          <div className="v">{saved.length}</div>
          <div className="sub mono">{fmtAmount(totalAmount, 'EUR', lang)} {lang==='sv'?'senaste 30 dagarna':'last 30 days'}</div>
        </div>
        <div className="stat">
          <div className="l">{t.stats.transferred}</div>
          <div className="v">{saved.length - 2}</div>
          <div className="sub">{lang==='sv'?'varav auto':'auto-transferred'} 9</div>
        </div>
        <div className="stat">
          <div className="l">{t.stats.errors}</div>
          <div className="v" style={{ color: errors.length ? 'var(--err)' : 'var(--text)' }}>{errors.length}</div>
          <div className="sub">{lang==='sv'?'sista körningen OK':'last run OK'}</div>
        </div>
      </div>

      <div className="sh"><h2>{t.sections.processed}</h2><span className="side">{filtered.length} {lang==='sv'?'rader':'rows'} · {lang==='sv'?'Senaste scanning':t.stats.lastRun} {fmtRelative(runs[0].finished_at, lang)}</span></div>

      <div className="fbar">
        {[
          ['all', t.filters.all, messages.length],
          ['needsReview', t.filters.needsReview, pending.length],
          ['auto', t.filters.auto, saved.length],
          ['errors', t.filters.errors, errors.length],
        ].map(([k, l, c]) => (
          <div key={k} className={`tab ${filter === k ? 'active' : ''}`} onClick={() => setFilter(k)}>
            {l} <span className="mono" style={{ color: 'var(--muted)', marginLeft: 4 }}>{c}</span>
          </div>
        ))}
        <div className="search">
          <I.Search size={14} />
          <input value={query} onChange={e => setQuery(e.target.value)} placeholder={lang==='sv'?'Sök leverantör, ämne, filnamn…':'Search vendor, subject, filename…'} />
        </div>
      </div>

      <div className="card" style={{ overflow: 'hidden' }}>
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 100 }}>{t.cols.time}</th>
              <th style={{ width: 180 }}>{t.cols.vendor}</th>
              <th>{t.cols.subject}</th>
              <th style={{ width: 240 }}>{t.cols.file}</th>
              <th style={{ width: 110 }}>{t.cols.amount}</th>
              <th style={{ width: 110 }}>{t.cols.confidence}</th>
              <th style={{ width: 160 }}>{t.cols.status}</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(m => (
              <tr key={m.id} className={selected === m.id ? 'selected' : ''} onClick={() => setSelected(m.id)}>
                <td className="num" style={{ color: 'var(--text-2)' }}>{fmtRelative(m.processed_at, lang)}</td>
                <td>
                  <span className="vchip">
                    <VendorLogo vendor={m.vendor} />
                    <span style={{ fontSize: 13 }}>{m.vendor.name}</span>
                  </span>
                </td>
                <td style={{ color: 'var(--text-2)', maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.subject}</td>
                <td className="mono" style={{ fontSize: 11.5, color: 'var(--text-2)' }}>{m.file_name || <span style={{color:'var(--muted)'}}>—</span>}</td>
                <td className="num" style={{ textAlign: 'right' }}>
                  {m.amount ? fmtAmount(m.amount, m.currency, lang) : <span style={{color:'var(--muted)'}}>—</span>}
                </td>
                <td>{m.confidence ? <Confidence value={m.confidence} /> : <span style={{color:'var(--muted)'}}>—</span>}</td>
                <td><StatusCell m={m} t={t} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="sh"><h2>{t.sections.runs}</h2><span className="side">{lang==='sv'?'senaste 14 körningarna':'last 14 runs'}</span></div>
      <div className="card card-pad" style={{ padding: 20 }}>
        <div style={{ display: 'flex', gap: 3, alignItems: 'flex-end', height: 60 }}>
          {runs.slice().reverse().map((r, i) => {
            const h = 10 + Math.min(50, r.messages_processed * 8);
            const color = r.errors ? 'var(--err)' : r.messages_processed === 0 ? 'var(--border-strong)' : 'var(--accent)';
            return (
              <div key={i} style={{ flex: 1, height: h, background: color, borderRadius: 3, minWidth: 10, opacity: r.messages_processed === 0 ? 0.5 : 1 }} title={`${r.messages_processed} processed`} />
            );
          })}
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 10, fontSize: 11.5, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
          <span>−14h</span>
          <span>{lang==='sv'?'nu':'now'}</span>
        </div>
      </div>
    </div>
  );
}

window.Sidebar = Sidebar;
window.TopBar = TopBar;
window.Dashboard = Dashboard;
