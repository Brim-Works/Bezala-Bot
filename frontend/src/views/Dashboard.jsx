import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { withStatuses } from '../api/adapters.js';
import { useApiData } from '../hooks/useApiData.js';
import { filterMessages } from '../lib/filterMessages.js';
import { deriveDashboardStats } from '../lib/deriveDashboardStats.js';
import { fmtRelative } from '../lib/format.js';
import { useScanFeedback } from '../hooks/useScanFeedback.js';
import { useDrawer } from '../drawer/DrawerProvider.jsx';

import HeroStrip from '../components/dashboard/HeroStrip.jsx';
import StatGrid from '../components/dashboard/StatGrid.jsx';
import FilterTabs from '../components/dashboard/FilterTabs.jsx';
import MessageTable from '../components/dashboard/MessageTable.jsx';
import RunBars from '../components/dashboard/RunBars.jsx';

const POLL_INTERVAL_MS = 60_000;
const MESSAGES_LIMIT = 100;

function lastRunHadNoNewMail(stats) {
  const last = stats?.last_run;
  if (!last) return false;
  const processed = last.messages_processed ?? null;
  if (processed == null) return false;
  return processed === 0;
}

export default function Dashboard() {
  const { t, lang } = useI18n();
  const { selectMessage, openDrawer } = useDrawer();
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedIdLocal] = useState(null);

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

  useEffect(() => {
    const id = setInterval(() => {
      refetch().catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refetch]);

  const { runScan } = useScanFeedback(() => refetch().catch(() => {}));

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

  const setSelectedId = useCallback(
    (id) => {
      setSelectedIdLocal(id);
      const found = messages.find((m) => m.id === id) || null;
      selectMessage(found);
    },
    [messages, selectMessage],
  );

  const onRowActivate = useCallback(
    (id) => {
      const found = messages.find((m) => m.id === id);
      if (found) {
        setSelectedIdLocal(id);
        openDrawer(found, 'gmail');
      }
    },
    [messages, openDrawer],
  );

  const lastFinished = data?.stats?.last_run?.finished_at;
  const noNewLastRun = lastRunHadNoNewMail(data?.stats);
  const lastRunLabel = lastFinished
    ? noNewLastRun
      ? t.stats.noNewMailLastRun
      : fmtRelative(lastFinished, lang)
    : null;

  return (
    <>
      <HeroStrip pendingCount={stats.pending} />

      <StatGrid stats={stats} />

      <div className="section-header">
        <h2>{t.sections.processed}</h2>
        <div className="section-header__meta">
          <span className="muted">
            <span className="mono">{filtered.length}</span> {t.sections.rows}
            {lastRunLabel ? (
              <>
                {' · '}
                {t.stats.lastRun}{' '}
                {noNewLastRun ? (
                  <span>{lastRunLabel}</span>
                ) : (
                  <span className="mono">{lastRunLabel}</span>
                )}
              </>
            ) : null}
          </span>
          <button
            type="button"
            className="btn primary"
            onClick={runScan}
            data-testid="scan-button"
          >
            {t.topbar.scan}
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
        onActivate={onRowActivate}
        isLoading={isLoading}
      />

      <div className="section-header">
        <h2>{t.sections.runs}</h2>
        <span className="muted">{t.runs.subtitle}</span>
      </div>
      <RunBars runs={runs} />
    </>
  );
}
