import { useCallback, useRef, useState } from 'react';
import { api } from '../api/client.js';
import { useI18n } from '../i18n/useI18n.jsx';
import { useToast } from '../lib/toast.jsx';
import { parseBackendDate } from '../lib/format.js';

const POLL_INTERVAL_MS = 2500;
// Gate 4: höjd från 30s → 45s. Railway-scanning kan ta upp till ~40s vid
// stor Gmail-kö innan en färsk ScanRun.finished_at syns i API:t.
const POLL_TIMEOUT_MS = 45_000;

/* Triggar POST /api/scan och pollar /api/runs?limit=1 tills senaste
 * körningen är nyare än scan-starten. Exposerar isScanning som React-
 * state så TopBar/Dashboard kan visa spinner + "Scannar..."-text
 * under körningens gång. Toast vid klart / tom / timeout. */
export function useScanFeedback(onCompletion) {
  const { t } = useI18n();
  const toast = useToast();
  const [isScanning, setIsScanning] = useState(false);
  const timerRef = useRef(null);

  const runScan = useCallback(async () => {
    if (isScanning) return;
    setIsScanning(true);

    const scanStartedAt = Date.now();
    try {
      await api.scan();
    } catch (err) {
      setIsScanning(false);
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
          const finishedDate = parseBackendDate(latest.finished_at);
          if (finishedDate && finishedDate.getTime() >= scanStartedAt) {
            resolved = true;
            setIsScanning(false);
            const found = latest.messages_found || 0;
            const processed = latest.messages_processed || 0;
            if (found === 0) {
              toast.show({ kind: 'ok', message: t.scanFeedback.noNewMail });
            } else {
              // Gate 4: ny text format "Scanning klar — X nya kvitton hittade"
              toast.show({
                kind: 'ok',
                message: t.scanFeedback.found.replace('{found}', String(found)),
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
        setIsScanning(false);
        toast.show({ kind: 'warn', message: t.scanFeedback.timeout });
        return;
      }
      timerRef.current = setTimeout(pollOnce, POLL_INTERVAL_MS);
    }

    toast.show({ kind: 'ok', message: t.scanFeedback.started });
    timerRef.current = setTimeout(pollOnce, POLL_INTERVAL_MS);
  }, [isScanning, onCompletion, t, toast]);

  return { runScan, isScanning };
}
