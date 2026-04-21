import { useI18n } from '../i18n/useI18n.jsx';
import { useRouter } from '../router/useRouter.jsx';
import { NAV_ICONS } from '../icons/index.jsx';
import { routeForView, viewForPath } from '../routes.js';
import PipelineNav from './PipelineNav.jsx';
import { useTrashCountContext } from '../hooks/TrashCountProvider.jsx';

const ITEMS = [
  { id: 'dashboard' },
  { id: 'review' },
  { id: 'log' },
  { id: 'settings' },
  { id: 'trash' },
];

function formatBadge(count) {
  if (!count) return null;
  if (count > 99) return '99+';
  return String(count);
}

export default function Sidebar() {
  const { t } = useI18n();
  const { path, navigate } = useRouter();
  const { count: trashCount } = useTrashCountContext();
  const activeView = viewForPath(path);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">B</div>
        <div>
          <div className="brand-name">{t.app}</div>
          <div className="brand-tag">{t.tagline}</div>
        </div>
      </div>
      <nav className="sidebar__nav">
        {ITEMS.map((item) => {
          const Icon = NAV_ICONS[item.id];
          const isActive = activeView === item.id;
          const badge = item.id === 'trash' ? formatBadge(trashCount) : null;
          return (
            <button
              key={item.id}
              type="button"
              className={`nav-item ${isActive ? 'active' : ''}`}
              aria-current={isActive ? 'page' : undefined}
              onClick={() => navigate(routeForView(item.id))}
              data-testid={`nav-${item.id}`}
            >
              <Icon className="icon sm" />
              <span>{t.nav[item.id]}</span>
              {badge ? (
                <span className="nav-count mono" data-testid="trash-count-badge">
                  {badge}
                </span>
              ) : null}
            </button>
          );
        })}
      </nav>
      <div className="sidebar__pipeline">
        <div className="sidebar__pipeline-label">{t.pipelineNav.label}</div>
        <PipelineNav variant="sidebar" />
      </div>
    </aside>
  );
}
