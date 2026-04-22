import { useState } from 'react';
import VendorLogo from '../VendorLogo.jsx';
import StatusCell from '../StatusCell.jsx';
import { IconRefresh } from '../../icons/index.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';
import { api } from '../../api/client.js';
import { useToast } from '../../lib/toast.jsx';

/* Meddelanden som föll inom körningens tidsintervall (processed_at mellan
 * run.started_at och run.finished_at). Oprecist när körningar överlappar
 * — se BACKEND-TODO (message_ids saknas på /api/runs).
 *
 * Rader med file_status='skipped' får en "Försök igen"-knapp som raderar
 * DB-raden, tar bort Gmail-etiketten och triggar en ny scan. */
export default function RunMessages({ messages, onOpenMessage, onReprocessed }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [retrying, setRetrying] = useState(() => new Set());

  if (!messages || messages.length === 0) return null;

  async function onRetry(id) {
    setRetrying((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    try {
      await api.reprocessMessage(id);
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

  return (
    <div className="card run-messages" data-testid="run-messages">
      <div className="run-messages__head">
        <span className="run-messages__title">{t.log.messagesTitle}</span>
        <span className="mono muted">{messages.length}</span>
      </div>
      <table className="tbl">
        <tbody>
          {messages.map((m) => {
            const canRetry = m.file_status === 'skipped';
            const isRetrying = retrying.has(m.id);
            return (
              <tr
                key={m.id}
                onClick={() => onOpenMessage?.(m.id)}
                data-testid={`run-message-${m.id}`}
              >
                <td className="mono tbl__col-id" style={{ width: 80 }}>
                  #{String(m.id).padStart(4, '0')}
                </td>
                <td style={{ width: 200 }}>
                  <span className="vchip">
                    <VendorLogo name={m.vendor} />
                    <span>{m.vendor || <span className="muted">—</span>}</span>
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
                      className="btn btn-ghost btn-sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRetry(m.id);
                      }}
                      disabled={isRetrying}
                      data-testid={`retry-${m.id}`}
                      aria-label={t.log.retry}
                    >
                      <IconRefresh />
                      <span>{isRetrying ? t.log.retrying : t.log.retry}</span>
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
