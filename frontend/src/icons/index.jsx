/* Linjeikoner — 1.75px stroke, currentColor, 20×20 viewBox.
 * Klassen .icon i base.css sätter stroke-width/fill/linecap/linejoin.
 * Alla ikoner accepterar { className, title } för tillgänglighet. */

function IconBase({ children, title, className = 'icon' }) {
  return (
    <svg
      viewBox="0 0 20 20"
      className={className}
      role={title ? 'img' : 'presentation'}
      aria-label={title || undefined}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

export function IconDashboard(props) {
  return (
    <IconBase {...props}>
      <rect x="3" y="3" width="6.5" height="6.5" rx="1.2" />
      <rect x="10.5" y="3" width="6.5" height="6.5" rx="1.2" />
      <rect x="3" y="10.5" width="6.5" height="6.5" rx="1.2" />
      <rect x="10.5" y="10.5" width="6.5" height="6.5" rx="1.2" />
    </IconBase>
  );
}

export function IconReview(props) {
  return (
    <IconBase {...props}>
      <path d="M4 3h9l3 3v11a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z" />
      <path d="M13 3v3h3" />
      <path d="M6.5 10.5l1.5 1.5 3.5-4" />
    </IconBase>
  );
}

export function IconLog(props) {
  return (
    <IconBase {...props}>
      <path d="M3 5h14" />
      <path d="M3 10h14" />
      <path d="M3 15h9" />
    </IconBase>
  );
}

export function IconSettings(props) {
  return (
    <IconBase {...props}>
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2v2.2M10 15.8V18M2 10h2.2M15.8 10H18M4.3 4.3l1.6 1.6M14.1 14.1l1.6 1.6M4.3 15.7l1.6-1.6M14.1 5.9l1.6-1.6" />
    </IconBase>
  );
}

export function IconMail(props) {
  return (
    <IconBase {...props}>
      <rect x="2.5" y="4.5" width="15" height="11" rx="1.5" />
      <path d="M3 5.5l7 5 7-5" />
    </IconBase>
  );
}

export function IconSparkle(props) {
  return (
    <IconBase {...props}>
      <path d="M10 2.5l1.8 4.7L16.5 9l-4.7 1.8L10 15.5l-1.8-4.7L3.5 9l4.7-1.8z" />
      <path d="M15.5 13.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z" />
    </IconBase>
  );
}

export function IconDrive(props) {
  return (
    <IconBase {...props}>
      <path d="M7.5 3h5l5 8.5-2.5 4.5H4.5L2 11.5z" />
      <path d="M7.5 3L2 11.5M12.5 3l5 8.5M4.5 16L7 11.5h11" />
    </IconBase>
  );
}

export function IconBezala(props) {
  return (
    <IconBase {...props}>
      <rect x="3" y="3" width="14" height="14" rx="2" />
      <path d="M6.5 10l2 2 5-5" />
    </IconBase>
  );
}

export function IconLogout(props) {
  return (
    <IconBase {...props}>
      <path d="M8 4H4a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h4" />
      <path d="M13 6l4 4-4 4" />
      <path d="M17 10H8" />
    </IconBase>
  );
}

export function IconTrash(props) {
  return (
    <IconBase {...props}>
      <path d="M4 5h12" />
      <path d="M8 5V3h4v2" />
      <path d="M5 5l1 12a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1l1-12" />
      <path d="M9 9v6M11 9v6" />
    </IconBase>
  );
}

export function IconDownload(props) {
  return (
    <IconBase {...props}>
      <path d="M10 3v10" />
      <path d="M6 9l4 4 4-4" />
      <path d="M3.5 15v1.5A1.5 1.5 0 0 0 5 18h10a1.5 1.5 0 0 0 1.5-1.5V15" />
    </IconBase>
  );
}

export function IconRestore(props) {
  return (
    <IconBase {...props}>
      <path d="M4 4v5h5" />
      <path d="M4.5 9A7 7 0 1 1 3 13.5" />
    </IconBase>
  );
}

export function IconRefresh(props) {
  return (
    <IconBase {...props}>
      <path d="M16 4v4h-4" />
      <path d="M4 16v-4h4" />
      <path d="M15.5 8A6 6 0 0 0 5 9.5" />
      <path d="M4.5 12A6 6 0 0 0 15 10.5" />
    </IconBase>
  );
}

export const NAV_ICONS = {
  dashboard: IconDashboard,
  review: IconReview,
  log: IconLog,
  settings: IconSettings,
  trash: IconTrash,
};
