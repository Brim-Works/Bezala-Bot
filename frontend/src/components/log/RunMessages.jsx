import { useState } from 'react';
import VendorLogo from '../VendorLogo.jsx';
import StatusCell from '../StatusCell.jsx';
import Pill from '../Pill.jsx';
import { IconRefresh } from '../../icons/index.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';
import { displayVendor } from '../../lib/vendorFromSender.js';
import { api } from '../../api/client.js';
import { useToast } from '../../lib/toast.jsx';

/* Meddelanden som föll inom körningens tidsintervall (processed_at mellan
 * run.started_at och run.finished_at). Oprecist när körningar överlappar
 * — se BACKEND-TODO (message_ids saknas på /api/runs).
 *
 * Gate 2: "Försök igen"-knapp på skipped DB-rader.
 * Gate 1.5: Tabellen visar även filteredEntries (mail utan DB-rad som
 * filtrerades bort av pipelinen). Dessa är inte klickbara och har
 * ingen "Försök igen"-knapp — bara en reason-pill. */
function reasonLabel(t, entry) {
  const base = t.log.filtered[entry.reason] || entry.reason;
  if (entry.reason === 'ai_filtered' && entry.confidence != null) {
    return base.replace('{confidence}', entry.confidence);
  }
  return base;
}

export default function RunMessages({
  messages,
  filteredEntries,
  onOpenMessage,
  onReprocessed,
}) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [retrying, setRetrying] = useState(() => new Set());

  const hasMessages = messages && messages.length > 0;
  const hasFiltered = filteredEntries && filteredEntries.length > 0;
  if (!hasMessages && !hasFiltered) return null;

  async function onRetry(id) {
    setRetrying((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    try {
      await api.reprocessMessage(id);
      // Trigga scanning direkt istället för att vänta på schemalagd körning.
      // Reprocess-endpointen kickar redan igång en bakgrundsscan (max_results=10),
      // men vi anropar /api/scan separat också så useScanFeedback-hooken i
      // parent (Log/Dashboard-topbar) triggar polling + toast när scan är klar.
      try {
        await api.scan();
      } catch {
        // Ignorera — reprocess har redan triggat sin egen bakgrundsscan
      }
      toast.show({ kind: 'ok', message: t.log.toast.reprocessQueued });
      onReprocessed?.(id);
    } catch (err) {
      toast.show({
        kind: 'err',
        message: `${t.log.toast.reprocessFailed}: ${err.message || err}`,
      });
    } finally {
      setRetrying((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  const totalCount = (messages?.length || 0) + (filteredEntries?.length || 0);

  return (
    <div className="card run-messages" data-testid="run-messages">
      <div className="run-messages__head">
        <span className="run-messages__title">{t.log.messagesTitle}</span>
        <span className="mono muted">{totalCount}</span>
      </div>
      <table className="tbl">
        <tbody>
          {hasMessages &&
            messages.map((m) => {
              const canRetry = m.file_status === 'skipped';
              const isRetrying = retrying.has(m.id);
              return (
                <tr
                  key={`saved-${m.id}`}
                  onClick={() => onOpenMessage?.(m.id)}
                  data-testid={`run-message-${m.id}`}
                >
                  <td className="mono tbl__col-id" style={{ width: 80 }}>
                    #{String(m.id).padStart(4, '0')}
                  </td>
                  <td style={{ width: 200 }}>
                    <span className="vchip">
                      <VendorLogo name={displayVendor(m)} />
                      <span>{displayVendor(m) || <span className="muted">—</span>}</span>
                    </span>
                  </td>
                  <td className="tbl__subject">
                    {m.subject || <span className="muted">—</span>}
                  </td>
                  <td className="mono tbl__amount" style={{ width: 120 }}>
                    {m.amount != null ? fmtAmount(m.amount, m.currency, lang) : '—'}
                  </td>
                  <td style={{ width: 160 }}>
                    <StatusCell
                      fileStatus={m.file_status}
                      bezalaStatus={m.bezala_status}
                    />
                  </td>
                  <td style={{ width: 120 }}>
                    {canRetry ? (
                      <button
                        type="button"
                        className="btn primary"
                        onClick={(e) => {
                          e.stopPropagation();
                          onRetry(m.id);
                        }}
                        disabled={isRetrying}
                        data-testid={`retry-${m.id}`}
                        aria-label={t.log.retry}
                        title={t.log.retryHint}
                      >
                        <IconRefresh />
                        <span>{isRetrying ? t.log.retrying : t.log.retry}</span>
                      </button>
                    ) : null}
                  </td>
                </tr>
              );
            })}

          {hasMessages && hasFiltered ? (
            <tr
              className="run-messages__separator"
              data-testid="run-messages-separator"
            >
              <td colSpan={6} className="muted">
                — {t.log.filtered.title} —
              </td>
            </tr>
          ) : null}

          {hasFiltered &&
            filteredEntries.map((e, idx) => (
              <tr
                key={`filtered-${e.message_id || idx}`}
                className="run-messages__filtered muted"
                data-testid={`filtered-row-${e.message_id || idx}`}
              >
                <td className="mono tbl__col-id" style={{ width: 80 }}>
                  —
                </td>
                <td style={{ width: 200 }}>
                  <span className="mono">
                    {e.sender || <span className="muted">—</span>}
                  </span>
                </td>
                <td className="tbl__subject">
                  {e.subject || <span className="muted">—</span>}
                </td>
                <td className="mono tbl__amount" style={{ width: 120 }}>
                  —
                </td>
                <td style={{ width: 160 }}>
                  <span
                    className="pill pill--warn"
                    data-testid={`filtered-reason-${e.message_id || idx}`}
                  >
                    <span className="pill__dot" aria-hidden="true" />
                    {reasonLabel(t, e)}
                  </span>
                </td>
                <td style={{ width: 120 }} />
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
