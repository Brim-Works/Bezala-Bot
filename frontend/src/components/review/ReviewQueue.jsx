import VendorLogo from '../VendorLogo.jsx';
import Confidence from '../Confidence.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount, fmtRelative } from '../../lib/format.js';
import { displayVendor } from '../../lib/vendorFromSender.js';

/* Vänsterkolumn: scrollbar lista över pending-rader. Vald rad får accent
 * vänsterkant enligt design/src/components.jsx (.q-item.active). */
export default function ReviewQueue({ queue, activeId, onSelect }) {
  const { t, lang } = useI18n();

  return (
    <div className="queue">
      <div className="queue-head">
        <span className="queue-head__title">{t.review.queueTitle}</span>
        <span className="pill pill--warn">
          <span className="pill__dot" aria-hidden="true" />
          <span className="mono">{queue.length}</span>
        </span>
      </div>
      <div className="queue-list" data-testid="review-queue">
        {queue.map((m) => {
          const isActive = m.id === activeId;
          const vendor = displayVendor(m);
          return (
            <button
              key={m.id}
              type="button"
              className={`q-item ${isActive ? 'is-active' : ''}`}
              onClick={() => onSelect(m.id)}
              aria-pressed={isActive}
              data-testid={`queue-item-${m.id}`}
            >
              <VendorLogo name={vendor} size={26} />
              <div className="q-item__body">
                <div className="q-item__vendor">
                  {vendor || <span className="muted">{t.review.unknownVendor}</span>}
                </div>
                <div className="q-item__meta">
                  <span className="mono">{fmtRelative(m.processed_at, lang)}</span>
                  {' · '}
                  <Confidence value={m.ai_confidence} />
                </div>
              </div>
              <div className="q-item__amount mono">
                {m.amount != null ? fmtAmount(m.amount, m.currency, lang) : '—'}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
