import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';
import VendorLogo from '../VendorLogo.jsx';

/* Vänster panel — saknade Bezala-korttransaktioner.
 * Klick markerar raden (lila border + bg). Footer visar matchat/totalt. */
export default function MissingPaymentsList({
  rows,
  selectedId,
  onSelect,
  matchedCount,
  totalCount,
  isLoading,
}) {
  const { t, lang } = useI18n();

  return (
    <aside className="tt-payments" data-testid="tt-payments">
      <div className="tt-payments__head">
        <span className="tt-payments__head-label">
          {t.travelTinder.paymentsHeader}
        </span>
        <span className="pill pill--warn">
          <span className="pill__dot" aria-hidden="true" />
          <span className="mono">{rows.length}</span>
        </span>
      </div>

      {isLoading ? (
        <div className="muted" data-testid="tt-payments-loading">
          {t.common.loading}
        </div>
      ) : rows.length === 0 ? (
        <div className="tt-empty-card" data-testid="tt-payments-empty">
          <h3>{t.travelTinder.empty.allMatched}</h3>
          <p className="muted">
            {t.travelTinder.empty.allMatchedBody.replace('{minutes}', '8')}
          </p>
        </div>
      ) : (
        <div className="tt-payments__list" data-testid="tt-payments-list">
          {rows.map((row) => {
            const m = row.missing_receipt;
            const isActive = m.id === selectedId;
            return (
              <button
                key={m.id}
                type="button"
                className={`tt-payment ${isActive ? 'is-active' : ''}`}
                onClick={() => onSelect(m.id)}
                aria-pressed={isActive}
                data-testid={`tt-payment-${m.id}`}
              >
                <VendorLogo name={m.description} size={24} />
                <div className="tt-payment__body">
                  <div className="tt-payment__vendor">
                    {m.description || (
                      <span className="muted">{t.match.unknownVendor}</span>
                    )}
                  </div>
                  <div className="tt-payment__meta mono muted">
                    {m.date || '—'}
                  </div>
                </div>
                <div className="tt-payment__amount mono">
                  {m.amount != null
                    ? fmtAmount(m.amount, m.currency, lang)
                    : '—'}
                </div>
              </button>
            );
          })}
        </div>
      )}

      <div className="tt-payments__foot mono muted" data-testid="tt-matched-foot">
        {t.travelTinder.matchedFooter
          .replace('{matched}', String(matchedCount))
          .replace('{total}', String(totalCount))}
      </div>
    </aside>
  );
}
