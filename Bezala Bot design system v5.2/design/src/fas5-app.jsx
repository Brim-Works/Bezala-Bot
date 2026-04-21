const { useState: useStateF5, useEffect: useEffectF5 } = React;

function Fas5App() {
  const [variant, setVariant] = useStateF5(() => localStorage.getItem('bb_variant') || 'A');
  const [lang, setLang]       = useStateF5(() => localStorage.getItem('bb_lang') || 'sv');
  const [density, setDensity] = useStateF5(() => localStorage.getItem('bb_density') || 'cozy');
  const [view, setView]       = useStateF5(() => localStorage.getItem('bb5_view') || 'trash');
  const [tweaksActive, setTweaksActive] = useStateF5(false);
  const [scanning, setScanning] = useStateF5(false);
  const [toast, setToast] = useStateF5(null);

  // Shared FAS 4 state
  const [messages, setMessages] = useStateF5(() => window.MOCK_MESSAGES);
  const [selectedId, setSelectedId] = useStateF5(null);
  const [drawerStep, setDrawerStep] = useStateF5(null);
  const selectedMsg = messages.find(m => m.id === selectedId) || null;
  const runs = window.MOCK_RUNS;
  const t = useI18n(lang);

  // FAS 5 state
  const [trash, setTrash] = useStateF5(() => window.FAS5_DATA.TRASH_ROWS);
  const [rules, setRules] = useStateF5(() => window.FAS5_DATA.RULES);
  const [patterns, setPatterns] = useStateF5(() => window.FAS5_DATA.PATTERNS);
  const [cardRows, setCardRows] = useStateF5(() => window.FAS5_DATA.CARD_ROWS);
  const feedback = window.FAS5_DATA.FEEDBACK;

  useEffectF5(() => { applyTheme(variant, density); localStorage.setItem('bb_variant', variant); }, [variant, density]);
  useEffectF5(() => { localStorage.setItem('bb_lang', lang); }, [lang]);
  useEffectF5(() => { localStorage.setItem('bb_density', density); }, [density]);
  useEffectF5(() => { localStorage.setItem('bb5_view', view); }, [view]);

  useEffectF5(() => {
    function onMsg(e) {
      const d = e.data || {};
      if (d.type === '__activate_edit_mode') setTweaksActive(true);
      if (d.type === '__deactivate_edit_mode') setTweaksActive(false);
    }
    window.addEventListener('message', onMsg);
    window.parent.postMessage({ type: '__edit_mode_available' }, '*');
    return () => window.removeEventListener('message', onMsg);
  }, []);

  const counts = {
    pending: messages.filter(m => m.status === 'pending').length,
    trash: trash.length,
    cards_suggested: cardRows.filter(r=>r.status==='suggested').length,
  };

  function onScan() {
    setScanning(true);
    setTimeout(() => { setScanning(false); setToast(lang==='sv'?'Scanning klar':'Scan complete'); }, 1400);
  }
  function onTransfer(msg) {
    setMessages(ms => ms.map(m => m.id === msg.id ? { ...m, status: 'transferred' } : m));
    setToast(lang==='sv'?`Överförd till Bezala: ${msg.vendor.name}`:`Sent to Bezala: ${msg.vendor.name}`);
  }

  // FAS 5 handlers
  function onRestore(id) {
    setTrash(rows => rows.filter(r=>r.id!==id));
    setToast(lang==='sv'?'Återställd':'Restored');
  }
  function onPurge(id) {
    setTrash(rows => rows.filter(r=>r.id!==id));
    setToast(lang==='sv'?'Raderad permanent':'Permanently deleted');
  }
  function onEmptyAll() {
    if (!confirm(lang==='sv'?'Tömma papperskorgen? Kan inte ångras.':'Empty trash? Cannot be undone.')) return;
    setTrash([]);
    setToast(lang==='sv'?'Papperskorgen tömd':'Trash emptied');
  }

  function onRuleToggle(id)   { setRules(rs => rs.map(r => r.id===id ? {...r, active:!r.active} : r)); }
  function onRuleEdit(updated){ setRules(rs => rs.map(r => r.id===updated.id ? updated : r)); setToast(lang==='sv'?'Regel sparad':'Rule saved'); }
  function onRuleDuplicate(id){ const r = rules.find(x=>x.id===id); setRules(rs => [...rs, {...r, id:'r'+Date.now(), name:r.name+' (kopia)', stats:{matches_30d:0}}]); }
  function onRuleDelete(id)   { if (!confirm(lang==='sv'?'Radera regel?':'Delete rule?')) return; setRules(rs => rs.filter(r=>r.id!==id)); }
  function onRuleReorder(from, to) {
    setRules(rs => { const n = [...rs]; const [moved] = n.splice(from,1); n.splice(to,0,moved); return n; });
  }
  function onRuleNew() {
    const r = { id:'r'+Date.now(), name:(lang==='sv'?'Ny regel':'New rule'), active:true,
      match:{from:'', subject_any:[], has_attachment:false, min_amount:0, currency:'EUR'},
      action:{category:'Övrigt', bezala_account:'', auto_upload:false, notify_first:true},
      stats:{matches_30d:0} };
    setRules(rs => [...rs, r]);
  }
  function onRuleTest(id) {
    const r = rules.find(x=>x.id===id);
    const n = Math.floor(Math.random()*12)+1;
    setToast(lang==='sv'?`Dry-run: ${n} mail skulle matchat "${r.name}"`:`Dry-run: ${n} mail would match "${r.name}"`);
  }

  function onForgetPattern(id){ setPatterns(p => p.filter(x=>x.id!==id)); }

  function onCardConfirm(rowId, candidateId) {
    setCardRows(rows => rows.map(r => r.id===rowId ? {...r, status:'auto', candidates: r.candidates.filter(c=>c.id===candidateId)} : r));
    setToast(lang==='sv'?'Kvitto bifogat till kortrad i Bezala':'Receipt attached to card row in Bezala');
  }
  function onCardIgnore(rowId, candidateId) {
    setCardRows(rows => rows.map(r => {
      if (r.id !== rowId) return r;
      if (candidateId === null) return {...r, status:'manual'}; // mark orphan as manual
      const candidates = r.candidates.filter(c => c.id !== candidateId);
      return {...r, candidates, status: candidates.length ? r.status : 'orphan'};
    }));
  }

  const title = {
    dashboard: t.nav.dashboard, review: t.nav.review, log: t.nav.log, settings: t.nav.settings,
    trash: t.nav.trash, rules: t.nav.rules, patterns: t.nav.patterns, cards: t.nav.cards,
  }[view];

  return (
    <>
      <GlobalStyles />
      <div className="shell" data-screen-label={`00 App / ${view}`}>
        <Fas5Sidebar view={view} setView={setView} t={t} counts={counts} selectedMsg={selectedMsg} onPipeline={(s)=>setDrawerStep(s)} lang={lang} />
        <div className="main">
          <TopBar title={title} lang={lang} setLang={setLang} variant={variant} setVariant={setVariant} onScan={onScan} scanning={scanning} t={t} selectedMsg={selectedMsg} onPipeline={(s)=>setDrawerStep(s)} />

          {view === 'dashboard' && <Dashboard t={t} lang={lang} messages={messages} runs={runs} onOpenReview={()=>setView('review')} selected={selectedId} setSelected={setSelectedId} />}
          {view === 'review'    && <ReviewScreen t={t} lang={lang} messages={messages} onTransfer={onTransfer} selectedId={selectedId} setSelectedId={setSelectedId} />}
          {view === 'log'       && <LogScreen t={t} lang={lang} runs={runs} messages={messages} onOpenMessage={(id)=>{ setSelectedId(id); setDrawerStep('bezala'); }} />}
          {view === 'settings'  && <SettingsScreen t={t} lang={lang} />}

          {view === 'trash'     && <TrashScreen t={t} lang={lang} trashed={trash} onRestore={onRestore} onPurge={onPurge} onEmptyAll={onEmptyAll} />}
          {view === 'rules'     && <RulesScreen t={t} lang={lang} rules={rules} onToggle={onRuleToggle} onEdit={onRuleEdit} onDuplicate={onRuleDuplicate} onDelete={onRuleDelete} onReorder={onRuleReorder} onNew={onRuleNew} onTest={onRuleTest} />}
          {view === 'patterns'  && <PatternsScreen t={t} lang={lang} patterns={patterns} feedback={feedback} onForget={onForgetPattern} />}
          {view === 'cards'     && <CardMatchScreen t={t} lang={lang} cardRows={cardRows} onConfirm={onCardConfirm} onIgnore={onCardIgnore} />}
        </div>
      </div>
      <TweaksPanel active={tweaksActive} setActive={setTweaksActive} variant={variant} setVariant={setVariant} density={density} setDensity={setDensity} lang={lang} setLang={setLang} />
      <Drawer step={drawerStep} msg={selectedMsg} lang={lang} onClose={()=>setDrawerStep(null)} onGoReview={(id)=>{ setSelectedId(id); setView('review'); }} />
      <Toast message={toast} onDone={()=>setToast(null)} />
    </>
  );
}

const TWEAK_DEFAULTS_F5 = /*EDITMODE-BEGIN*/{
  "variant": "A",
  "lang": "sv",
  "density": "cozy"
}/*EDITMODE-END*/;

ReactDOM.createRoot(document.getElementById('root')).render(<Fas5App />);
