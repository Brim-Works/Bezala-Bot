function TweaksPanel({ active, setActive, variant, setVariant, density, setDensity, lang, setLang }) {
  if (!active) return null;
  return (
    <div className="tweaks">
      <div className="tweaks-head">
        <h3>Tweaks</h3>
        <button className="btn ghost sm" onClick={() => setActive(false)}><I.X size={12}/></button>
      </div>
      <div className="tweaks-body">
        <div className="tw-row">
          <label>Variant</label>
          <div className="tw-opts">
            <div className={`opt ${variant==='A'?'active':''}`} onClick={() => setVariant('A')}>A · Ljust</div>
            <div className={`opt ${variant==='B'?'active':''}`} onClick={() => setVariant('B')}>B · Skog</div>
          </div>
        </div>
        <div className="tw-row">
          <label>Språk / Language</label>
          <div className="tw-opts">
            <div className={`opt ${lang==='sv'?'active':''}`} onClick={() => setLang('sv')}>Svenska</div>
            <div className={`opt ${lang==='en'?'active':''}`} onClick={() => setLang('en')}>English</div>
          </div>
        </div>
        <div className="tw-row">
          <label>Density</label>
          <div className="tw-opts">
            <div className={`opt ${density==='cozy'?'active':''}`} onClick={() => setDensity('cozy')}>Cozy</div>
            <div className={`opt ${density==='compact'?'active':''}`} onClick={() => setDensity('compact')}>Compact</div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.TweaksPanel = TweaksPanel;
