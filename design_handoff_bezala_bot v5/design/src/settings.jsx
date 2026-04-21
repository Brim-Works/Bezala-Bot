function SettingsScreen({ t, lang }) {
  const [interval, setInterval] = React.useState(60);
  const [threshold, setThreshold] = React.useState(90);
  const [ai, setAi] = React.useState(true);
  const [auto, setAuto] = React.useState(true);
  const [includes, setIncludes] = React.useState(['finnair.com','vr.fi','hsl.fi','scandichotels.com']);
  const [excludes, setExcludes] = React.useState(['newsletter@','marketing@']);
  const [excludeSubjects, setExcludeSubjects] = React.useState(['Offer', 'Kampanja']);
  const [includeSubjects, setIncludeSubjects] = React.useState(['Kuitti', 'Receipt']);

  return (
    <div className="content" style={{ maxWidth: 880 }}>
      <div className="hero-strip">
        <div style={{ flex: 1 }}>
          <h1>{t.settings.title}</h1>
        </div>
        <div className="sub">{lang==='sv' ? 'Konfigurera scanning, AI och överföring.' : 'Configure scanning, AI and transfer.'}</div>
      </div>

      <div className="sh"><h2>{t.settings.rules}</h2></div>
      <div className="card card-pad" style={{ padding: 20 }}>
        <div className="fld-row">
          <div className="fld">
            <label>{t.settings.include}</label>
            <div className="input" style={{ padding: 10, minHeight: 40, display:'flex', gap: 5, flexWrap:'wrap', alignItems:'center' }}>
              {includes.map(v => (
                <span key={v} className="pill muted" style={{ paddingRight: 2 }}>
                  {v}
                  <button className="btn ghost sm" style={{ padding:'0 4px' }} onClick={() => setIncludes(includes.filter(x=>x!==v))}>×</button>
                </span>
              ))}
              <input placeholder={lang==='sv'?'+ lägg till domän':'+ add domain'} style={{ background: 'transparent', border: 0, outline: 0, flex: 1, minWidth: 140, fontSize: 12 }} />
            </div>
          </div>
          <div className="fld">
            <label>{t.settings.exclude}</label>
            <div className="input" style={{ padding: 10, minHeight: 40, display:'flex', gap: 5, flexWrap:'wrap', alignItems:'center' }}>
              {excludes.map(v => (
                <span key={v} className="pill muted">
                  {v}
                  <button className="btn ghost sm" style={{ padding:'0 4px' }} onClick={() => setExcludes(excludes.filter(x=>x!==v))}>×</button>
                </span>
              ))}
              <input placeholder={lang==='sv'?'+ lägg till':'+ add'} style={{ background: 'transparent', border: 0, outline: 0, flex: 1, minWidth: 120, fontSize: 12 }} />
            </div>
          </div>
        </div>

        <div className="fld-row">
          <div className="fld">
            <label>{t.settings.includeSubjects}</label>
            <div className="input" style={{ padding: 10, minHeight: 40, display:'flex', gap: 5, flexWrap:'wrap', alignItems:'center' }}>
              {includeSubjects.map(v => (
                <span key={v} className="pill muted">
                  {v}
                  <button className="btn ghost sm" style={{ padding:'0 4px' }} onClick={() => setIncludeSubjects(includeSubjects.filter(x=>x!==v))}>×</button>
                </span>
              ))}
              <input placeholder={lang==='sv'?'+ substring':'+ substring'} style={{ background: 'transparent', border: 0, outline: 0, flex: 1, minWidth: 120, fontSize: 12 }} />
            </div>
          </div>
          <div className="fld">
            <label>{t.settings.excludeSubjects}</label>
            <div className="input" style={{ padding: 10, minHeight: 40, display:'flex', gap: 5, flexWrap:'wrap', alignItems:'center' }}>
              {excludeSubjects.map(v => (
                <span key={v} className="pill muted">
                  {v}
                  <button className="btn ghost sm" style={{ padding:'0 4px' }} onClick={() => setExcludeSubjects(excludeSubjects.filter(x=>x!==v))}>×</button>
                </span>
              ))}
              <input placeholder={lang==='sv'?'+ substring':'+ substring'} style={{ background: 'transparent', border: 0, outline: 0, flex: 1, minWidth: 120, fontSize: 12 }} />
            </div>
          </div>
        </div>
      </div>

      <div className="sh"><h2>{lang==='sv'?'AI och överföring':'AI & transfer'}</h2></div>
      <div className="card card-pad" style={{ padding: 20 }}>
        <div className="fld-row">
          <div className="fld">
            <label>{t.settings.interval}</label>
            <select value={interval} onChange={e => setInterval(parseInt(e.target.value))}>
              <option value={15}>15 min</option>
              <option value={30}>30 min</option>
              <option value={60}>{lang==='sv'?'1 timme':'1 hour'}</option>
              <option value={240}>{lang==='sv'?'4 timmar':'4 hours'}</option>
            </select>
          </div>
          <div className="fld">
            <label>{lang==='sv'?'AI-modell':'AI model'}</label>
            <select>
              <option>Claude Haiku 4.5</option>
              <option>Claude Sonnet 4.5</option>
            </select>
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginTop: 4 }}>
          <label style={{ display:'flex', alignItems:'center', gap: 10, padding: 14, border:'1px solid var(--border)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', background: ai ? 'color-mix(in oklch, var(--accent) 6%, var(--surface))' : 'var(--surface)' }}>
            <input type="checkbox" checked={ai} onChange={e => setAi(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 500 }}>{t.settings.ai}</div>
              <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 2 }}>{lang==='sv'?'Claude döper filer automatiskt':'Claude renames files automatically'}</div>
            </div>
          </label>
          <label style={{ display:'flex', alignItems:'center', gap: 10, padding: 14, border:'1px solid var(--border)', borderRadius: 'var(--radius-sm)', cursor: 'pointer', background: auto ? 'color-mix(in oklch, var(--accent) 6%, var(--surface))' : 'var(--surface)' }}>
            <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} style={{ accentColor: 'var(--accent)' }} />
            <div>
              <div style={{ fontSize: 13, fontWeight: 500 }}>{t.settings.auto}</div>
              <div style={{ fontSize: 11.5, color: 'var(--muted)', marginTop: 2 }}>{lang==='sv'?'Över tröskelvärdet': 'Above the threshold'}</div>
            </div>
          </label>
        </div>

        <div className="fld" style={{ marginTop: 16 }}>
          <label style={{ display:'flex', justifyContent:'space-between' }}>
            <span>{t.settings.threshold}</span>
            <span className="mono" style={{ color: 'var(--text)' }}>{threshold}%</span>
          </label>
          <input type="range" min="0" max="100" value={threshold} onChange={e => setThreshold(parseInt(e.target.value))} style={{ accentColor: 'var(--accent)' }} />
          <div className="hint">{lang==='sv'?'Kvitton över ':'Receipts above '}{threshold}%{lang==='sv'?' skickas direkt till Bezala. Under går till Granska.':' go straight to Bezala. Below land in Review.'}</div>
        </div>
      </div>

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: 20 }}>
        <button className="btn primary" style={{ padding: '9px 18px' }}>{t.settings.save}</button>
      </div>
    </div>
  );
}

function LogScreen({ t, lang, runs }) {
  return (
    <div className="content" style={{ maxWidth: 1000 }}>
      <div className="hero-strip"><div style={{flex:1}}><h1>{t.nav.log}</h1></div><div className="sub">{lang==='sv'?'Varje timmes scanning loggas här.':'Every hourly scan is logged here.'}</div></div>
      <div className="card" style={{ overflow: 'hidden' }}>
        <table className="tbl">
          <thead>
            <tr>
              <th>{t.cols.time}</th>
              <th>{lang==='sv'?'Hittade':'Found'}</th>
              <th>{lang==='sv'?'Bearbetade':'Processed'}</th>
              <th>{lang==='sv'?'Hoppade':'Skipped'}</th>
              <th>{t.stats.errors}</th>
              <th>{t.cols.status}</th>
              <th>{lang==='sv'?'Varaktighet':'Duration'}</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(r => {
              const dur = Math.round((r.finished_at - r.started_at) / 1000);
              return (
                <tr key={r.id}>
                  <td>{fmtDateTime(r.started_at, lang)}</td>
                  <td className="num">{r.messages_found}</td>
                  <td className="num">{r.messages_processed}</td>
                  <td className="num">{r.messages_skipped}</td>
                  <td className="num" style={{ color: r.errors ? 'var(--err)' : 'inherit' }}>{r.errors}</td>
                  <td><Pill kind={r.errors ? 'err' : 'ok'} dot>{r.status}</Pill></td>
                  <td className="num">{dur}s</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

window.SettingsScreen = SettingsScreen;
window.LogScreen = LogScreen;
