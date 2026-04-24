import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { withStatuses } from '../api/adapters.js';
import { useApiData } from '../hooks/useApiData.js';
import { isWithinDays, parseBackendDate } from '../lib/format.js';
import { useToast } from '../lib/toast.jsx';
import { useDrawer } from '../drawer/DrawerProvider.jsx';

import KpiStrip from '../components/log/KpiStrip.jsx';
import RunList from '../components/log/RunList.jsx';
import RunDetail from '../components/log/RunDetail.jsx';
import LogSearch from '../components/log/LogSearch.jsx';
import AllHitsList from '../components/log/AllHitsList.jsx';

const POLL_INTERVAL_MS = 60_000;

const DATE_FILTERS = {
  all: null,
  last24h: 1,
  last7d: 7,
  last30d: 30,
};

function messagesForRun(run, allMessages) {
  if (!run || !run.started_at) return [];
  const startDate = parseBackendDate(run.started_at);
  if (!startDate) return [];
  const start = startDate.getTime();
  const endDate = run.finished_at ? parseBackendDate(run.finished_at) : null;
  const end = endDate ? endDate.getTime() : Date.now();
  return allMessages.filter((m) => {
    const mDate = parseBackendDate(m.processed_at);
    if (!mDate) return false;
    const t = mDate.getTime();
    return t >= start && t <= end;
  });
}

export default function Log() {
  const { t } = useI18n();
  const toast = useToast();
  const { openDrawer } = useDrawer();
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [clearingErrors, setClearingErrors] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [dateFilter, setDateFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [listMode, setListMode] = useState('runs');

  const loader = useCallback(async () => {
    const [runs, rawMessages] = await Promise.all([
      api.runs(50),
      api.messages(200),
    ]);
    return {
      runs: runs || [],
      messages: (rawMessages || []).map(withStatuses),
    };
  }, []);

  const { data, refetch } = useApiData(loader, []);
  const runs = data?.runs || [];
  const messages = data?.messages || [];

  const searchLower = searchText.trim().toLowerCase();

  // Hjälpare: returnerar true om någon av körningens messages eller
  // filtered_messages matchar söktexten (sender/subject).
  const runMatchesSearch = useCallback(
    (run) => {
      if (!searchLower) return true;
      const entries = run?.filtered_messages || [];
      for (const e of entries) {
        if (
          (e.sender || '').toLowerCase().includes(searchLower) ||
          (e.subject || '').toLowerCase().includes(searchLower)
        ) {
          return true;
        }
      }
      // Matcha även mot DB-rader inom körningens tidsintervall
      const runMessages = messagesForRun(run, messages);
      return runMessages.some(
        (m) =>
          (m.sender || '').toLowerCase().includes(searchLower) ||
          (m.subject || '').toLowerCase().includes(searchLower),
      );
    },
    [searchLower, messages],
  );

  // Filtrera körningslistan på datum + status + text-sök
  const filteredRuns = useMemo(() => {
    const days = DATE_FILTERS[dateFilter];
    return runs.filter((run) => {
      if (days != null && !isWithinDays(run.started_at, days)) return false;
      if (statusFilter !== 'all') {
        const processed = run.messages_processed || 0;
        const errorCount = run.errors || 0;
        if (statusFilter === 'error' && !(errorCount > 0 && processed === 0))
          return false;
        if (statusFilter === 'partial' && !(errorCount > 0 && processed > 0))
          return false;
        if (statusFilter === 'idle' && !(errorCount === 0 && processed === 0))
          return false;
        if (statusFilter === 'ok' && !(errorCount === 0 && processed > 0))
          return false;
      }
      if (!runMatchesSearch(run)) return false;
      return true;
    });
  }, [runs, dateFilter, statusFilter, runMatchesSearch]);

  useEffect(() => {
    const id = setInterval(() => {
      refetch().catch(() => {});
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refetch]);

  const selectedRun = useMemo(() => {
    if (selectedRunId != null) {
      // Försök först bland filtrerade körningar; annars fall tillbaka på alla
      return (
        filteredRuns.find((r) => r.id === selectedRunId) ||
        runs.find((r) => r.id === selectedRunId) ||
        filteredRuns[0] ||
        runs[0] ||
        null
      );
    }
    return filteredRuns[0] || runs[0] || null;
  }, [runs, filteredRuns, selectedRunId]);

  const runsLast24h = useMemo(
    () => runs.filter((r) => isWithinDays(r.started_at, 1)),
    [runs],
  );

  const kpi = useMemo(() => {
    const processed = runsLast24h.reduce(
      (sum, r) => sum + (r.messages_processed || 0),
      0,
    );
    const errors = runsLast24h.reduce((sum, r) => sum + (r.errors || 0), 0);
    const messagesInLast24h = messages.filter((m) =>
      isWithinDays(m.processed_at, 1),
    );
    const autoCount = messagesInLast24h.filter(
      (m) => m.bezala_status === 'transferred',
    ).length;
    const autoRate = processed > 0 ? Math.round((autoCount / processed) * 100) : 0;
    return {
      runs24h: runsLast24h.length,
      processedCount: processed,
      autoCount,
      autoRate,
      errorCount: errors,
    };
  }, [runsLast24h, messages]);

  const runMessages = useMemo(
    () => messagesForRun(selectedRun, messages),
    [selectedRun, messages],
  );

  // Text-sök filtrerar sparade + filtrerade rader inom vald körning
  // (samma söktext används också för runMatchesSearch ovan, så runs
  //  som inte innehåller matchande meddelanden faller bort direkt).
  const matchesSearch = useCallback(
    (sender, subject) => {
      if (!searchLower) return true;
      const s = (sender || '').toLowerCase();
      const sub = (subject || '').toLowerCase();
      return s.includes(searchLower) || sub.includes(searchLower);
    },
    [searchLower],
  );

  const visibleMessages = useMemo(
    () => runMessages.filter((m) => matchesSearch(m.sender, m.subject)),
    [runMessages, matchesSearch],
  );

  const visibleFiltered = useMemo(() => {
    const entries = selectedRun?.filtered_messages || [];
    return entries.filter((e) => matchesSearch(e.sender, e.subject));
  }, [selectedRun, matchesSearch]);

  // "Alla träffar"-läge: platta ut meddelanden över alla körningar och
  // behåll körnings-metadata per rad. Tomt när söktexten är tom.
  const allHits = useMemo(() => {
    if (listMode !== 'all' || !searchLower) return [];
    const items = [];
    for (const run of runs) {
      const runMsgs = messagesForRun(run, messages);
      for (const m of runMsgs) {
        if (!matchesSearch(m.sender, m.subject)) continue;
        items.push({
          key: `p-${run.id}-${m.id}`,
          kind: 'processed',
          id: m.id,
          messageId: m.message_id,
          sender: m.sender,
          subject: m.subject,
          date: m.processed_at || m.received_at,
          runId: run.id,
          runStartedAt: run.started_at,
        });
      }
      for (const f of run.filtered_messages || []) {
        if (!matchesSearch(f.sender, f.subject)) continue;
        items.push({
          key: `f-${run.id}-${f.message_id || items.length}`,
          kind: 'filtered',
          messageId: f.message_id,
          sender: f.sender || '',
          subject: f.subject || '',
          date: f.received_at,
          reason: f.reason,
          runId: run.id,
          runStartedAt: run.started_at,
        });
      }
    }
    items.sort((a, b) => {
      const da = a.date ? new Date(a.date).getTime() : 0;
      const db = b.date ? new Date(b.date).getTime() : 0;
      return db - da;
    });
    return items;
  }, [listMode, searchLower, runs, messages, matchesSearch]);

  const clearErrors = useCallback(async () => {
    const confirmed =
      typeof window !== 'undefined'
        ? window.confirm(t.log.confirmClear)
        : true;
    if (!confirmed) return;
    setClearingErrors(true);
    try {
      const result = await api.deleteErrors();
      const count = result?.deleted ?? 0;
      toast.show({
        kind: 'ok',
        message: `${t.log.toast.cleared} (${count})`,
      });
      refetch().catch(() => {});
    } catch (err) {
      toast.show({
        kind: 'err',
        message: `${t.log.toast.clearFailed}: ${err.message || err}`,
      });
    } finally {
      setClearingErrors(false);
    }
  }, [refetch, t.log.confirmClear, t.log.toast.cleared, t.log.toast.clearFailed, toast]);

  const onOpenMessage = useCallback(
    (id) => {
      const row = messages.find((m) => m.id === id);
      if (row) openDrawer(row, 'gmail');
    },
    [messages, openDrawer],
  );

  const onReprocessed = useCallback(() => {
    // Ladda om /api/messages så raden försvinner ur listan och ny
    // scan-rad dyker upp när bakgrundsscan är klar.
    refetch().catch(() => {});
  }, [refetch]);

  return (
    <>
      <KpiStrip
        runs24h={kpi.runs24h}
        autoRate={kpi.autoRate}
        autoCount={kpi.autoCount}
        processedCount={kpi.processedCount}
        errorCount={kpi.errorCount}
      />

      <LogSearch
        searchText={searchText}
        onSearchText={setSearchText}
        dateFilter={dateFilter}
        onDateFilter={setDateFilter}
        statusFilter={statusFilter}
        onStatusFilter={setStatusFilter}
        listMode={listMode}
        onListMode={setListMode}
      />

      {listMode === 'all' ? (
        <AllHitsList
          hits={allHits}
          searchText={searchText}
          onOpenMessage={onOpenMessage}
        />
      ) : (
        <div className="log-split">
          <RunList
            runs={filteredRuns}
            selectedId={selectedRun?.id ?? null}
            onSelect={setSelectedRunId}
            onClearErrors={clearErrors}
            clearingErrors={clearingErrors}
          />
          <RunDetail
            run={selectedRun}
            messages={visibleMessages}
            filteredEntries={visibleFiltered}
            onOpenMessage={onOpenMessage}
            onReprocessed={onReprocessed}
          />
        </div>
      )}
    </>
  );
}
