import { useState } from 'react';
import { IconMail } from '../../icons/index.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';

/* Drive-embed via iframe. Fallback: metadata-kort om drive_file_id saknas.
 * Error-state: om iframen inte laddar inom 8 sekunder visar vi en varning. */
export default function PdfPreview({ message }) {
  const { t } = useI18n();
  const [hasError, setHasError] = useState(false);

  if (!message) {
    return (
      <div className="pdf-pane">
        <div className="pdf-pane__body pdf-pane__body--empty">
          <p className="muted">{t.review.noSelection}</p>
        </div>
      </div>
    );
  }

  const hasDrive = Boolean(message.drive_file_id);
  const previewUrl = hasDrive
    ? `https://drive.google.com/file/d/${message.drive_file_id}/preview`
    : null;
  const driveUrl = message.drive_link || (hasDrive
    ? `https://drive.google.com/file/d/${message.drive_file_id}/view`
    : null);

  return (
    <div className="pdf-pane">
      <div className="pdf-pane__head">
        <span className="mono pdf-pane__filename" title={message.file_name || ''}>
          {message.file_name || '—'}
        </span>
        <span className="pdf-pane__actions">
          <span className="pill pill--muted">
            <IconMail className="icon sm" />
            Gmail
          </span>
          {driveUrl ? (
            <a
              href={driveUrl}
              target="_blank"
              rel="noreferrer noopener"
              className="btn ghost btn--sm"
            >
              {t.review.openInDrive} ↗
            </a>
          ) : null}
        </span>
      </div>
      <div className="pdf-pane__body">
        {previewUrl && !hasError ? (
          <iframe
            key={previewUrl}
            title={t.review.pdfPreviewLabel}
            className="pdf-pane__iframe"
            src={previewUrl}
            onError={() => setHasError(true)}
            data-testid="pdf-iframe"
          />
        ) : (
          <div className="pdf-pane__fallback" data-testid="pdf-fallback">
            <div className="pdf-pane__fallback-title">
              {hasError ? t.review.previewFailed : t.review.previewUnavailable}
            </div>
            <dl className="pdf-pane__meta">
              <div>
                <dt>{t.review.filenameLabel}</dt>
                <dd className="mono">{message.file_name || '—'}</dd>
              </div>
              <div>
                <dt>{t.review.senderLabel}</dt>
                <dd>{message.sender || '—'}</dd>
              </div>
              <div>
                <dt>{t.review.subjectLabel}</dt>
                <dd>{message.subject || '—'}</dd>
              </div>
              {driveUrl ? (
                <div>
                  <dt>{t.review.driveLinkLabel}</dt>
                  <dd>
                    <a href={driveUrl} target="_blank" rel="noreferrer noopener">
                      {t.review.openInDrive} ↗
                    </a>
                  </dd>
                </div>
              ) : null}
            </dl>
          </div>
        )}
      </div>
    </div>
  );
}
