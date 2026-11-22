import { useState } from 'react';
import MatchHealth from './MatchHealth.jsx';
import Log from './Log.jsx';

/* C47 Del 6 — "Status" (felsökning) = Match Health + Logg på en sida. */
export default function Status() {
  const [tab, setTab] = useState(() =>
    new URLSearchParams(window.location.search).get('tab') === 'match-health' ? 'health' : 'log');
  return (
    <div className="status-page">
      <div className="tt-hub__modes">
        <button type="button" className={`btn ${tab === 'log' ? 'primary' : ''}`} onClick={() => setTab('log')}>
          Logg
        </button>
        <button type="button" className={`btn ${tab === 'health' ? 'primary' : ''}`} onClick={() => setTab('health')}>
          Match Health
        </button>
      </div>
      {tab === 'log' ? <Log /> : <MatchHealth />}
    </div>
  );
}
