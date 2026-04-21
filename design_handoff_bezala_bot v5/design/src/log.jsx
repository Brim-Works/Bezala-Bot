// Log / Kommandocenter — split view with run list + pipeline narrative detail.
const { useState: useStateL, useMemo: useMemoL } = React;

function fmtDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms/1000).toFixed(1)}s`;
}

function LogScreen({ t, lang, runs, messages, onOpenMessage }) {
  const [selectedId, setSelectedId] = useStateL(runs[0]?.id);
  const selected = runs.find(r => r.id === selectedId) || runs[0];
  // KPI aggregates
  const stats = useMemoL(() => {
    const last24h = runs; // mock data is already <24h
    const totalProcessed = last24h.reduce((s,r) => s + r.messages_processed, 0);
    const totalErrors = last24h.reduce((s,r) => s + (r.errors||0), 0);
    const totalAuto = last24h.reduce((s,r) => s + (r.stages?.bezala?.auto || 0), 0);
    const totalCost = last24h.reduce((s,r) => s + (r.stages?.ai?.cost_cents || 0), 0);
    const autoRate = totalProcessed ? Math.round((totalAuto / totalProcessed) * 100) : 0;
    return { processed: totalProcessed, errors: totalErrors, auto: totalAuto, autoRate, costCents: totalCost, runs: last24h.length };
  }, [runs]);

  return (
    <div className="content" style={{ paddingBottom: 24 }}>
      <div className="hero-strip">
        <div style={{flex:1}}><h1>{lang==='sv'?'Kommandocenter':'Command center'}</h1></div>
        <div className="sub">{lang==='sv'?'Varje scanning, varje steg, full spårbarhet.':'Every scan, every step, full traceability.'}</div>
      </div>

      {/* KPI strip */}
      <div className="stat-grid" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        <div className="stat">
          <div className="l">{lang==='sv'?'Körningar 24h':'Runs 24h'}</div>
          <div className="v">{stats.runs}</div>
          <div className="sub">{lang==='sv'?'var 60:e minut':'every 60 min'}</div>
        </div>
        <div className="stat">
          <div className="l">{lang==='sv'?'Auto-rate':'Auto-rate'}</div>
          <div className="v">{stats.autoRate}<span style={{fontSize:18, color:'var(--muted)'}}>%</span></div>
          <div className="sub">{stats.auto} / {stats.processed} {lang==='sv'?'överförda':'transferred'}</div>
        </div>
        <div className="stat">
          <div className="l">{lang==='sv'?'AI-kostnad':'AI spend'}</div>
          <div className="v mono" style={{fontSize:20}}>€{(stats.costCents/100).toFixed(2)}</div>
          <div className="sub">{lang==='sv'?'senaste 24h':'last 24h'}</div>
        </div>
        <div className={`stat ${stats.errors?'accent':''}`} style={stats.errors? {borderColor:'var(--err)'}:{}}>
          <div className="l">{lang==='sv'?'Fel':'Errors'}</div>
          <div className="v" style={{ color: stats.errors?'var(--err)':'var(--text)' }}>{stats.errors}</div>
          <div className="sub">{stats.errors ? (lang==='sv'?'kräver åtgärd':'needs attention') : (lang==='sv'?'allt grönt':'all green')}</div>
        </div>
      </div>

      {/* Split layout */}
      <div className="log-split">
        {/* LEFT — run list */}
        <div className="log-list card" style={{ padding: 0, overflow: 'hidden' }}>
          <div className="log-list-head">
            <span style={{ fontSize: 12, fontWeight: 600, textTransform:'uppercase', letterSpacing: 0.05 }}>{lang==='sv'?'Körningar':'Runs'}</span>
            <span className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>{runs.length}</span>
          </div>
          <div className="log-list-scroll">
            {runs.map(r => {
              const dur = r.finished_at - r.started_at;
              const isSel = r.id === selected?.id;
              const tone = r.errors ? 'err' : (r.messages_processed === 0 ? 'muted' : 'ok');
              return (
                <div key={r.id} className={`log-run ${isSel?'active':''} ${tone}`} onClick={() => setSelectedId(r.id)}>
                  <div className="log-run-dot" />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="log-run-time mono">{fmtDateTime(r.started_at, lang)}</div>
                    <div className="log-run-summary">
                      {r.messages_processed > 0
                        ? `${r.messages_processed} ${lang==='sv'?'bearbetade':'processed'}`
                        : (lang==='sv'?'Inga nya mail':'No new mail')}
                      {r.errors ? ` · ${r.errors} ${lang==='sv'?'fel':r.errors===1?'error':'errors'}` : ''}
                    </div>
                  </div>
                  <div className="log-run-dur mono">{fmtDuration(dur)}</div>
                </div>
              );
            })}
          </div>
        </div>

        {/* RIGHT — detail panel */}
        <div className="log-detail">
          {selected ? (
            <LogDetail run={selected} messages={messages} lang={lang} t={t} onOpenMessage={onOpenMessage} />
          ) : (
            <div className="card card-pad" style={{ padding: 40, textAlign:'center', color:'var(--muted)' }}>
              {lang==='sv'?'Välj en körning':'Select a run'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function LogDetail({ run, messages, lang, t, onOpenMessage }) {
  const dur = run.finished_at - run.started_at;
  const stages = [
    { key: 'gmail',  icon: I.Mail,    label: 'Gmail',  ...run.stages.gmail },
    { key: 'ai',     icon: I.Sparkle, label: 'AI',     ...run.stages.ai },
    { key: 'drive',  icon: I.Drive,   label: 'Drive',  ...run.stages.drive },
    { key: 'bezala', icon: I.Bezala,  label: 'Bezala', ...run.stages.bezala },
  ];
  const maxMs = Math.max(...stages.map(s => s.duration_ms), 100);
  const runMsgs = (run.message_ids || []).map(id => messages.find(m => m.id === id)).filter(Boolean);

  return (
    <div style={{ display:'flex', flexDirection:'column', gap: 16 }}>
      {/* Header */}
      <div className="card card-pad" style={{ padding: '18px 22px' }}>
        <div style={{ display:'flex', alignItems:'flex-start', gap: 16 }}>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11.5, color: 'var(--muted)', textTransform:'uppercase', letterSpacing: 0.08 }}>
              {lang==='sv'?'Körning':'Run'} #{run.id}
            </div>
            <div style={{ fontSize: 22, fontWeight: 600, marginTop: 4, fontFamily: 'var(--font-serif, "Instrument Serif", serif)', letterSpacing: -0.3 }}>
              {fmtDateTime(run.started_at, lang)}
            </div>
            <div style={{ fontSize: 13, color:'var(--text-2)', marginTop: 6, lineHeight: 1.55 }}>
              {narrative(run, lang)}
            </div>
          </div>
          <div style={{ display:'flex', flexDirection:'column', alignItems:'flex-end', gap: 6 }}>
            <Pill kind={run.errors ? 'err' : (run.messages_processed === 0 ? 'muted' : 'ok')} dot>
              {run.errors ? (lang==='sv'?'Partiell':'Partial') : (run.messages_processed === 0 ? (lang==='sv'?'Tom':'Idle') : 'OK')}
            </Pill>
            <div className="mono" style={{ fontSize: 12, color: 'var(--muted)' }}>{fmtDuration(dur)}</div>
          </div>
        </div>
      </div>

      {/* Pipeline timeline */}
      <div className="card card-pad" style={{ padding: '20px 22px' }}>
        <div style={{ fontSize: 11.5, color: 'var(--muted)', textTransform:'uppercase', letterSpacing: 0.08, marginBottom: 14 }}>
          {lang==='sv'?'Pipeline':'Pipeline'}
        </div>
        <div className="pipe-timeline">
          {stages.map((s, i) => (
            <div key={s.key} className="pipe-row">
              <div className="pipe-step">
                <div className={`pipe-icon ${s.status}`}>
                  <s.icon size={14} />
                </div>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 500 }}>{s.label}</div>
                  <div style={{ fontSize: 11.5, color: 'var(--muted)' }}>{s.note}</div>
                </div>
              </div>
              <div className="pipe-bar-wrap">
                <div className={`pipe-bar ${s.status}`} style={{ width: `${Math.max(3, (s.duration_ms / maxMs) * 100)}%` }} />
                <div className="mono pipe-dur">{fmtDuration(s.duration_ms)}</div>
              </div>
            </div>
          ))}
        </div>
        {run.stages.ai.tokens_in ? (
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: '1px solid var(--border)', display:'flex', gap: 18, fontSize: 11.5, color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            <span>input: {run.stages.ai.tokens_in.toLocaleString()} tokens</span>
            <span>output: {run.stages.ai.tokens_out.toLocaleString()} tokens</span>
            <span>cost: €{(run.stages.ai.cost_cents/100).toFixed(3)}</span>
          </div>
        ) : null}
      </div>

      {/* Messages in this run */}
      {runMsgs.length > 0 ? (
        <div className="card" style={{ overflow: 'hidden' }}>
          <div style={{ padding: '14px 20px', borderBottom:'1px solid var(--border)', display:'flex', alignItems:'center', justifyContent:'space-between' }}>
            <div style={{ fontSize: 11.5, color: 'var(--muted)', textTransform:'uppercase', letterSpacing: 0.08 }}>
              {lang==='sv'?'Meddelanden i denna körning':'Messages in this run'}
            </div>
            <span className="mono" style={{ fontSize: 11.5, color: 'var(--muted)' }}>{runMsgs.length}</span>
          </div>
          <table className="tbl">
            <tbody>
              {runMsgs.map(m => (
                <tr key={m.id} onClick={() => onOpenMessage(m.id)} style={{ cursor: 'pointer' }}>
                  <td style={{ width: 90 }} className="num" title={lang==='sv'?'Öppna pipeline':'Open pipeline'}>
                    <span className="mono" style={{ fontSize: 11, color: 'var(--muted)' }}>#{String(m.id).padStart(4,'0')}</span>
                  </td>
                  <td style={{ width: 200 }}>
                    <span className="vchip">
                      <VendorLogo vendor={m.vendor} size={22} />
                      <span style={{ fontSize: 13 }}>{m.vendor.name}</span>
                    </span>
                  </td>
                  <td style={{ color: 'var(--text-2)', maxWidth: 320, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{m.subject}</td>
                  <td className="num" style={{ width: 100, textAlign: 'right' }}>
                    {m.amount ? fmtAmount(m.amount, m.currency, lang) : <span style={{color:'var(--muted)'}}>—</span>}
                  </td>
                  <td style={{ width: 160 }}><StatusCell m={m} t={t} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function narrative(run, lang) {
  const time = `${run.started_at.getHours()}:${String(run.started_at.getMinutes()).padStart(2,'0')}`;
  if (run.messages_found === 0) {
    return lang==='sv'
      ? `Kl. ${time} — scanning körd, inga nya mail matchade filtret.`
      : `At ${time} — scan ran, no new mail matched the filter.`;
  }
  if (run.errors) {
    return lang==='sv'
      ? `Kl. ${time} hittade botten ${run.messages_found} nya mail. AI bearbetade ${run.messages_processed} av dem, men Bezala-överföringen misslyckades för ${run.errors} rad.`
      : `At ${time} the bot found ${run.messages_found} new mails. AI processed ${run.messages_processed}, but Bezala transfer failed for ${run.errors} row.`;
  }
  const auto = run.stages.bezala.auto || 0;
  const queued = run.stages.bezala.queued || 0;
  if (lang === 'sv') {
    return `Kl. ${time} hittade botten ${run.messages_found} nya mail → AI extraherade alla fält → ${auto} auto-överförda till Bezala${queued ? ` · ${queued} väntar granskning` : ''}.`;
  }
  return `At ${time} the bot found ${run.messages_found} new mails → AI extracted all fields → ${auto} auto-transferred to Bezala${queued ? ` · ${queued} awaiting review` : ''}.`;
}

window.LogScreen = LogScreen;
