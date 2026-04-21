import { useCallback, useRef } from 'react';
import { api } from '../api/client.js';
import { useI18n } from '../i18n/useI18n.jsx';
import { useToast } from '../lib/toast.jsx';

const POLL_INTERVAL_MS = 2500;
const POLL_TIMEOUT_MS = 30_000;

/* Triggar POST /api/scan och pollar /api/runs?limit=1 tills senaste
 * körningen är nyare än scan-starten — då visar vi toast med antal
 * hittade mail. Timeout efter 30 s → fallback-toast "Scanning pågår". */
export function useScanFeedback(onCompletion) {
  const { t } = useI18n();
  const toast = useToast();
  const inFlight = useRef(false);

  const runScan = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;

    const scanStartedAt = Date.now();
    try {
      await api.scan();
    } catch (err) {
      inFlight.current = false;
      toast.show({
        kind: 'err',
        message: `${t.scanFeedback.failed}: ${err.message || err}`,
      });
      return;
    }

    const deadline = Date.now() + POLL_TIMEOUT_MS;
    let resolved = false;

    async function pollOnce() {
      if (resolved) return;
      try {
        const runs = await api.runs(1);
        const latest = Array.isArray(runs) && runs.length > 0 ? runs[0] : null;
        if (latest && latest.finished_at) {
          const finishedAt = new Date(latest.finished_at).getTime();
          if (!Number.isNaN(finishedAt) && finishedAt >= scanStartedAt) {
            resolved = true;
            inFlight.current = false;
            const found = latest.messages_found || 0;
            const processed = latest.messages_processed || 0;
            if (found === 0) {
              toast.show({ kind: 'ok', message: t.scanFeedback.noNewMail });
            } else {
              toast.show({
                kind: 'ok',
                message: t.scanFeedback.found
                  .replace('{found}', String(found))
                  .replace('{processed}', String(processed)),
              });
            }
            onCompletion?.();
            return;
          }
        }
      } catch {
        // Ignorera polling-fel — vi försöker igen.
      }

      if (Date.now() >= deadline) {
        resolved = true;
        inFlight.current = false;
        toast.show({ kind: 'warn', message: t.scanFeedback.timeout });
        return;
      }
      setTimeout(pollOnce, POLL_INTERVAL_MS);
    }

    toast.show({ kind: 'ok', message: t.scanFeedback.started });
    setTimeout(pollOnce, POLL_INTERVAL_MS);
  }, [onCompletion, t, toast]);

  return { runScan, isScanning: () => inFlight.current };
}
