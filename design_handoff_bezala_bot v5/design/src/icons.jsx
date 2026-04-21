// Minimal line icons, 20x20 viewBox, currentColor stroke.
const Icon = ({ d, size = 18, stroke = 1.75, fill = 'none', children }) => (
  <svg width={size} height={size} viewBox="0 0 20 20" fill={fill} stroke="currentColor" strokeWidth={stroke} strokeLinecap="round" strokeLinejoin="round">
    {d ? <path d={d} /> : children}
  </svg>
);

const I = {
  Dashboard: (p) => <Icon {...p}><rect x="2.5" y="2.5" width="6" height="7" rx="1"/><rect x="11.5" y="2.5" width="6" height="4" rx="1"/><rect x="11.5" y="9" width="6" height="8.5" rx="1"/><rect x="2.5" y="11.5" width="6" height="6" rx="1"/></Icon>,
  Review: (p) => <Icon {...p}><path d="M3.5 4.5 h13 M3.5 10 h13 M3.5 15.5 h8"/><circle cx="16.5" cy="15.5" r="2"/></Icon>,
  Log: (p) => <Icon {...p}><path d="M4 3.5 h12 v13 H4z"/><path d="M7 7 h6 M7 10 h6 M7 13 h4"/></Icon>,
  Settings: (p) => <Icon {...p}><circle cx="10" cy="10" r="2.5"/><path d="M10 1.5 v2 M10 16.5 v2 M1.5 10 h2 M16.5 10 h2 M3.9 3.9 l1.4 1.4 M14.7 14.7 l1.4 1.4 M3.9 16.1 l1.4-1.4 M14.7 5.3 l1.4-1.4"/></Icon>,
  Refresh: (p) => <Icon {...p}><path d="M16.5 4.5 v4 h-4"/><path d="M16 8.5 A 6.5 6.5 0 1 0 17 13"/></Icon>,
  Check: (p) => <Icon {...p}><path d="M4 10.5 l3.5 3.5 L16 5.5"/></Icon>,
  X: (p) => <Icon {...p}><path d="M5 5 l10 10 M15 5 l-10 10"/></Icon>,
  Arrow: (p) => <Icon {...p}><path d="M4 10 h12 M11 5 l5 5 -5 5"/></Icon>,
  ArrowL: (p) => <Icon {...p}><path d="M16 10 h-12 M9 5 l-5 5 5 5"/></Icon>,
  Download: (p) => <Icon {...p}><path d="M10 3 v10 M5.5 8.5 L10 13 14.5 8.5 M4 16.5 h12"/></Icon>,
  Mail: (p) => <Icon {...p}><rect x="2.5" y="4.5" width="15" height="11" rx="1.5"/><path d="M3 5.5 L10 11 17 5.5"/></Icon>,
  File: (p) => <Icon {...p}><path d="M5 2.5 h7 l4 4 v11 H5z M12 2.5 v4 h4"/></Icon>,
  ArrowUp: (p) => <Icon {...p}><path d="M10 16 V4 M5 9 l5-5 5 5"/></Icon>,
  Search: (p) => <Icon {...p}><circle cx="9" cy="9" r="5"/><path d="M13 13 l4 4"/></Icon>,
  Filter: (p) => <Icon {...p}><path d="M2.5 4.5 h15 l-5.5 7 v5 l-4 -2 v-3 z"/></Icon>,
  Dot: (p) => <Icon {...p}><circle cx="10" cy="10" r="3" fill="currentColor"/></Icon>,
  Plus: (p) => <Icon {...p}><path d="M10 4 v12 M4 10 h12"/></Icon>,
  Sliders: (p) => <Icon {...p}><path d="M4 6 h12 M4 14 h12"/><circle cx="8" cy="6" r="1.6" fill="var(--surface)"/><circle cx="13" cy="14" r="1.6" fill="var(--surface)"/></Icon>,
  Bezala: (p) => <Icon {...p}><path d="M3 3.5 h9 a3 3 0 0 1 0 6 h-6 a3 3 0 0 0 0 6 h10"/></Icon>,
  Drive: (p) => <Icon {...p}><path d="M7 3.5 h6 l5 8.5 -3 5.5 h-10 l-3-5.5 z M7 3.5 l3 8.5 M13 3.5 l-3 8.5 M5 17.5 l3-5.5 h10"/></Icon>,
  Sparkle: (p) => <Icon {...p}><path d="M10 3 l1.5 4 4 1.5 -4 1.5 L10 14 8.5 10 4.5 8.5 8.5 7z"/></Icon>,
  ExternalLink: (p) => <Icon {...p}><path d="M11 3.5 h5.5 v5.5 M16 4 l-7 7 M14.5 11 v5 h-11 v-11 h5"/></Icon>,
  Clock: (p) => <Icon {...p}><circle cx="10" cy="10" r="7"/><path d="M10 6 v4.5 l3 2"/></Icon>,
  Alert: (p) => <Icon {...p}><path d="M10 3 L17.5 16 h-15z"/><path d="M10 8 v4 M10 14.2 v.1"/></Icon>,
};

window.I = I;
