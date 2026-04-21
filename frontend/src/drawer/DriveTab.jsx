import { useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { fmtDate } from '../lib/format.js';
import { IconDrive, IconDownload } from '../icons/index.jsx';
import { api } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { useTrashCountContext } from '../hooks/TrashCountProvider.jsx';

function Row({ label, children }) {
  return (
    <div className="drawer-kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function DownloadBanner({ message }) {
  const { t } = useI18n();
  const toast = useToast();
  const { bumpMessagesVersion } = useTrashCountContext();
  const [busy, setBusy] = useState(false);

  if (!message?.pending_link) return null;

  async function runFetch() {
    setBusy(true);
    try {
      await api.fetchPdf(message.id);
      toast.show({ kind: 'ok', message: t.drawer.drive.downloadSuccess });
      bumpMessagesVersion();
    } catch (err) {
      toast.show({
        kind: 'err',
        message: `${t.drawer.drive.downloadFailed}: ${err.message || err}`,
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="drawer-banner drawer-banner--warn" data-testid="download-banner">
      <IconDownload className="icon" />
      <div className="drawer-banner__body">
        <div className="drawer-banner__title">{t.drawer.drive.downloadTitle}</div>
        <p>{t.drawer.drive.downloadBody}</p>
        <a
          href={message.pending_link}
          target="_blank"
          rel="noreferrer noopener"
          className="mono drawer-banner__link"
          data-testid="pending-link"
        >
          {message.pending_link}
        </a>
        <button
          type="button"
          className="btn primary"
          onClick={runFetch}
          disabled={busy}
          data-testid="fetch-pdf-btn"
        >
          <IconDownload className="icon sm" />
          {busy ? t.drawer.drive.downloading : t.drawer.drive.downloadAction}
        </button>
      </div>
    </div>
  );
}

export default function DriveTab({ message }) {
  const { t, lang } = useI18n();
  const [iframeError, setIframeError] = useState(false);
  if (!message) return null;

  const needsDownload = message.status === 'needs_manual_download';
  const hasFile = Boolean(message.drive_file_id);
  const previewUrl = hasFile
    ? `https://drive.google.com/file/d/${message.drive_file_id}/preview`
    : null;
  const openUrl =
    message.drive_link ||
    (hasFile ? `https://drive.google.com/file/d/${message.drive_file_id}/view` : null);

  if (needsDownload) {
    return (
      <div className="drawer-section" data-testid="drawer-tab-drive-content">
        <DownloadBanner message={message} />
      </div>
    );
  }

  return (
    <div className="drawer-section" data-testid="drawer-tab-drive-content">
      <dl className="drawer-kv-list">
        <Row label={t.drawer.drive.filename}>
          {message.file_name ? (
            <span className="mono">{message.file_name}</span>
          ) : (
            <span className="muted">—</span>
          )}
        </Row>
        <Row label={t.drawer.drive.fileId}>
          {message.drive_file_id ? (
            <span className="mono">{message.drive_file_id}</span>
          ) : (
            <span className="muted">—</span>
          )}
        </Row>
        <Row label={t.drawer.drive.uploaded}>
          <span className="mono">{fmtDate(message.processed_at, lang)}</span>
        </Row>
      </dl>

      {hasFile && !iframeError ? (
        <div className="drawer-drive-preview">
          <iframe
            title={t.drawer.drive.previewLabel}
            src={previewUrl}
            className="drawer-drive-preview__iframe"
            onError={() => setIframeError(true)}
            data-testid="drawer-drive-iframe"
          />
        </div>
      ) : (
        <div className="drawer-drive-fallback muted">
          {iframeError
            ? t.drawer.drive.previewFailed
            : t.drawer.drive.noFile}
        </div>
      )}

      {openUrl ? (
        <a href={openUrl} target="_blank" rel="noreferrer noopener" className="btn">
          <IconDrive className="icon sm" />
          {t.drawer.drive.openInDrive} ↗
        </a>
      ) : null}
    </div>
  );
}
