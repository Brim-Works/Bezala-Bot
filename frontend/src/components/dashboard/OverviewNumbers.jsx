import '../../mobile/mobile.css';
import { useEffect, useState } from 'react';
import { api } from '../../api/client.js';
import { useRouter } from '../../router/useRouter.jsx';

/* C47 Del 6 — Översikt: tre siffror + senaste scanning. */
export default function OverviewNumbers({ version = 0 }) {
  const [data, setData] = useState(null);
  const { navigate } = useRouter();
  useEffect(() => {
    api.overview().then(setData).catch(() => setData(null));
  }, [version]);
  const tiles = [
    { key: 'to_decide', label: 'Att besluta', hint: 'Svep-kön i Travel Tinder' },
    { key: 'card_lines_without_receipt', label: 'Kortrader utan kvitto', hint: 'Bezala' },
    { key: 'receipts_waiting_for_transaction', label: 'Kvitton som väntar på transaktion', hint: 'Kommer oftast inom 1–5 dagar' },
  ];
  const last = data?.last_scan;
  return (
    <div className="overview-numbers">
      <div className="stat-grid">
        {tiles.map((tile) => (
          <button key={tile.key} type="button" className="stat overview-tile"
            data-testid={`overview-${tile.key}`} onClick={() => navigate('/travel-tinder')}>
            <div className="stat__label">{tile.label}</div>
            <div className="stat__value mono">{data ? data[tile.key] : '—'}</div>
            <div className="stat__sub muted">{tile.hint}</div>
          </button>
        ))}
      </div>
      {last && (
        <p className="muted overview-last">
          Senaste scanning: <span className="mono">{(last.finished_at || last.started_at || '').replace('T', ' ').slice(0, 16)}</span>
          {' · '}{last.status} · {last.found ?? 0} hittade · {last.processed ?? 0} sparade
          {last.errors ? ` · ${last.errors} fel` : ''}
        </p>
      )}
    </div>
  );
}
