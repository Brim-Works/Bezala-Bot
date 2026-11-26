import { useEffect, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { useRouter } from '../router/useRouter.jsx';
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
  const [writes, setWrites] = useState([]);
  const [amountFix, setAmountFix] = useState(null);
  const [distanceFix, setDistanceFix] = useState(null);

  const status = message?.bezala_status;
  const msgId = message?.id;
  const hasTx = Boolean(message?.bezala_transaction_id);

  // C60 — botens skrivningar till utkastet (före/efter).
  useEffect(() => {
    let alive = true;
    if (!msgId || !hasTx || !api.messageBezalaWrites) return undefined;
    api.messageBezalaWrites(msgId)
      .then((r) => {
        if (alive) {
          setWrites(r?.writes || []);
          setAmountFix(r?.amount_fix || null);
          setDistanceFix(r?.distance_fix || null);
        }
      })
      .catch(() => { if (alive) { setWrites([]); setAmountFix(null); setDistanceFix(null); } });
    return () => { alive = false; };
  }, [msgId, hasTx]);

  if (!message) return null;

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
    navigate('/travel-tinder?mode=review');
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

      {status === 'unverified' ? (
        <div
          className="drawer-banner drawer-banner--warn"
          data-testid="bezala-banner-unverified"
        >
          <IconBezala className="icon" />
          <div className="drawer-banner__body">
            <div className="drawer-banner__title">{t.drawer.bezala.unverifiedTitle}</div>
            <p>{t.drawer.bezala.unverifiedBody}</p>
            {message.bezala_error_message ? (
              <pre className="drawer-banner__error mono">
                {message.bezala_error_message}
              </pre>
            ) : null}
            <dl className="drawer-kv-list">
              <Row label={t.drawer.bezala.transactionId}>
                <span className="mono">{message.bezala_transaction_id || '—'}</span>
              </Row>
            </dl>
          </div>
        </div>
      ) : null}

      {amountFix ? (
        <div className="drawer-banner" data-testid="bezala-amount-fix">
          <div className="drawer-banner__body">
            <div className="drawer-banner__title">{t.drawer.bezala.amountFixTitle}</div>
            <p>
              {t.drawer.bezala.amountFixBefore}: <span className="mono">{amountFix.before}</span>
              {' → '}
              {t.drawer.bezala.amountFixAfter}: <span className="mono">{amountFix.after}</span>
              {amountFix.when ? ` (${amountFix.when.slice(0, 16).replace('T', ' ')})` : ''}
            </p>
          </div>
        </div>
      ) : null}

      {distanceFix ? (
        <div className="drawer-banner" data-testid="bezala-distance-fix">
          <div className="drawer-banner__body">
            <div className="drawer-banner__title">{t.drawer.bezala.distanceFixTitle}</div>
            {distanceFix.fields.map((f) => (
              <p key={f.field}>
                {t.drawer.bezala.distanceField[f.field] || f.field}:{' '}
                <span className="mono">{f.before || t.drawer.bezala.empty}</span>
                {' → '}
                <span className="mono">{f.after || t.drawer.bezala.empty}</span>
              </p>
            ))}
            {distanceFix.emissions_after ? (
              <p>
                {t.drawer.bezala.emissions}:{' '}
                <span className="mono">{distanceFix.emissions_before ?? '–'} kg</span>
                {' → '}
                <span className="mono">{distanceFix.emissions_after} kg</span>
              </p>
            ) : null}
          </div>
        </div>
      ) : null}

      {writes.length ? (
        <div className="drawer-kv-list" data-testid="bezala-bot-writes">
          <div className="drawer-banner__title">{t.drawer.bezala.writesTitle}</div>
          {writes.map((w, i) => (
            <Row key={`${w.when}-${i}`} label={`${(w.when || '').slice(0, 16)} ${w.operation}`}>
              <span className="mono">
                {w.outcome}
                {w.fields ? ` · ${Object.keys(w.fields).join(', ')}` : ''}
                {w.before && w.after
                  ? ` · ${Object.keys(w.fields || {}).map((k) => { const v = k === 'vat_lines_attributes' ? 'vat_lines' : k; return `${v}: ${JSON.stringify(w.before[v] ?? null)} → ${JSON.stringify(w.after[v] ?? null)}`; }).join('; ')}`
                  : ''}
              </span>
            </Row>
          ))}
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
