import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { withStatuses } from '../api/adapters.js';
import { useApiData } from '../hooks/useApiData.js';
import { filterMessages } from '../lib/filterMessages.js';
import { deriveDashboardStats } from '../lib/deriveDashboardStats.js';
import { fmtRelative } from '../lib/format.js';

import HeroStrip from '../components/dashboard/HeroStrip.jsx';
import StatGrid from '../components/dashboard/StatGrid.jsx';
import FilterTabs from '../components/dashboard/FilterTabs.jsx';
import MessageTable from '../components/dashboard/MessageTable.jsx';
import RunBars from '../components/dashboard/RunBars.jsx';
import Toast from '../components/Toast.jsx';

const POLL_INTERVAL_MS = 30000;
const MESSAGES_LIMIT = 100;

export default function Dashboard() {
  const { t, lang } = useI18n();
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [toast, setToast] = useState(null);

  const loader = useCallback(async () => {
    const [messages, stats, runs] = await Promise.all([
      api.messages(MESSAGES_LIMIT),
      api.stats(),
      api.runs(14),
    ]);
    return {
      messages: (messages || []).map(withStatuses),
      stats,
      runs: runs || [],
    };
  }, []);

  const { data, isLoading, refetch } = useApiData(loader, []);
  const messages = data?.messages || [];
  const runs = data?.runs || [];

  // Polling — refetcha var 30:e sekund för att hålla dashboarden levande.
  useEffect(() => {
    const id = setInterval(() => {
      refetch().catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refetch]);

  const stats = useMemo(
    () => deriveDashboardStats(messages, data?.stats),
    [messages, data?.stats],
  );

  const filtered = useMemo(
    () => filterMessages(messages, { filter, query }),
    [messages, filter, query],
  );

  const counts = useMemo(
    () => ({
      all: messages.length,
      pending: messages.filter((m) => m.bezala_status === 'pending').length,
      auto: messages.filter((m) => m.bezala_status === 'transferred').length,
      errors: messages.filter(
        (m) => m.file_status === 'error' || m.bezala_status === 'error',
      ).length,
    }),
    [messages],
  );

  const triggerScan = useCallback(async () => {
    setScanning(true);
    try {
      await api.scan();
      setToast({ kind: 'ok', message: t.toast.scanStarted });
      // Pipeline kan ta några sekunder — refetcha efter en kort stund.
      setTimeout(() => {
        refetch().catch(() => {});
      }, 1500);
    } catch (err) {
      setToast({ kind: 'err', message: `${t.toast.scanFailed}: ${err.message}` });
    } finally {
      setTimeout(() => setScanning(false), 1500);
    }
  }, [refetch, t.toast.scanFailed, t.toast.scanStarted]);

  const lastFinished = data?.stats?.last_run?.finished_at;

  return (
    <>
      <HeroStrip pendingCount={stats.pending} />

      <StatGrid stats={stats} />

      <div className="section-header">
        <h2>{t.sections.processed}</h2>
        <div className="section-header__meta">
          <span className="muted">
            <span className="mono">{filtered.length}</span> {t.sections.rows}
            {lastFinished ? (
              <>
                {' '}
                · {t.stats.lastRun}{' '}
                <span className="mono">{fmtRelative(lastFinished, lang)}</span>
              </>
            ) : null}
          </span>
          <button
            type="button"
            className="btn primary"
            onClick={triggerScan}
            disabled={scanning}
          >
            {scanning ? t.topbar.scanning : t.topbar.scan}
          </button>
        </div>
      </div>

      <FilterTabs
        filter={filter}
        setFilter={setFilter}
        query={query}
        setQuery={setQuery}
        counts={counts}
      />

      <MessageTable
        messages={filtered}
        selectedId={selectedId}
        onSelect={setSelectedId}
        isLoading={isLoading}
      />

      <div className="section-header">
        <h2>{t.sections.runs}</h2>
        <span className="muted">{t.runs.subtitle}</span>
      </div>
      <RunBars runs={runs} />

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </>
  );
}
