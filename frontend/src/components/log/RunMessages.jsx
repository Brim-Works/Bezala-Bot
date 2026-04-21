import VendorLogo from '../VendorLogo.jsx';
import StatusCell from '../StatusCell.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';

/* Meddelanden som föll inom körningens tidsintervall (processed_at mellan
 * run.started_at och run.finished_at). Oprecist när körningar överlappar
 * — se BACKEND-TODO (message_ids saknas på /api/runs). */
export default function RunMessages({ messages, onOpenMessage }) {
  const { t, lang } = useI18n();
  if (!messages || messages.length === 0) return null;

  return (
    <div className="card run-messages" data-testid="run-messages">
      <div className="run-messages__head">
        <span className="run-messages__title">{t.log.messagesTitle}</span>
        <span className="mono muted">{messages.length}</span>
      </div>
      <table className="tbl">
        <tbody>
          {messages.map((m) => (
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
