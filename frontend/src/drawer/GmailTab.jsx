import { useI18n } from '../i18n/useI18n.jsx';
import { fmtDate } from '../lib/format.js';
import { IconMail } from '../icons/index.jsx';

function Row({ label, children }) {
  return (
    <div className="drawer-kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export default function GmailTab({ message }) {
  const { t, lang } = useI18n();
  if (!message) return null;

  const gmailUrl = message.message_id
    ? `https://mail.google.com/mail/u/0/#inbox/${message.message_id}`
    : null;

  return (
    <div className="drawer-section" data-testid="drawer-tab-gmail-content">
      <dl className="drawer-kv-list">
        <Row label={t.drawer.gmail.from}>
          <span className="mono">{message.sender || '—'}</span>
        </Row>
        <Row label={t.drawer.gmail.subject}>
          <span>{message.subject || '—'}</span>
        </Row>
        <Row label={t.drawer.gmail.received}>
          <span className="mono">{fmtDate(message.received_at, lang)}</span>
        </Row>
        <Row label={t.drawer.gmail.messageId}>
          <span className="mono">{message.message_id || '—'}</span>
        </Row>
        <Row label={t.drawer.gmail.attachments}>
          {message.file_name ? (
            <span className="mono">{message.file_name}</span>
          ) : (
            <span className="muted">{t.drawer.gmail.noAttachments}</span>
          )}
        </Row>
      </dl>
      <p className="drawer-note muted">{t.drawer.gmail.note}</p>
      {gmailUrl ? (
        <a
          href={gmailUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="btn"
        >
          <IconMail className="icon sm" />
          {t.drawer.gmail.openInGmail} ↗
        </a>
      ) : null}
    </div>
  );
}
