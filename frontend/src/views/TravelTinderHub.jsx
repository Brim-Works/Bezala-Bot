import { useState } from 'react';
import DecisionTabs from '../mobile/DecisionTabs.jsx';
import ClassicTravelTinder from './TravelTinder.jsx';
import Review from './Review.jsx';
import Match from './Match.jsx';
import '../mobile/mobile.css';

/* C47 Del 6 — EN sida "Travel Tinder" som ersätter Granska, Kortmatchning
 * och gamla Travel Tinder. Samma tre flikar som mobilen (Att besluta /
 * Matchade / Väntar). Allt de gamla vyerna kunde finns kvar under
 * "Att besluta":
 *   Svep-kö         — nya kön (kvitto↔kortrad, resor, prenumerationer …)
 *   Manuell koppling — gamla Travel Tinder/Kortmatchning (välj kortrad +
 *                      kvitto, ladda upp, privat utlägg, matchade par)
 *   Granska          — gamla Granska (redigera fält + ladda upp till Bezala)
 *   Kortmatchning    — gamla listan kortrad → förslag (Koppla ihop)
 */

const MODES = [
  { key: 'queue', label: 'Svep-kö' },
  { key: 'manual', label: 'Manuell koppling' },
  { key: 'review', label: 'Granska (Bezala-uppladdning)' },
  { key: 'match', label: 'Kortmatchning (lista)' },
];

export default function TravelTinderHub() {
  const [tab, setTab] = useState('decide');
  const [mode, setMode] = useState(() => {
    const m = new URLSearchParams(window.location.search).get('mode');
    return MODES.some((x) => x.key === m) ? m : 'queue';
  });
  return (
    <div className="tt-hub">
      <div className="m-tabs" role="tablist">
        {[['decide', 'Att besluta'], ['matched', 'Matchade'], ['waiting', 'Väntar']].map(([k, l]) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k}
            className={`m-tab ${tab === k ? 'is-active' : ''}`} onClick={() => setTab(k)}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'decide' && (
        <>
          <div className="tt-hub__modes">
            {MODES.map((m) => (
              <button key={m.key} type="button"
                className={`btn ${mode === m.key ? 'primary' : ''}`} onClick={() => setMode(m.key)}>
                {m.label}
              </button>
            ))}
          </div>
          {mode === 'queue' && <DecisionTabs key="q" initialTab="decide" variant="desktop" hideTabs />}
          {mode === 'manual' && <ClassicTravelTinder />}
          {mode === 'review' && <Review />}
          {mode === 'match' && <Match />}
        </>
      )}
      {tab === 'matched' && <DecisionTabs key="m" initialTab="matched" variant="desktop" hideTabs />}
      {tab === 'waiting' && <DecisionTabs key="w" initialTab="waiting" variant="desktop" hideTabs />}
    </div>
  );
}
