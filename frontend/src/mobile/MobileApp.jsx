import { useEffect, useState } from 'react';
import { api } from '../api/client.js';
import { useRouter } from '../router/useRouter.jsx';
import UploadView from './UploadView.jsx';
import DecisionTabs from './DecisionTabs.jsx';
import './mobile.css';

/* C47 — mobilvyn. /m = Fota kvitto, /m/swipe = Travel Tinder (tre flikar).
 * Samma inloggning som desktop-appen. */
export default function MobileApp() {
  const { path, navigate } = useRouter();
  const onSwipe = path.startsWith('/m/swipe');
  const [inbox, setInbox] = useState(null);

  useEffect(() => {
    let alive = true;
    api.receiptInbox().then((r) => { if (alive) setInbox(r?.address || null); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    document.title = onSwipe ? 'Travel Tinder' : 'Kvitton';
    document.body.classList.add('m-body');
    return () => document.body.classList.remove('m-body');
  }, [onSwipe]);

  return (
    <div className="m-app">
      <header className="m-header">
        <nav className="m-nav">
          <button type="button" className={`m-navbtn ${!onSwipe ? 'is-active' : ''}`}
            onClick={() => navigate('/m')}>Fota</button>
          <button type="button" className={`m-navbtn ${onSwipe ? 'is-active' : ''}`}
            onClick={() => navigate('/m/swipe')}>Travel Tinder</button>
        </nav>
        <a className="m-desktop-link" href="/">Desktop</a>
      </header>
      <main className="m-main">{onSwipe ? <DecisionTabs /> : <UploadView />}</main>
      {inbox && (
        <footer className="m-footer" data-testid="m-inbox-footer">
          Mail a receipt to: <a href={`mailto:${inbox}`}>{inbox}</a>
        </footer>
      )}
    </div>
  );
}
