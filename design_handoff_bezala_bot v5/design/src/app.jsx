const { useState: useStateA, useEffect: useEffectA } = React;

function App() {
  const [variant, setVariant] = useStateA(() => localStorage.getItem('bb_variant') || 'A');
  const [lang, setLang] = useStateA(() => localStorage.getItem('bb_lang') || 'sv');
  const [density, setDensity] = useStateA(() => localStorage.getItem('bb_density') || 'cozy');
  const [view, setView] = useStateA(() => localStorage.getItem('bb_view') || 'dashboard');
  const [tweaksActive, setTweaksActive] = useStateA(false);
  const [scanning, setScanning] = useStateA(false);
  const [toast, setToast] = useStateA(null);
  const [messages, setMessages] = useStateA(() => window.MOCK_MESSAGES);
  const [selectedId, setSelectedId] = useStateA(null);
  const [drawerStep, setDrawerStep] = useStateA(null);
  const selectedMsg = messages.find(m => m.id === selectedId) || null;
  const runs = window.MOCK_RUNS;
  const t = useI18n(lang);

  useEffectA(() => { applyTheme(variant, density); localStorage.setItem('bb_variant', variant); }, [variant, density]);
  useEffectA(() => { localStorage.setItem('bb_lang', lang); }, [lang]);
  useEffectA(() => { localStorage.setItem('bb_density', density); }, [density]);
  useEffectA(() => { localStorage.setItem('bb_view', view); }, [view]);

  // Edit mode integration
  useEffectA(() => {
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
  };

  function onScan() {
    setScanning(true);
    setTimeout(() => { setScanning(false); setToast(lang==='sv'?'Scanning klar — inga nya kvitton':'Scan complete — no new receipts'); }, 1400);
  }

  function onTransfer(msg) {
    setMessages(ms => ms.map(m => m.id === msg.id ? { ...m, status: 'transferred' } : m));
    setToast(lang==='sv'?`Överförd till Bezala: ${msg.vendor.name}`:`Sent to Bezala: ${msg.vendor.name}`);
  }

  const title = {
    dashboard: t.nav.dashboard,
    review:    t.nav.review,
    log:       t.nav.log,
    settings:  t.nav.settings,
  }[view];

  return (
    <>
      <GlobalStyles />
      <div className="shell" data-screen-label={`00 App / ${view}`}>
        <Sidebar view={view} setView={setView} t={t} counts={counts} selectedMsg={selectedMsg} onPipeline={(s) => setDrawerStep(s)} lang={lang} />
        <div className="main">
          <TopBar title={title} lang={lang} setLang={setLang} variant={variant} setVariant={setVariant} onScan={onScan} scanning={scanning} t={t} selectedMsg={selectedMsg} onPipeline={(s) => setDrawerStep(s)} />
          {view === 'dashboard' && <Dashboard t={t} lang={lang} messages={messages} runs={runs} onOpenReview={() => setView('review')} selected={selectedId} setSelected={setSelectedId} />}
          {view === 'review' && <ReviewScreen t={t} lang={lang} messages={messages} onTransfer={onTransfer} selectedId={selectedId} setSelectedId={setSelectedId} />}
          {view === 'log' && <LogScreen t={t} lang={lang} runs={runs} messages={messages} onOpenMessage={(id) => { setSelectedId(id); setDrawerStep('bezala'); }} />}
          {view === 'settings' && <SettingsScreen t={t} lang={lang} />}
        </div>
      </div>
      <TweaksPanel active={tweaksActive} setActive={setTweaksActive} variant={variant} setVariant={setVariant} density={density} setDensity={setDensity} lang={lang} setLang={setLang} />
      <Drawer step={drawerStep} msg={selectedMsg} lang={lang} onClose={() => setDrawerStep(null)} onGoReview={(id) => { setSelectedId(id); setView('review'); }} />
      <Toast message={toast} onDone={() => setToast(null)} />
    </>
  );
}

// Tweak defaults so the host can rewrite on disk
const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "variant": "A",
  "lang": "sv",
  "density": "cozy"
}/*EDITMODE-END*/;

ReactDOM.createRoot(document.getElementById('root')).render(<App />);
