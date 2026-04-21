// Shared UI primitives, theme-aware via CSS variables.

const { useState, useEffect, useRef, useMemo } = React;

// ============ GLOBAL STYLES injected once ============
function GlobalStyles() {
  return (
    <style>{`
      :root { color-scheme: light dark; }
      *, *::before, *::after { box-sizing: border-box; }
      body { margin: 0; font-family: var(--font-sans); color: var(--text); background: var(--bg); font-feature-settings: "ss01","cv11"; -webkit-font-smoothing: antialiased; }
      a { color: inherit; }
      button { font: inherit; cursor: pointer; }
      input, select, textarea { font: inherit; color: inherit; }

      /* ========= SHELL ========= */
      .shell { display: grid; grid-template-columns: 232px 1fr; min-height: 100vh; background: var(--bg); }
      [data-theme="B"] .shell { background: var(--bg); }
      .sidebar {
        border-right: 1px solid var(--border);
        padding: 20px 14px;
        display: flex; flex-direction: column; gap: 4px;
        background: var(--bg);
      }
      [data-theme="B"] .sidebar { background: var(--bg-2); }
      .brand { display: flex; align-items: center; gap: 10px; padding: 6px 10px 18px; }
      .brand-mark { width: 28px; height: 28px; border-radius: 7px; display: grid; place-items: center; background: var(--accent); color: var(--accent-ink); font-weight: 700; font-size: 13px; letter-spacing: -0.02em; }
      [data-theme="B"] .brand-mark { border-radius: 4px; }
      .brand-name { font-family: var(--font-display); font-size: 19px; font-weight: 500; letter-spacing: -0.01em; }
      [data-theme="A"] .brand-name { font-weight: 600; font-size: 15px; letter-spacing: 0.01em; }
      .nav-item { display: flex; align-items: center; gap: 11px; padding: 8px 11px; border-radius: var(--radius-sm); color: var(--text-2); cursor: pointer; font-size: 13.5px; border: 1px solid transparent; user-select: none; }
      .nav-item:hover { background: var(--surface-2); color: var(--text); }
      .nav-item.active { background: var(--surface); color: var(--text); border-color: var(--border); }
      [data-theme="B"] .nav-item.active { background: var(--surface); box-shadow: var(--shadow); }
      .nav-item .count { margin-left: auto; font-variant-numeric: tabular-nums; font-size: 11.5px; color: var(--muted); background: var(--surface-2); padding: 1px 7px; border-radius: 999px; }
      .nav-item.active .count { background: var(--surface-3); color: var(--text-2); }
      .nav-sep { height: 1px; background: var(--border); margin: 14px 2px; }

      /* ========= MAIN ========= */
      .main { display: flex; flex-direction: column; min-width: 0; }
      .topbar { display: flex; align-items: center; gap: 10px; padding: 14px 28px; border-bottom: 1px solid var(--border); background: var(--bg); position: sticky; top: 0; z-index: 5; }
      [data-theme="B"] .topbar { background: var(--bg); }
      .topbar .title { font-family: var(--font-display); font-size: 17px; font-weight: 500; letter-spacing: -0.01em; }
      [data-theme="A"] .topbar .title { font-weight: 600; font-size: 14px; letter-spacing: 0.02em; text-transform: uppercase; color: var(--text-2); }
      .topbar .spacer { flex: 1; }
      .content { padding: 24px 28px 60px; max-width: 1400px; width: 100%; }

      /* ========= BUTTONS ========= */
      .btn { display: inline-flex; align-items: center; gap: 7px; padding: 7px 13px; border-radius: var(--radius-sm); border: 1px solid var(--border-strong); background: var(--surface); color: var(--text); font-size: 13px; font-weight: 500; transition: 0.12s ease; }
      .btn:hover { background: var(--surface-2); }
      .btn.primary { background: var(--accent); color: var(--accent-ink); border-color: transparent; font-weight: 600; }
      .btn.primary:hover { filter: brightness(1.06); }
      .btn.ghost { background: transparent; border-color: transparent; color: var(--text-2); }
      .btn.ghost:hover { background: var(--surface-2); color: var(--text); }
      .btn.danger { color: var(--err); border-color: color-mix(in oklch, var(--err) 40%, transparent); }
      .btn.sm { padding: 4px 9px; font-size: 12px; }
      .btn[disabled] { opacity: 0.5; cursor: not-allowed; }

      /* ========= CARDS ========= */
      .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow); }
      .card-pad { padding: var(--pad-2); }

      /* ========= STAT GRID ========= */
      .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; }
      .stat { padding: 16px 18px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); position: relative; overflow: hidden; }
      .stat .l { font-size: 11.5px; color: var(--muted); letter-spacing: 0.04em; text-transform: uppercase; font-weight: 500; }
      [data-theme="B"] .stat .l { text-transform: none; letter-spacing: 0; font-size: 13px; }
      .stat .v { font-family: var(--font-display); font-size: 36px; font-weight: 500; margin-top: 8px; letter-spacing: -0.02em; font-variant-numeric: tabular-nums; line-height: 1; }
      [data-theme="A"] .stat .v { font-family: var(--font-sans); font-weight: 600; font-size: 28px; }
      .stat .sub { font-size: 12px; color: var(--text-2); margin-top: 6px; font-variant-numeric: tabular-nums; }
      .stat.accent { background: linear-gradient(180deg, color-mix(in oklch, var(--accent) 14%, var(--surface)) 0%, var(--surface) 70%); }
      [data-theme="B"] .stat.accent { background: var(--surface); border-color: var(--accent); border-width: 1px; }

      /* ========= TABLE ========= */
      .tbl { width: 100%; border-collapse: separate; border-spacing: 0; font-size: 13px; }
      .tbl thead th { text-align: left; padding: 10px 14px; font-weight: 500; font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid var(--border); background: var(--bg-2); position: sticky; top: 0; }
      [data-theme="B"] .tbl thead th { text-transform: none; letter-spacing: 0; font-size: 12px; background: var(--surface-2); color: var(--text-2); }
      .tbl tbody td { padding: 0 14px; border-bottom: 1px solid var(--border); height: var(--row-h); vertical-align: middle; }
      .tbl tbody tr { cursor: pointer; }
      .tbl tbody tr:hover { background: var(--surface-2); }
      .tbl tbody tr.selected { background: color-mix(in oklch, var(--accent) 10%, var(--surface)); }
      [data-theme="B"] .tbl tbody tr.selected { background: color-mix(in oklch, var(--accent) 8%, var(--surface)); }
      .tbl .num { font-variant-numeric: tabular-nums; font-family: var(--font-mono); font-size: 12.5px; }

      /* ========= PILLS / BADGES ========= */
      .pill { display: inline-flex; align-items: center; gap: 5px; padding: 2px 8px; font-size: 11.5px; border-radius: 999px; border: 1px solid transparent; font-weight: 500; letter-spacing: 0.02em; }
      .pill .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
      .pill.ok      { color: var(--ok);    background: color-mix(in oklch, var(--ok) 14%, transparent); border-color: color-mix(in oklch, var(--ok) 25%, transparent); }
      .pill.warn    { color: var(--warn);  background: color-mix(in oklch, var(--warn) 14%, transparent); border-color: color-mix(in oklch, var(--warn) 25%, transparent); }
      .pill.err     { color: var(--err);   background: color-mix(in oklch, var(--err) 12%, transparent); border-color: color-mix(in oklch, var(--err) 25%, transparent); }
      .pill.muted   { color: var(--muted); background: var(--surface-2); border-color: var(--border); }
      .pill.accent  { color: var(--accent);background: color-mix(in oklch, var(--accent) 14%, transparent); border-color: color-mix(in oklch, var(--accent) 25%, transparent); }

      /* ========= VENDOR CHIP ========= */
      .vchip { display: inline-flex; align-items: center; gap: 9px; }
      .vlogo { width: 22px; height: 22px; border-radius: 6px; display: grid; place-items: center; color: white; font-weight: 600; font-size: 10px; letter-spacing: 0.02em; flex-shrink: 0; }
      [data-theme="B"] .vlogo { border-radius: 3px; font-family: var(--font-mono); }

      /* ========= FILTER BAR ========= */
      .fbar { display: flex; align-items: center; gap: 6px; padding: 8px 12px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); margin-bottom: 12px; }
      .fbar .tab { padding: 5px 11px; border-radius: var(--radius-sm); font-size: 12.5px; color: var(--text-2); cursor: pointer; border: 1px solid transparent; }
      .fbar .tab:hover { background: var(--surface-2); color: var(--text); }
      .fbar .tab.active { background: var(--surface-2); color: var(--text); border-color: var(--border); }
      .fbar .search { flex: 1; display: flex; align-items: center; gap: 7px; padding: 0 10px; color: var(--muted); }
      .fbar .search input { background: transparent; border: 0; outline: 0; flex: 1; font-size: 13px; color: var(--text); }
      .fbar .search input::placeholder { color: var(--muted); }

      /* ========= CONFIDENCE BAR ========= */
      .conf { display: inline-flex; align-items: center; gap: 8px; font-variant-numeric: tabular-nums; }
      .conf .track { width: 54px; height: 5px; border-radius: 99px; background: var(--surface-3); overflow: hidden; }
      .conf .fill { height: 100%; border-radius: 99px; }
      .conf.low    .fill { background: var(--err); }
      .conf.mid    .fill { background: var(--warn); }
      .conf.high   .fill { background: var(--ok); }
      .conf .val { font-size: 11.5px; color: var(--text-2); font-family: var(--font-mono); min-width: 28px; }

      /* ========= REVIEW LAYOUT ========= */
      .review-grid { display: grid; grid-template-columns: 340px 1fr 1fr; gap: 14px; height: calc(100vh - 180px); min-height: 560px; }
      .queue { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; display: flex; flex-direction: column; }
      .queue-head { padding: 12px 14px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
      .queue-list { overflow-y: auto; flex: 1; }
      .q-item { padding: 12px 14px; border-bottom: 1px solid var(--border); cursor: pointer; display: grid; grid-template-columns: auto 1fr auto; gap: 10px; align-items: center; }
      .q-item:hover { background: var(--surface-2); }
      .q-item.active { background: color-mix(in oklch, var(--accent) 10%, var(--surface)); border-left: 2px solid var(--accent); padding-left: 12px; }
      [data-theme="B"] .q-item.active { background: color-mix(in oklch, var(--accent) 8%, var(--surface)); }
      .q-item .vendor { font-weight: 500; font-size: 13px; }
      .q-item .meta { font-size: 11.5px; color: var(--muted); margin-top: 2px; }
      .q-item .amt { font-family: var(--font-mono); font-size: 12.5px; font-variant-numeric: tabular-nums; font-weight: 500; text-align: right; }

      .pdf-pane { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; display: flex; flex-direction: column; }
      .pdf-head { padding: 10px 14px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; font-size: 12.5px; color: var(--text-2); }
      .pdf-body { flex: 1; overflow-y: auto; background: var(--bg-2); padding: 24px; display: flex; justify-content: center; align-items: flex-start; }
      .pdf-page { background: #fefcf8; color: #1a1a1a; width: 100%; max-width: 420px; aspect-ratio: 1/1.414; padding: 30px 28px; font-family: 'IBM Plex Sans', sans-serif; font-size: 10.5px; line-height: 1.5; box-shadow: 0 12px 40px -10px rgba(0,0,0,0.25); border-radius: 3px; position: relative; }

      .form-pane { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; display: flex; flex-direction: column; }
      .form-head { padding: 12px 14px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; }
      .form-body { padding: 18px 18px 10px; overflow-y: auto; flex: 1; }
      .form-footer { padding: 12px 14px; border-top: 1px solid var(--border); display: flex; gap: 8px; justify-content: space-between; align-items: center; background: var(--surface-2); }

      .fld { display: flex; flex-direction: column; gap: 5px; margin-bottom: 12px; }
      .fld > label { font-size: 11.5px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.03em; font-weight: 500; }
      [data-theme="B"] .fld > label { text-transform: none; letter-spacing: 0; font-size: 12.5px; color: var(--text-2); }
      .fld > .input, .fld > input, .fld > select, .fld > textarea {
        background: var(--bg-2);
        border: 1px solid var(--border);
        border-radius: var(--radius-sm);
        padding: 8px 11px;
        color: var(--text);
        font-size: 13px;
        outline: none;
        transition: 0.1s;
      }
      [data-theme="B"] .fld > .input, [data-theme="B"] .fld > input, [data-theme="B"] .fld > select, [data-theme="B"] .fld > textarea { background: var(--surface-2); }
      .fld > input:focus, .fld > select:focus, .fld > textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--ring); }
      .fld.edited > input, .fld.edited > select { border-color: var(--warn); }
      .fld > .hint { font-size: 11px; color: var(--muted); display: flex; align-items: center; gap: 5px; margin-top: 1px; }
      .fld-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
      .fld-row-3 { display: grid; grid-template-columns: 2fr 1fr 1fr; gap: 12px; }

      /* ========= SECTION TITLES ========= */
      .sh { display: flex; align-items: baseline; justify-content: space-between; margin: 24px 0 10px; }
      .sh h2 { margin: 0; font-family: var(--font-display); font-size: 20px; font-weight: 500; letter-spacing: -0.01em; }
      [data-theme="A"] .sh h2 { font-family: var(--font-sans); font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--text-2); font-weight: 600; }
      .sh .side { font-size: 12px; color: var(--muted); }

      /* ========= MISC ========= */
      .kbd { display: inline-block; padding: 1px 5px; font-size: 10.5px; font-family: var(--font-mono); background: var(--surface-2); border: 1px solid var(--border); border-radius: 4px; color: var(--text-2); }
      .dotsep { color: var(--muted); margin: 0 6px; }
      .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
      .skeleton-pdf-line { height: 6px; border-radius: 2px; background: #e8e2d5; margin: 4px 0; }

      /* Review header bar */
      .rev-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 14px; }
      .rev-head .intro h1 { margin: 0; font-family: var(--font-display); font-size: 26px; font-weight: 500; letter-spacing: -0.015em; }
      [data-theme="A"] .rev-head .intro h1 { font-size: 20px; font-weight: 600; letter-spacing: 0; }
      .rev-head .intro p { margin: 4px 0 0; color: var(--text-2); font-size: 13px; }
      .rev-progress { font-family: var(--font-mono); font-size: 12px; color: var(--text-2); background: var(--surface); border: 1px solid var(--border); padding: 6px 10px; border-radius: 999px; }

      /* Toast */
      .toast-wrap { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); display: flex; flex-direction: column; gap: 8px; z-index: 1000; }
      .toast { display: flex; align-items: center; gap: 10px; padding: 10px 14px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: 0 12px 30px -10px rgba(0,0,0,0.4); font-size: 13px; min-width: 300px; }
      .toast.ok { border-color: color-mix(in oklch, var(--ok) 40%, var(--border)); }
      .toast .icon { color: var(--ok); }

      /* Split logo header strip (variant B flourish) */
      [data-theme="B"] .hero-strip { display: flex; align-items: center; gap: 16px; padding: 24px 0 18px; border-bottom: 1px solid var(--border); margin-bottom: 24px; }
      [data-theme="B"] .hero-strip h1 { margin: 0; font-family: var(--font-display); font-size: 44px; font-weight: 400; letter-spacing: -0.02em; line-height: 1; }
      [data-theme="B"] .hero-strip h1 em { font-style: italic; color: var(--accent); }
      [data-theme="B"] .hero-strip .sub { font-size: 13px; color: var(--text-2); max-width: 280px; }

      [data-theme="A"] .hero-strip { display: none; }

      /* Flow indicator */
      .flow { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--text-2); padding: 7px 10px; background: var(--surface); border: 1px solid var(--border); border-radius: 999px; }
      .flow .arr { color: var(--muted); }
      .flow .node.done { color: var(--text); }
      .flow .node.active { color: var(--accent); font-weight: 600; }

      /* Tweaks panel */
      .tweaks { position: fixed; bottom: 20px; right: 20px; width: 280px; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: 0 24px 60px -20px rgba(0,0,0,0.5); z-index: 500; overflow: hidden; font-family: var(--font-sans); }
      .tweaks-head { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid var(--border); background: var(--surface-2); }
      .tweaks-head h3 { margin: 0; font-size: 12px; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: var(--text-2); }
      .tweaks-body { padding: 12px 14px; display: flex; flex-direction: column; gap: 12px; }
      .tw-row { display: flex; flex-direction: column; gap: 6px; }
      .tw-row > label { font-size: 11.5px; color: var(--muted); }
      .tw-opts { display: flex; gap: 4px; padding: 2px; background: var(--bg-2); border: 1px solid var(--border); border-radius: var(--radius-sm); }
      .tw-opts .opt { flex: 1; text-align: center; padding: 5px 7px; font-size: 12px; border-radius: 4px; cursor: pointer; color: var(--text-2); }
      .tw-opts .opt.active { background: var(--surface); color: var(--text); box-shadow: 0 1px 0 var(--border); }
      [data-theme="A"] .tw-opts .opt.active { background: var(--accent); color: var(--accent-ink); box-shadow: none; }

      /* ========= LOG / COMMAND CENTER ========= */
      .log-split { display: grid; grid-template-columns: 360px 1fr; gap: 14px; margin-top: 14px; align-items: start; }
      .log-list { display: flex; flex-direction: column; max-height: calc(100vh - 320px); min-height: 500px; }
      .log-list-head { padding: 12px 16px; border-bottom: 1px solid var(--border); display: flex; align-items: center; justify-content: space-between; background: var(--surface-2); }
      .log-list-scroll { flex: 1; overflow-y: auto; }
      .log-run { display: flex; align-items: center; gap: 10px; padding: 11px 14px 11px 10px; border-bottom: 1px solid var(--border); cursor: pointer; transition: background 0.1s; }
      .log-run:hover { background: var(--surface-2); }
      .log-run.active { background: color-mix(in oklch, var(--accent) 10%, var(--surface)); border-left: 3px solid var(--accent); padding-left: 7px; }
      .log-run-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--ok); flex-shrink: 0; }
      .log-run.err .log-run-dot { background: var(--err); }
      .log-run.muted .log-run-dot { background: var(--border-strong); }
      .log-run-time { font-size: 12px; color: var(--text); font-weight: 500; }
      .log-run-summary { font-size: 11.5px; color: var(--muted); margin-top: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .log-run-dur { font-size: 11px; color: var(--muted); flex-shrink: 0; }

      .log-detail { display: block; }

      .pipe-timeline { display: flex; flex-direction: column; gap: 14px; }
      .pipe-row { display: grid; grid-template-columns: 180px 1fr; gap: 16px; align-items: center; }
      .pipe-step { display: flex; gap: 10px; align-items: center; }
      .pipe-icon { width: 28px; height: 28px; border-radius: 6px; display: grid; place-items: center; flex-shrink: 0; background: color-mix(in oklch, var(--accent) 12%, var(--surface-2)); color: var(--accent); }
      .pipe-icon.idle { background: var(--surface-2); color: var(--muted); }
      .pipe-icon.error { background: color-mix(in oklch, var(--err) 16%, var(--surface-2)); color: var(--err); }
      .pipe-bar-wrap { display: flex; align-items: center; gap: 10px; }
      .pipe-bar { height: 8px; border-radius: 4px; background: var(--accent); min-width: 6px; transition: width 0.3s ease; }
      .pipe-bar.idle { background: var(--border-strong); opacity: 0.5; }
      .pipe-bar.error { background: var(--err); }
      .pipe-dur { font-size: 11.5px; color: var(--muted); min-width: 44px; text-align: right; }

      @media (max-width: 900px) {
        .log-split { grid-template-columns: 1fr; }
        .log-list { max-height: 300px; min-height: 200px; }
      }
    `}</style>
  );
}

// ============ Small components ============

function VendorLogo({ vendor, size = 22 }) {
  const h = vendor?.hue ?? 220;
  return (
    <div className="vlogo" style={{ width: size, height: size, background: `oklch(55% 0.12 ${h})`, fontSize: size < 22 ? 9 : 10 }}>
      {vendor?.logo || '??'}
    </div>
  );
}

function Pill({ kind = 'muted', children, dot }) {
  return <span className={`pill ${kind}`}>{dot && <span className="dot"/>}{children}</span>;
}

function StatusPill({ status, t }) {
  const map = {
    pending:     { kind: 'warn',   label: t.status.pending },
    saved:       { kind: 'accent', label: t.status.saved },
    transferred: { kind: 'ok',     label: t.status.transferred },
    error:       { kind: 'err',    label: t.status.error },
    skipped:     { kind: 'muted',  label: t.status.skipped },
  };
  const m = map[status] || map.skipped;
  return <Pill kind={m.kind} dot>{m.label}</Pill>;
}

// ======== SPLIT STATUS: file + Bezala are two separate concerns ========
// Derives two statuses from the single `status` field on a message.
//   file_status:   saved | error | skipped
//   bezala_status: transferred | pending | error | na
function deriveStatuses(m) {
  switch (m.status) {
    case 'transferred':
    case 'saved':
      return { file: 'saved', bezala: 'transferred' };
    case 'pending':
      return { file: 'saved', bezala: 'pending' };
    case 'error':
      return { file: 'error', bezala: 'na' };
    case 'skipped':
      return { file: 'skipped', bezala: 'na' };
    default:
      return { file: 'skipped', bezala: 'na' };
  }
}

function FileBadge({ status, t }) {
  const map = {
    saved:   { kind: 'ok',    label: t.fileStatus.saved },
    error:   { kind: 'err',   label: t.fileStatus.error },
    skipped: { kind: 'muted', label: t.fileStatus.skipped },
  };
  const m = map[status];
  if (!m) return null;
  return <Pill kind={m.kind} dot>{m.label}</Pill>;
}

function BezalaBadge({ status, t }) {
  // 'na' → render nothing (per spec: if not relevant, don't include)
  if (!status || status === 'na') return null;
  const map = {
    transferred: { kind: 'ok',   label: t.bezalaStatus.transferred },
    pending:     { kind: 'warn', label: t.bezalaStatus.pending },
    error:       { kind: 'err',  label: t.bezalaStatus.error },
  };
  const m = map[status];
  if (!m) return null;
  return <Pill kind={m.kind} dot>{m.label}</Pill>;
}

// Compact two-line status cell for tables.
function StatusCell({ m, t }) {
  const s = deriveStatuses(m);
  const bez = <BezalaBadge status={s.bezala} t={t} />;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
      <FileBadge status={s.file} t={t} />
      {bez || <span style={{ fontSize: 11, color: 'var(--muted)', paddingLeft: 2 }}>—</span>}
    </div>
  );
}

function Confidence({ value }) {
  const lvl = value >= 85 ? 'high' : value >= 70 ? 'mid' : 'low';
  return (
    <span className={`conf ${lvl}`}>
      <span className="track"><span className="fill" style={{ width: `${value}%` }}/></span>
      <span className="val">{value}%</span>
    </span>
  );
}

function Toast({ message, onDone }) {
  useEffect(() => {
    if (!message) return;
    const id = setTimeout(onDone, 2200);
    return () => clearTimeout(id);
  }, [message, onDone]);
  if (!message) return null;
  return (
    <div className="toast-wrap">
      <div className="toast ok">
        <span className="icon"><I.Check size={16} /></span>
        <span>{message}</span>
      </div>
    </div>
  );
}

Object.assign(window, { GlobalStyles, VendorLogo, Pill, StatusPill, FileBadge, BezalaBadge, StatusCell, deriveStatuses, Confidence, Toast });
