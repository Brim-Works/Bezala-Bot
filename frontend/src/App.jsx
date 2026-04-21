import { useEffect, useState } from 'react';
import { I18nProvider, useI18n } from './i18n/useI18n.jsx';
import { ThemeProvider } from './theme/ThemeProvider.jsx';
import { RouterProvider, useRouter } from './router/useRouter.jsx';
import AppShell from './components/AppShell.jsx';
import Dashboard from './views/Dashboard.jsx';
import Review from './views/Review.jsx';
import LogPlaceholder from './views/LogPlaceholder.jsx';
import SettingsPlaceholder from './views/SettingsPlaceholder.jsx';
import NotFound from './views/NotFound.jsx';
import { api, ApiError, setUnauthorizedHandler } from './api/client.js';
import { viewForPath } from './routes.js';

function ViewForRoute() {
  const { path } = useRouter();
  const view = viewForPath(path);
  switch (view) {
    case 'dashboard':
      return <Dashboard />;
    case 'review':
      return <Review />;
    case 'log':
      return <LogPlaceholder />;
    case 'settings':
      return <SettingsPlaceholder />;
    default:
      return <NotFound />;
  }
}

function LoadingScreen() {
  const { t } = useI18n();
  return (
    <div className="login-wrap">
      <p style={{ color: 'var(--muted)' }}>{t.common.loading}</p>
    </div>
  );
}

function AuthedApp() {
  const [status, setStatus] = useState('checking'); // checking | ready

  useEffect(() => {
    let cancelled = false;
    setUnauthorizedHandler(() => {
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    });
    api
      .me()
      .then(() => {
        if (!cancelled) setStatus('ready');
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          // client.js har redan redirectat
          return;
        }
        if (!cancelled) setStatus('ready');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (status !== 'ready') {
    return <LoadingScreen />;
  }

  return (
    <AppShell>
      <ViewForRoute />
    </AppShell>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <I18nProvider>
        <RouterProvider>
          <AuthedApp />
        </RouterProvider>
      </I18nProvider>
    </ThemeProvider>
  );
}
