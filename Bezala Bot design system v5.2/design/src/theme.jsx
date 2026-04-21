// Two theme variants. Applied via CSS variables on root.
const THEMES = {
  // Variant A — Refined: Bezala-inspired light, modern
  // Echoes Bezala (green header, light canvas, 3-col) but modernized with
  // deeper greens, tighter type, softer borders, no harsh green bar.
  A: {
    name: 'Bezala Modern Light',
    vars: {
      '--bg': '#f7f7f4',
      '--bg-2': '#efefe9',
      '--surface': '#ffffff',
      '--surface-2': '#f3f3ee',
      '--surface-3': '#e7e7df',
      '--border': '#e4e3db',
      '--border-strong': '#cfcec2',
      '--text': '#111412',
      '--text-2': '#4b524c',
      '--muted': '#8a8f88',
      '--accent': 'oklch(48% 0.09 165)',
      '--accent-2': 'oklch(48% 0.09 40)',
      '--accent-ink': '#ffffff',
      '--ok': 'oklch(52% 0.10 160)',
      '--warn': 'oklch(60% 0.14 65)',
      '--err': 'oklch(52% 0.16 25)',
      '--ring': 'color-mix(in oklch, var(--accent) 28%, transparent)',
      '--radius': '8px',
      '--radius-sm': '5px',
      '--font-sans': "'IBM Plex Sans', system-ui, sans-serif",
      '--font-mono': "'IBM Plex Mono', ui-monospace, monospace",
      '--font-display': "'IBM Plex Sans', system-ui, sans-serif",
      '--shadow': '0 1px 0 rgba(0,0,0,0.02), 0 1px 3px rgba(20,40,30,0.04)',
    },
  },
  // Variant B — Evolved: deeper, richer take. Dark emerald + cream
  B: {
    name: 'Forest & Cream',
    vars: {
      '--bg': '#12221c',
      '--bg-2': '#0e1b17',
      '--surface': '#1a2d26',
      '--surface-2': '#203830',
      '--surface-3': '#2a483d',
      '--border': '#264037',
      '--border-strong': '#35574a',
      '--text': '#f1ead8',
      '--text-2': '#b5b09c',
      '--muted': '#7d8078',
      '--accent': 'oklch(80% 0.13 90)',
      '--accent-2': 'oklch(70% 0.12 25)',
      '--accent-ink': '#12221c',
      '--ok': 'oklch(78% 0.13 160)',
      '--warn': 'oklch(82% 0.14 80)',
      '--err': 'oklch(70% 0.16 25)',
      '--ring': 'color-mix(in oklch, var(--accent) 30%, transparent)',
      '--radius': '10px',
      '--radius-sm': '6px',
      '--font-sans': "'IBM Plex Sans', system-ui, sans-serif",
      '--font-mono': "'IBM Plex Mono', ui-monospace, monospace",
      '--font-display': "'Instrument Serif', Georgia, serif",
      '--shadow': '0 1px 0 rgba(255,255,255,0.02) inset, 0 10px 30px -12px rgba(0,0,0,0.4)',
    },
  },
};

function applyTheme(key, density) {
  const t = THEMES[key] || THEMES.A;
  const root = document.documentElement;
  Object.entries(t.vars).forEach(([k, v]) => root.style.setProperty(k, v));
  root.style.setProperty('--pad', density === 'compact' ? '0.5rem' : '0.75rem');
  root.style.setProperty('--pad-2', density === 'compact' ? '0.75rem' : '1.125rem');
  root.style.setProperty('--row-h', density === 'compact' ? '36px' : '44px');
  root.setAttribute('data-theme', key);
  root.setAttribute('data-density', density || 'cozy');
  document.body.style.background = t.vars['--bg'];
  document.body.style.color = t.vars['--text'];
  document.body.style.fontFamily = t.vars['--font-sans'];
}

window.THEMES = THEMES;
window.applyTheme = applyTheme;
