import { useEffect, useState } from 'react';
import { api } from '../../api/client.js';
import { useI18n } from '../../i18n/useI18n.jsx';

/* C48 Del 5 — +kvitto-adressen som boten räknat ut från de anslutna
 * Gmail-kontona, vilka avsändare som godkänns och de senaste mailen.
 * Adresserna är inte hemliga. */
export default function ReceiptInboxBlock() {
  const { t } = useI18n();
  const s = t.settings.auth.inbox;
  const [data, setData] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    api.receiptInbox().then((d) => { if (alive) setData(d); }).catch(() => {});
    return () => { alive = false; };
  }, []);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(data.address);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      /* urklipp kan vara blockerat — adressen syns ändå */
    }
  };

  if (!data) return null;
  const extra = (data.also_accepted || []).filter((a) => a !== data.address);
  return (
    <div className="settings-auth-row settings-inbox" data-testid="receipt-inbox">
      <div className="settings-auth-row__label">
        <span className="settings-field__label">{s.title}</span>
        <span className="muted">{s.lead}</span>
        {data.address ? (
          <span>
            <code data-testid="receipt-inbox-address">{data.address}</code>{' '}
            <button type="button" className="btn ghost" onClick={copy}
              data-testid="receipt-inbox-copy">
              {copied ? s.copied : s.copy}
            </button>
          </span>
        ) : (
          <span className="muted">{s.unavailable}</span>
        )}
        {extra.length > 0 && <span className="muted">{s.alsoAccepted} {extra.join(', ')}</span>}
        {(data.allowed_senders || []).length > 0 && (
          <span className="muted">{s.allowed} {data.allowed_senders.join(', ')}</span>
        )}
        <span className="settings-field__label">{s.recent}</span>
        {(data.recent || []).length === 0 ? (
          <span className="muted">{s.none}</span>
        ) : (
          <ul className="settings-inbox__list" data-testid="receipt-inbox-recent">
            {data.recent.map((r, i) => (
              <li key={`${r.at}-${i}`}>
                {r.at ? new Date(r.at).toLocaleString() : '—'} · {r.sender || '—'} ·{' '}
                {s.attachments.replace('{n}', r.attachments ?? 0)} ·{' '}
                <strong>{s[r.outcome] || r.outcome}</strong>
                {r.reason ? ` (${r.reason})` : ''}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
