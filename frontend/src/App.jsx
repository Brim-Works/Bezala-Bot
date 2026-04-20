import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';
import Dashboard from './Dashboard.jsx';
import Settings from './Settings.jsx';

function usePath() {
  const [path, setPath] = useState(() =>
    typeof window === 'undefined' ? '/' : window.location.pathname
  );

  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = useCallback((to) => {
    if (to === window.location.pathname) return;
    window.history.pushState({}, '', to);
    setPath(to);
  }, []);

  return [path, navigate];
}

export default function App() {
  const [authChecked, setAuthChecked] = useState(false);
  const [path, navigate] = usePath();

  useEffect(() => {
    let cancelled = false;
    api.me()
      .then(() => {
        if (!cancelled) setAuthChecked(true);
      })
      .catch(() => {
        // api.js har redan redirectat till /login vid 401
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!authChecked) {
    return (
      <div style={{ padding: '2rem', color: 'var(--muted, #888)' }}>Laddar…</div>
    );
  }

  if (path === '/settings') {
    return <Settings navigate={navigate} />;
  }
  return <Dashboard navigate={navigate} />;
}
