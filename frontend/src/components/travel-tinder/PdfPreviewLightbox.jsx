import { useEffect } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';

/* Lightbox för Drive-PDF-preview via iframe. Esc/click-utanför stänger.
 * Saknar Drive-fil → visar "PDF saknas". */
export default function PdfPreviewLightbox({ message, onClose }) {
  const { t } = useI18n();

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const src = message?.drive_file_id
    ? `https://drive.google.com/file/d/${encodeURIComponent(
        message.drive_file_id,
      )}/preview`
    : null;

  return (
    <div
      className="tt-lightbox-overlay"
      role="presentation"
      onClick={onClose}
      data-testid="tt-lightbox-overlay"
    >
      <div
        className="tt-lightbox"
        role="dialog"
        aria-modal="true"
        aria-label={t.travelTinder.pdfPreview}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="tt-lightbox__head">
          <span className="mono">
            {message?.file_name || t.travelTinder.pdfPreview}
          </span>
          <button
            type="button"
            className="btn ghost"
            onClick={onClose}
            data-testid="tt-lightbox-close"
            aria-label={t.travelTinder.closePreview}
          >
            ×
          </button>
        </header>
        <div className="tt-lightbox__body">
          {src ? (
            <iframe
              title={t.travelTinder.pdfPreview}
              src={src}
              className="tt-lightbox__iframe"
              data-testid="tt-lightbox-iframe"
            />
          ) : (
            <div className="muted tt-lightbox__missing">
              {t.travelTinder.pdfMissing}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
