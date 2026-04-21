import { useI18n } from '../i18n/useI18n.jsx';
import { useRouter } from '../router/useRouter.jsx';
import { NAV_ICONS } from '../icons/index.jsx';
import { routeForView, viewForPath } from '../routes.js';

const ITEMS = [
  { id: 'dashboard' },
  { id: 'review' },
  { id: 'log' },
  { id: 'settings' },
];

export default function Sidebar() {
  const { t } = useI18n();
  const { path, navigate } = useRouter();
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
      <nav>
        {ITEMS.map((item) => {
          const Icon = NAV_ICONS[item.id];
          const isActive = activeView === item.id;
          return (
            <button
              key={item.id}
              type="button"
              className={`nav-item ${isActive ? 'active' : ''}`}
              aria-current={isActive ? 'page' : undefined}
              onClick={() => navigate(routeForView(item.id))}
            >
              <Icon className="icon sm" />
              <span>{t.nav[item.id]}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
