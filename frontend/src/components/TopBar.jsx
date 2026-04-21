import { useI18n } from '../i18n/useI18n.jsx';
import { useTheme } from '../theme/ThemeProvider.jsx';
import { useRouter } from '../router/useRouter.jsx';
import { viewForPath } from '../routes.js';
import { api } from '../api/client.js';
import { IconLogout } from '../icons/index.jsx';

export default function TopBar() {
  const { lang, setLang, t } = useI18n();
  const { variant, setVariant } = useTheme();
  const { path } = useRouter();
  const view = viewForPath(path);
  const title = view ? t.views[view].title : '';

  return (
    <header className="topbar">
      <div className="title">{title}</div>
      <div className="spacer" />

      <div
        className="toggle-group"
        role="radiogroup"
        aria-label={t.topbar.theme}
      >
        <button
          type="button"
          role="radio"
          aria-checked={variant === 'A'}
          className={`toggle-opt ${variant === 'A' ? 'active' : ''}`}
          onClick={() => setVariant('A')}
        >
          <span className="theme-dot theme-dot--a" aria-hidden="true" />
          {t.topbar.themeLight}
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={variant === 'B'}
          className={`toggle-opt ${variant === 'B' ? 'active' : ''}`}
          onClick={() => setVariant('B')}
        >
          <span className="theme-dot theme-dot--b" aria-hidden="true" />
          {t.topbar.themeForest}
        </button>
      </div>

      <div
        className="toggle-group"
        role="radiogroup"
        aria-label={t.topbar.language}
      >
        <button
          type="button"
          role="radio"
          aria-checked={lang === 'sv'}
          className={`toggle-opt ${lang === 'sv' ? 'active' : ''}`}
          onClick={() => setLang('sv')}
        >
          SV
        </button>
        <button
          type="button"
          role="radio"
          aria-checked={lang === 'en'}
          className={`toggle-opt ${lang === 'en' ? 'active' : ''}`}
          onClick={() => setLang('en')}
        >
          EN
        </button>
      </div>

      <button
        type="button"
        className="btn ghost"
        onClick={() => api.logout()}
        title={t.topbar.logout}
      >
        <IconLogout className="icon sm" />
        <span>{t.topbar.logout}</span>
      </button>
    </header>
  );
}
