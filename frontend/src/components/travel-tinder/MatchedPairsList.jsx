import { useEffect, useMemo, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { api, ApiError } from '../../api/client.js';
import { fmtAmount } from '../../lib/format.js';
import { useToast } from '../../lib/toast.jsx';
import VendorLogo from '../VendorLogo.jsx';

/* FAS 8.5 — Matchade-vyn för Travel Tinder. Höger panel-innehåll när
 * mode='matched'. Visar:
 *  - sökfält + period-dropdown
 *  - stats-banner (total / denna vecka / sparad tid)
 *  - lista med matchade par (kvitto ↔ tx-id + matched_at)
 *  - bekräftelsemodal när användaren klickar "Frikoppla"
 *
 * Klick på en rad öppnar Drawer för det valda meddelandet (kallaren
 * skickar onOpenDrawer-prop). När unmatch lyckas kallas onChanged så
 * parent kan uppdatera båda listorna.
 */

function relativeTime(iso, t) {
  if (!iso) return t.travelTinder.matched.matchedEarlier;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return t.travelTinder.matched.matchedEarlier;
  const diffMs = Date.now() - d.getTime();
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return t.travelTinder.justNow;
  if (min < 60) {
    return t.travelTinder.matched.matchedAt.replace(
      '{time}', `${min} min`,
    );
  }
  const hours = Math.floor(min / 60);
  if (hours < 24) {
    return t.travelTinder.matched.matchedAt.replace(
      '{time}', `${hours} h`,
    );
  }
  const days = Math.floor(hours / 24);
  return t.travelTinder.matched.matchedAt.replace(
    '{time}', `${days} d`,
  );
}

export default function MatchedPairsList({
  data,
  isLoading,
  search,
  setSearch,
  period,
  setPeriod,
  onOpenDrawer,
  onChanged,
}) {
  const { t, lang } = useI18n();
  const toast = useToast();
  const [confirmId, setConfirmId] = useState(null);
  const [busy, setBusy] = useState(false);

  const pairs = data?.pairs || [];
  const stats = data?.stats || {
    total_all_time: 0,
    this_week: 0,
    estimated_minutes_saved: 0,
  };
  const hours = useMemo(
    () => (Math.max(0, stats.estimated_minutes_saved || 0) / 60).toFixed(1),
    [stats.estimated_minutes_saved],
  );

  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape' && confirmId && !busy) setConfirmId(null);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [confirmId, busy]);

  const onUnmatch = async (messageId) => {
    setBusy(true);
    try {
      await api.unmatchReceipt(messageId);
      toast.show({
        kind: 'ok', message: t.travelTinder.matched.unmatchSuccess,
      });
      setConfirmId(null);
      onChanged?.();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.travelTinder.matched.unmatchFailed}: ${detail}`,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tt-matched" data-testid="tt-matched">
      <div className="tt-matched__toolbar">
        <input
          type="search"
          className="tt-search"
          placeholder={t.travelTinder.matched.searchPlaceholder}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          data-testid="tt-matched-search"
          aria-label={t.travelTinder.matched.searchPlaceholder}
        />
        <select
          className="tt-select"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
          data-testid="tt-matched-period"
          aria-label={t.travelTinder.matched.periodLabel}
        >
          <option value="7d">{t.travelTinder.matched.period7d}</option>
          <option value="30d">{t.travelTinder.matched.period30d}</option>
          <option value="90d">{t.travelTinder.matched.period90d}</option>
          <option value="all">{t.travelTinder.matched.periodAll}</option>
        </select>
      </div>

      <div
        className="tt-matched__stats muted mono"
        data-testid="tt-matched-stats"
      >
        {t.travelTinder.matched.statsLine
          .replace('{total}', String(stats.total_all_time || 0))
          .replace('{thisWeek}', String(stats.this_week || 0))
          .replace('{hours}', hours)}
      </div>

      {isLoading ? (
        <div className="muted">{t.common.loading}</div>
      ) : pairs.length === 0 ? (
        <div className="tt-empty-card" data-testid="tt-matched-empty">
          <h3>{t.travelTinder.matched.emptyState}</h3>
        </div>
      ) : (
        <ul className="tt-matched__list" data-testid="tt-matched-list">
          {pairs.map((p) => (
            <li
              key={p.message_id}
              className="tt-matched__row"
              data-testid={`tt-matched-row-${p.message_id}`}
            >
              <button
                type="button"
                className="tt-matched__main"
                onClick={() => onOpenDrawer?.(p)}
                aria-label={p.receipt?.vendor || p.receipt?.file_name || ''}
              >
                <VendorLogo
                  name={p.receipt?.vendor || p.receipt?.sender}
                  size={22}
                />
                <div className="tt-matched__body">
                  <div className="tt-matched__vendor">
                    {p.receipt?.vendor || p.receipt?.file_name || (
                      <span className="muted">—</span>
                    )}
                  </div>
                  <div className="tt-matched__meta mono muted">
                    {p.receipt?.receipt_date || '—'}
                    {p.receipt?.amount != null
                      ? ' · ' +
                        fmtAmount(
                          p.receipt.amount, p.receipt.currency, lang,
                        )
                      : ''}
                  </div>
                  <div className="tt-matched__time muted">
                    {relativeTime(p.matched_at, t)}
                    {p.bezala_transaction_id ? (
                      <>
                        {' · '}
                        <span className="mono">#{p.bezala_transaction_id}</span>
                      </>
                    ) : null}
                  </div>
                </div>
              </button>
              <button
                type="button"
                className="btn ghost tt-matched__unmatch"
                onClick={() => setConfirmId(p.message_id)}
                disabled={busy && confirmId === p.message_id}
                data-testid={`tt-matched-unmatch-${p.message_id}`}
              >
                {t.travelTinder.matched.unmatch}
              </button>
            </li>
          ))}
        </ul>
      )}

      {confirmId ? (
        <div
          className="modal-shell"
          role="dialog"
          aria-modal="true"
          aria-label={t.travelTinder.matched.unmatchConfirmTitle}
          onClick={(e) => {
            if (e.target === e.currentTarget && !busy) setConfirmId(null);
          }}
          data-testid="tt-matched-confirm"
        >
          <div className="modal-card card-pad">
            <h3 className="modal-card__title">
              {t.travelTinder.matched.unmatchConfirmTitle}
            </h3>
            <p className="muted">
              {t.travelTinder.matched.unmatchConfirmText}
            </p>
            <footer className="modal-card__actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setConfirmId(null)}
                disabled={busy}
              >
                {t.common.cancel}
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={() => onUnmatch(confirmId)}
                disabled={busy}
                data-testid="tt-matched-confirm-btn"
              >
                {busy
                  ? t.travelTinder.matched.unmatchBusy
                  : t.travelTinder.matched.unmatchConfirmAction}
              </button>
            </footer>
          </div>
        </div>
      ) : null}
    </div>
  );
}
