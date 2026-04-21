import { useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { useRouter } from '../router/useRouter.jsx';
import { routeForView } from '../routes.js';
import { IconBezala } from '../icons/index.jsx';

function Row({ label, children }) {
  return (
    <div className="drawer-kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/* Status-beroende banner:
 *  pending     → gul + CTA "Öppna granskning"
 *  transferred → grön + transaktions-ID + "Öppna i Bezala"
 *  error       → röd + meddelande + "Försök igen"-knapp
 *  na          → grå + förklaring
 *
 * "Försök igen" anropar POST /upload-to-bezala och refetcha:r (inte
 * optimistic) — drawern behåller samma message-snapshot tills
 * parent-komponenten levererar uppdaterad data. */
export default function BezalaTab({ message, onRefetch, onClose }) {
  const { t } = useI18n();
  const toast = useToast();
  const { navigate } = useRouter();
  const [retrying, setRetrying] = useState(false);

  if (!message) return null;

  const status = message.bezala_status;

  async function retry() {
    setRetrying(true);
    try {
      await api.uploadToBezala(message.id);
      toast.show({ kind: 'ok', message: t.drawer.bezala.retrySucceeded });
      onRefetch?.();
    } catch (err) {
      toast.show({
        kind: 'err',
        message: `${t.drawer.bezala.retryFailed}: ${err.message || err}`,
      });
    } finally {
      setRetrying(false);
    }
  }

  function openReview() {
    onClose?.();
    navigate(routeForView('review'));
  }

  return (
    <div className="drawer-section" data-testid="drawer-tab-bezala-content">
      {status === 'pending' ? (
        <div
          className="drawer-banner drawer-banner--warn"
          data-testid="bezala-banner-pending"
        >
          <IconBezala className="icon" />
          <div className="drawer-banner__body">
            <div className="drawer-banner__title">{t.drawer.bezala.pendingTitle}</div>
            <p>{t.drawer.bezala.pendingBody}</p>
            <button type="button" className="btn primary" onClick={openReview}>
              {t.drawer.bezala.openReview} →
            </button>
          </div>
        </div>
      ) : null}

      {status === 'transferred' ? (
        <div
          className="drawer-banner drawer-banner--ok"
          data-testid="bezala-banner-transferred"
        >
          <IconBezala className="icon" />
          <div className="drawer-banner__body">
            <div className="drawer-banner__title">
              {t.drawer.bezala.transferredTitle}
            </div>
            <p>{t.drawer.bezala.transferredBody}</p>
            <dl className="drawer-kv-list">
              <Row label={t.drawer.bezala.transactionId}>
                <span className="mono">
                  {message.bezala_transaction_id || '—'}
                </span>
              </Row>
            </dl>
          </div>
        </div>
      ) : null}

      {status === 'error' ? (
        <div
          className="drawer-banner drawer-banner--err"
          data-testid="bezala-banner-error"
        >
          <IconBezala className="icon" />
          <div className="drawer-banner__body">
            <div className="drawer-banner__title">{t.drawer.bezala.errorTitle}</div>
            <p>{t.drawer.bezala.errorBody}</p>
            {message.bezala_error_message ? (
              <pre className="drawer-banner__error mono">
                {message.bezala_error_message}
              </pre>
            ) : null}
            <button
              type="button"
              className="btn primary"
              onClick={retry}
              disabled={retrying}
              data-testid="bezala-retry"
            >
              {retrying ? t.drawer.bezala.retrying : t.drawer.bezala.retry}
            </button>
          </div>
        </div>
      ) : null}

      {status === 'na' || !status ? (
        <div
          className="drawer-banner drawer-banner--muted"
          data-testid="bezala-banner-na"
        >
          <IconBezala className="icon" />
          <div className="drawer-banner__body">
            <div className="drawer-banner__title">{t.drawer.bezala.naTitle}</div>
            <p>{t.drawer.bezala.naBody}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}
