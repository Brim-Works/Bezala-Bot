import { useCallback, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { fmtDate } from '../lib/format.js';
import { IconMail, IconDownload } from '../icons/index.jsx';
import { api, ApiError } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';

function Row({ label, children }) {
  return (
    <div className="drawer-kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/* Preview-panel för mail-bodyn.
 * Renderas i sandboxad iframe så scripts/styles/JS inte kan köras —
 * sanitizing sker även server-side som defence-in-depth. Detekterade
 * <a href>-länkar listas separat (extract_links på servern). När
 * canFetch=true (needs_download-rader) är länkarna klickbara knappar
 * som triggar fetch-PDF; annars rendereras de som externa länkar. */
function MailPreview({ body, onFetchUrl, fetchingUrl, canFetch }) {
  const { t } = useI18n();
  const html = body?.html || '';
  const text = body?.text || '';
  const links = body?.links || [];
  const hasContent = html || text;

  return (
    <div className="mail-preview" data-testid="mail-preview">
      {html ? (
        <iframe
          title={t.drawer.gmail.previewTitle}
          className="mail-preview__frame"
          sandbox="allow-same-origin"
          srcDoc={html}
          data-testid="mail-preview-frame"
        />
      ) : text ? (
        <pre className="mail-preview__text" data-testid="mail-preview-text">
          {text}
        </pre>
      ) : (
        <p className="muted">{t.drawer.gmail.previewEmpty}</p>
      )}

      {hasContent && links.length > 0 ? (
        <div className="mail-preview__links" data-testid="mail-preview-links">
          <div className="mail-preview__links-label">
            {t.drawer.gmail.linksLabel}
          </div>
          <ul>
            {links.map((link) => {
              const busy = fetchingUrl === link.href;
              if (canFetch) {
                return (
                  <li key={link.href}>
                    <button
                      type="button"
                      className="btn primary mail-preview__link-btn"
                      onClick={() => onFetchUrl(link)}
                      disabled={Boolean(fetchingUrl)}
                      data-testid={`mail-preview-link-${link.href}`}
                      title={link.href}
                    >
                      <IconDownload className="icon sm" />
                      <span className="mail-preview__link-text">
                        {busy ? t.drawer.gmail.fetchingLink : link.text}
                      </span>
                    </button>
                  </li>
                );
              }
              return (
                <li key={link.href}>
                  <a
                    href={link.href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="mail-preview__link-ext"
                    title={link.href}
                  >
                    {link.text}
                  </a>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export default function GmailTab({ message, onUpdated, onTabChange }) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [body, setBody] = useState(null);
  const [loadingBody, setLoadingBody] = useState(false);
  const [fetchingUrl, setFetchingUrl] = useState(null);

  const needsDownload = message?.file_status === 'needs_download';
  const gmailUrl = message?.message_id
    ? `https://mail.google.com/mail/u/0/#inbox/${message.message_id}`
    : null;

  const onLoadPreview = useCallback(async () => {
    if (!message) return;
    setLoadingBody(true);
    try {
      const data = await api.messageBody(message.id);
      setBody(data);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.drawer.gmail.previewFailed}: ${detail}`,
      });
    } finally {
      setLoadingBody(false);
    }
  }, [message, t.drawer.gmail.previewFailed, toast]);

  const onFetchUrl = useCallback(
    async (link) => {
      if (!message) return;
      const confirmed =
        typeof window !== 'undefined'
          ? window.confirm(
              `${t.drawer.gmail.fetchConfirm}\n\n${link.text}\n${link.href}`,
            )
          : true;
      if (!confirmed) return;
      setFetchingUrl(link.href);
      try {
        const updated = await api.fetchPdfFromUrl(message.id, link.href);
        toast.show({ kind: 'ok', message: t.drawer.gmail.fetchSuccess });
        // Uppdatera drawer + Dashboard-listan
        onUpdated?.(updated);
        // Switcha till Drive-fliken så användaren ser previewan direkt
        onTabChange?.('drive');
      } catch (err) {
        const detail = err instanceof ApiError ? err.message : String(err);
        toast.show({
          kind: 'err',
          message: `${t.drawer.gmail.fetchFailed}: ${detail}`,
        });
      } finally {
        setFetchingUrl(null);
      }
    },
    [message, onUpdated, t, toast],
  );

  if (!message) return null;

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

      {body ? (
        <MailPreview
          body={body}
          onFetchUrl={onFetchUrl}
          fetchingUrl={fetchingUrl}
          canFetch={needsDownload}
        />
      ) : (
        <button
          type="button"
          className="btn primary"
          onClick={onLoadPreview}
          disabled={loadingBody}
          data-testid="show-mail-preview"
        >
          <IconMail className="icon sm" />
          <span>
            {loadingBody
              ? t.drawer.gmail.previewLoading
              : t.drawer.gmail.showPreview}
          </span>
        </button>
      )}

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
