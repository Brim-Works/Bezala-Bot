import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';
import VendorLogo from '../VendorLogo.jsx';

/* Vänster panel — saknade Bezala-korttransaktioner + mode-toggle.
 * Klick markerar raden (lila border + bg). Footer visar matchat/totalt.
 *
 * FAS 8.5 — `mode`-prop växlar mellan "Att matcha" (default) och
 * "Matchade". I matchade-läget döljs payment-listan; en kompakt
 * info-rad visas istället eftersom innehållet flyttas till höger panel.
 */
export default function MissingPaymentsList({
  rows,
  selectedId,
  onSelect,
  matchedCount,
  totalCount,
  isLoading,
  // FAS 8.5
  mode = 'unmatched',
  onModeChange,
  unmatchedCount,
  matchedTotalCount,
}) {
  const { t, lang } = useI18n();

  const isMatchedMode = mode === 'matched';

  return (
    <aside className="tt-payments" data-testid="tt-payments">
      {onModeChange ? (
        <div className="tt-mode-toggle" role="tablist" data-testid="tt-mode-toggle">
          <button
            type="button"
            role="tab"
            aria-selected={!isMatchedMode}
            className={`tt-mode-toggle__btn ${!isMatchedMode ? 'is-active' : ''}`}
            onClick={() => onModeChange('unmatched')}
            data-testid="tt-mode-unmatched"
          >
            {t.travelTinder.mode.unmatched}
            {unmatchedCount != null ? (
              <span className="tt-mode-toggle__count mono">
                {unmatchedCount}
              </span>
            ) : null}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={isMatchedMode}
            className={`tt-mode-toggle__btn ${isMatchedMode ? 'is-active' : ''}`}
            onClick={() => onModeChange('matched')}
            data-testid="tt-mode-matched"
          >
            {t.travelTinder.mode.matched}
            {matchedTotalCount != null ? (
              <span className="tt-mode-toggle__count mono">
                {matchedTotalCount}
              </span>
            ) : null}
          </button>
        </div>
      ) : null}

      {isMatchedMode ? (
        <div className="tt-payments__matched-info muted" data-testid="tt-matched-info">
          {t.travelTinder.matched.leftPanelInfo}
        </div>
      ) : (
        <>
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

          <div
            className="tt-payments__foot mono muted"
            data-testid="tt-matched-foot"
          >
            {t.travelTinder.matchedFooter
              .replace('{matched}', String(matchedCount))
              .replace('{total}', String(totalCount))}
          </div>
        </>
      )}
    </aside>
  );
}
