import { useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { fmtDate } from '../lib/format.js';
import { IconDrive } from '../icons/index.jsx';

function Row({ label, children }) {
  return (
    <div className="drawer-kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export default function DriveTab({ message }) {
  const { t, lang } = useI18n();
  const [iframeError, setIframeError] = useState(false);
  if (!message) return null;

  const hasFile = Boolean(message.drive_file_id);
  const previewUrl = hasFile
    ? `https://drive.google.com/file/d/${message.drive_file_id}/preview`
    : null;
  const openUrl = message.drive_link || (hasFile
    ? `https://drive.google.com/file/d/${message.drive_file_id}/view`
    : null);

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
