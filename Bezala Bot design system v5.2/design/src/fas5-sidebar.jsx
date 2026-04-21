// FAS 5 sidebar — extends FAS 4 nav with 4 new items.
// Mirrors structure of FAS 4 Sidebar (see dashboard.jsx) and reuses .nav-item / .count styles.

function Fas5Sidebar({ view, setView, t, counts, selectedMsg, onPipeline, lang }) {
  const I = window.I;
  const items = [
    { id:'dashboard', label:t.nav.dashboard, icon:I.Dashboard },
    { id:'review',    label:t.nav.review,    icon:I.Review, count: counts.pending },
    { id:'cards',     label:t.nav.cards,     icon:I.Bezala, count: counts.cards_suggested },
    { id:'log',       label:t.nav.log,       icon:I.Log },
    { id:'sep' },
    { id:'trash',     label:t.nav.trash,     icon:I.X, count: counts.trash },
    { id:'rules',     label:t.nav.rules,     icon:I.Filter },
    { id:'patterns',  label:t.nav.patterns,  icon:I.Sparkle },
    { id:'settings',  label:t.nav.settings,  icon:I.Settings },
  ];
  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">B</div>
        <div>
          <div className="brand-name">Bezala Bot</div>
          <div style={{fontSize:11, color:'var(--muted)', marginTop:2}}>{t.tagline}</div>
        </div>
      </div>
      {items.map(it => {
        if (it.id === 'sep') return <div key="sep" className="nav-sep" style={{margin:'8px 10px', height:1, background:'var(--border)', opacity:0.6}}/>;
        const Icon = it.icon;
        return (
          <div key={it.id} className={`nav-item ${view === it.id ? 'active' : ''}`} onClick={() => setView(it.id)} data-screen-label={`nav-${it.id}`}>
            <Icon size={16}/>
            <span>{it.label}</span>
            {it.count ? <span className="count">{it.count}</span> : null}
          </div>
        );
      })}
      <div className="nav-sep" style={{margin:'14px 10px 8px'}} />
      <div style={{ padding: '0 10px', fontSize: 11, color: 'var(--muted)', lineHeight: 1.5 }}>
        <div className="flow" style={{ padding: '8px 10px', borderRadius: 8 }}>
          <span className="node done" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('gmail')}><I.Mail size={13}/></span>
          <span className="arr">→</span>
          <span className="node done" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('ai')}><I.Sparkle size={13}/></span>
          <span className="arr">→</span>
          <span className="node done" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('drive')}><I.Drive size={13}/></span>
          <span className="arr">→</span>
          <span className="node active" style={{ cursor: selectedMsg?'pointer':'default', opacity: selectedMsg?1:0.5 }} onClick={() => selectedMsg && onPipeline('bezala')}><I.Bezala size={13}/></span>
        </div>
      </div>
    </aside>
  );
}

window.Fas5Sidebar = Fas5Sidebar;
