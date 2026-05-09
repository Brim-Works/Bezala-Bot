import { useEffect, useState } from 'react';
import { I18nProvider, useI18n } from './i18n/useI18n.jsx';
import { ThemeProvider } from './theme/ThemeProvider.jsx';
import { RouterProvider, useRouter } from './router/useRouter.jsx';
import { ToastProvider } from './lib/toast.jsx';
import { DrawerProvider } from './drawer/DrawerProvider.jsx';
import PipelineDrawer from './drawer/PipelineDrawer.jsx';
import AppShell from './components/AppShell.jsx';
import ViewErrorBoundary from './components/ViewErrorBoundary.jsx';
import Dashboard from './views/Dashboard.jsx';
import Review from './views/Review.jsx';
import Match from './views/Match.jsx';
import TravelTinder from './views/TravelTinder.jsx';
import Log from './views/Log.jsx';
import Settings from './views/Settings.jsx';
import Trash from './views/Trash.jsx';
import NotFound from './views/NotFound.jsx';
import { TrashCountProvider } from './hooks/TrashCountProvider.jsx';
import { api, ApiError, setUnauthorizedHandler } from './api/client.js';
import { viewForPath } from './routes.js';

function ViewForRoute() {
  const { path } = useRouter();
  const view = viewForPath(path);
  switch (view) {
    case 'dashboard':
      return (
        <ViewErrorBoundary viewKey="dashboard">
          <Dashboard />
        </ViewErrorBoundary>
      );
    case 'review':
      return (
        <ViewErrorBoundary viewKey="review">
          <Review />
        </ViewErrorBoundary>
      );
    case 'match':
      return (
        <ViewErrorBoundary viewKey="match">
          <Match />
        </ViewErrorBoundary>
      );
    case 'travelTinder':
      return (
        <ViewErrorBoundary viewKey="travelTinder">
          <TravelTinder />
        </ViewErrorBoundary>
      );
    case 'log':
      return (
        <ViewErrorBoundary viewKey="log">
          <Log />
        </ViewErrorBoundary>
      );
    case 'settings':
      return (
        <ViewErrorBoundary viewKey="settings">
          <Settings />
        </ViewErrorBoundary>
      );
    case 'trash':
      return (
        <ViewErrorBoundary viewKey="trash">
          <Trash />
        </ViewErrorBoundary>
      );
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
  const [status, setStatus] = useState('checking');

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
      <PipelineDrawer />
    </AppShell>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <I18nProvider>
        <RouterProvider>
          <DrawerProvider>
            <ToastProvider>
              <TrashCountProvider>
                <AuthedApp />
              </TrashCountProvider>
            </ToastProvider>
          </DrawerProvider>
        </RouterProvider>
      </I18nProvider>
    </ThemeProvider>
  );
}
