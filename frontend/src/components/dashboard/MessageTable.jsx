import VendorLogo from '../VendorLogo.jsx';
import Confidence from '../Confidence.jsx';
import StatusCell from '../StatusCell.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount, fmtRelative } from '../../lib/format.js';

export default function MessageTable({ messages, selectedId, onSelect, isLoading }) {
  const { t, lang } = useI18n();

  if (isLoading && messages.length === 0) {
    return (
      <div className="card table-empty">
        <p className="muted">{t.common.loading}</p>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="card table-empty">
        <p className="muted">{t.empty.noMessages}</p>
      </div>
    );
  }

  return (
    <div className="card table-card">
      <table className="tbl">
        <thead>
          <tr>
            <th className="tbl__col-time">{t.cols.time}</th>
            <th className="tbl__col-vendor">{t.cols.vendor}</th>
            <th className="tbl__col-subject">{t.cols.subject}</th>
            <th className="tbl__col-file">{t.cols.file}</th>
            <th className="tbl__col-amount">{t.cols.amount}</th>
            <th className="tbl__col-conf">{t.cols.confidence}</th>
            <th className="tbl__col-status">{t.cols.status}</th>
          </tr>
        </thead>
        <tbody>
          {messages.map((m) => (
            <tr
              key={m.id}
              className={selectedId === m.id ? 'is-selected' : ''}
              onClick={() => onSelect(m.id)}
            >
              <td className="mono tbl__time">{fmtRelative(m.processed_at, lang)}</td>
              <td>
                <span className="vchip">
                  <VendorLogo name={m.vendor} />
                  <span>{m.vendor || <span className="muted">—</span>}</span>
                </span>
              </td>
              <td className="tbl__subject">{m.subject || <span className="muted">—</span>}</td>
              <td className="mono tbl__file">
                {m.file_name || <span className="muted">—</span>}
              </td>
              <td className="mono tbl__amount">
                {m.amount != null ? fmtAmount(m.amount, m.currency, lang) : <span className="muted">—</span>}
              </td>
              <td>
                <Confidence value={m.ai_confidence} />
              </td>
              <td>
                <StatusCell fileStatus={m.file_status} bezalaStatus={m.bezala_status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
