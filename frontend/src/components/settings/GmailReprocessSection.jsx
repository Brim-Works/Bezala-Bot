import { useState } from 'react';
import { api, ApiError } from '../../api/client.js';
import { useToast } from '../../lib/toast.jsx';

/* Knapp för att återprocessa Gmail-mail som hittas i fönstret men aldrig
 * fått en ProcessedMessage-rad ("EJ processad" i Match Health).
 *
 * Använder /api/gmail/reprocess: söker Gmail brett (utan has:attachment),
 * filtrerar bort redan-processade message_id:n, och kör pipeline-
 * orkestreringen för resten. Default 30 dagar. */
export default function GmailReprocessSection() {
  const toast = useToast();
  const [days, setDays] = useState(30);
  const [vendorFilter, setVendorFilter] = useState('');
  const [busy, setBusy] = useState(false);
  const [lastResult, setLastResult] = useState(null);

  const onReprocess = async () => {
    setBusy(true);
    setLastResult(null);
    try {
      const result = await api.reprocessGmailWindow({
        days,
        vendorFilter: vendorFilter.trim() || null,
      });
      setLastResult(result);
      toast.show({
        kind: 'ok',
        message: `Klar: ${result.processed ?? 0} sparades, ` +
          `${result.failed ?? 0} fel, ${result.skipped ?? 0} filtrerade ` +
          `(av ${result.found ?? 0} ej processade).`,
      });
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `Reprocess misslyckades: ${detail}` });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="settings-section" data-testid="gmail-reprocess">
      <header className="settings-section__head">
        <h2 className="settings-section__title">
          Återprocessa Gmail
        </h2>
        <p className="settings-section__lead muted">
          Sök Gmail bakåt i tiden och kör pipelinen på mail som aldrig
          fick en ProcessedMessage-rad (t.ex. tidigare missade pga
          has:attachment-bugg eller html_to_pdf-fel).
        </p>
      </header>

      <div className="settings-cleanup-row">
        <label className="muted" htmlFor="gmail-reprocess-days">
          Antal dagar bakåt
          <input
            id="gmail-reprocess-days"
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(e) => setDays(Number(e.target.value) || 30)}
            disabled={busy}
            data-testid="gmail-reprocess-days"
            style={{ marginLeft: 8, width: 80 }}
          />
        </label>
        <label className="muted" htmlFor="gmail-reprocess-vendor">
          Vendor-filter (valfri)
          <input
            id="gmail-reprocess-vendor"
            type="text"
            placeholder="t.ex. lovable"
            value={vendorFilter}
            onChange={(e) => setVendorFilter(e.target.value)}
            disabled={busy}
            data-testid="gmail-reprocess-vendor"
            style={{ marginLeft: 8 }}
          />
        </label>
        <button
          type="button"
          className="btn primary"
          onClick={onReprocess}
          disabled={busy}
          data-testid="gmail-reprocess-trigger"
        >
          {busy
            ? 'Återprocessar…'
            : `Återprocessa ej processade Gmail (senaste ${days} dagar)`}
        </button>
      </div>

      {lastResult ? (
        <div className="muted" data-testid="gmail-reprocess-summary">
          Hittade {lastResult.found ?? 0} ej processade mail.
          {' '}Sparades: {lastResult.processed ?? 0}.
          {' '}Fel: {lastResult.failed ?? 0}.
          {' '}Filtrerade: {lastResult.skipped ?? 0}.
        </div>
      ) : null}
    </section>
  );
}
