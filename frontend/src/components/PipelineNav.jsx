import {
  IconMail,
  IconSparkle,
  IconDrive,
  IconBezala,
} from '../icons/index.jsx';
import { useI18n } from '../i18n/useI18n.jsx';
import { useDrawer, DRAWER_TABS } from '../drawer/DrawerProvider.jsx';

const ICONS = {
  gmail: IconMail,
  ai: IconSparkle,
  drive: IconDrive,
  bezala: IconBezala,
};

/* Kompakt Gmail → AI → Drive → Bezala-rad. Återanvänds i Sidebar och
 * TopBar. När en rad är vald är ikonerna klickbara — annars nedtonade. */
export default function PipelineNav({ variant = 'sidebar' }) {
  const { t } = useI18n();
  const { selectedMessage, openDrawer } = useDrawer();
  const hasSelection = Boolean(selectedMessage);

  return (
    <div
      className={`pipeline-nav pipeline-nav--${variant} ${
        hasSelection ? '' : 'pipeline-nav--dim'
      }`}
      role="group"
      aria-label={t.pipelineNav.label}
      title={hasSelection ? t.pipelineNav.tipActive : t.pipelineNav.tipIdle}
      data-testid={`pipeline-nav-${variant}`}
    >
      {DRAWER_TABS.map((step, idx) => {
        const Icon = ICONS[step];
        return (
          <span key={step} className="pipeline-nav__group">
            <button
              type="button"
              className="pipeline-nav__step"
              disabled={!hasSelection}
              onClick={() => openDrawer(selectedMessage, step)}
              aria-label={t.drawer.tabs[step]}
              data-testid={`pipeline-nav-${variant}-${step}`}
            >
              <Icon className="icon sm" />
              {variant === 'topbar' ? (
                <span className="pipeline-nav__label">{t.drawer.tabs[step]}</span>
              ) : null}
            </button>
            {idx < DRAWER_TABS.length - 1 ? (
              <span className="pipeline-nav__arrow" aria-hidden="true">→</span>
            ) : null}
          </span>
        );
      })}
    </div>
  );
}
