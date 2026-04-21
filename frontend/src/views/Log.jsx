import { useCallback, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { withStatuses } from '../api/adapters.js';
import { useApiData } from '../hooks/useApiData.js';
import { isWithinDays } from '../lib/format.js';

import KpiStrip from '../components/log/KpiStrip.jsx';
import RunList from '../components/log/RunList.jsx';
import RunDetail from '../components/log/RunDetail.jsx';
import Toast from '../components/Toast.jsx';

function messagesForRun(run, allMessages) {
  if (!run || !run.started_at) return [];
  const start = new Date(run.started_at).getTime();
  const end = run.finished_at
    ? new Date(run.finished_at).getTime()
    : Date.now();
  return allMessages.filter((m) => {
    if (!m.processed_at) return false;
    const t = new Date(m.processed_at).getTime();
    if (Number.isNaN(t)) return false;
    return t >= start && t <= end;
  });
}

export default function Log() {
  const { t } = useI18n();
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [clearingErrors, setClearingErrors] = useState(false);
  const [toast, setToast] = useState(null);

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

  const selectedRun = useMemo(() => {
    if (selectedRunId != null) {
      return runs.find((r) => r.id === selectedRunId) || runs[0] || null;
    }
    return runs[0] || null;
  }, [runs, selectedRunId]);

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
      setToast({
        kind: 'ok',
        message: `${t.log.toast.cleared} (${count})`,
      });
      refetch().catch(() => {});
    } catch (err) {
      setToast({
        kind: 'err',
        message: `${t.log.toast.clearFailed}: ${err.message || err}`,
      });
    } finally {
      setClearingErrors(false);
    }
  }, [refetch, t.log.confirmClear, t.log.toast.cleared, t.log.toast.clearFailed]);

  return (
    <>
      <KpiStrip
        runs24h={kpi.runs24h}
        autoRate={kpi.autoRate}
        autoCount={kpi.autoCount}
        processedCount={kpi.processedCount}
        errorCount={kpi.errorCount}
      />

      <div className="log-split">
        <RunList
          runs={runs}
          selectedId={selectedRun?.id ?? null}
          onSelect={setSelectedRunId}
          onClearErrors={clearErrors}
          clearingErrors={clearingErrors}
        />
        <RunDetail run={selectedRun} messages={runMessages} />
      </div>

      <Toast toast={toast} onDismiss={() => setToast(null)} />
    </>
  );
}
