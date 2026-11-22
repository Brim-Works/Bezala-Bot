import { useRef, useState } from 'react';
import { api } from '../api/client.js';
import { shrinkImage } from './imageResize.js';
import { day, errorText, money } from './format.js';
import ReceiptThumb from './ReceiptThumb.jsx';

/* C47 Del 1 — "Fota kvitto". En kö av uppladdningar; varje kvitto får ett
 * resultatkort med föreslagen kortrad/resa och knapparna Rätt / Annan
 * kortrad / Privat / Spara utan koppling. */

let seq = 0;

export default function UploadView() {
  const inputRef = useRef(null);
  const [items, setItems] = useState([]);

  const update = (key, patch) =>
    setItems((prev) => prev.map((it) => (it.key === key ? { ...it, ...patch } : it)));

  async function onFiles(e) {
    const files = Array.from(e.target.files || []);
    e.target.value = '';
    for (const f of files) {
      const key = ++seq;
      setItems((prev) => [{ key, name: f.name, state: 'uploading' }, ...prev]);
      try {
        const small = await shrinkImage(f);
        const res = await api.mobileUpload(small);
        update(key, { state: 'done', data: res });
      } catch (err) {
        update(key, { state: 'error', error: errorText(err) });
      }
    }
  }

  return (
    <div className="m-upload">
      <input
        ref={inputRef}
        type="file"
        accept="image/*,application/pdf"
        capture="environment"
        multiple
        hidden
        onChange={onFiles}
        data-testid="m-file"
      />
      <button type="button" className="m-bigbtn" onClick={() => inputRef.current?.click()}>
        <span className="m-bigbtn__icon" aria-hidden="true">📷</span>
        Fota kvitto
      </button>
      <p className="m-hint">Kvittot tolkas och kopplas till kortraden. Finns ingen kortrad än sparas det och kopplas när transaktionen dyker upp.</p>
      <div className="m-list">
        {items.map((it) => (
          <ResultCard key={it.key} item={it} onChange={(data) => update(it.key, { data })} />
        ))}
      </div>
    </div>
  );
}

function ResultCard({ item, onChange }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [lines, setLines] = useState(null);

  if (item.state === 'uploading') {
    return <div className="m-card m-card--pending">Läser {item.name || 'kvitto'} …</div>;
  }
  if (item.state === 'error') {
    return <div className="m-card m-card--error">Kunde inte spara {item.name}: {item.error}</div>;
  }
  const { receipt, suggestion, status, trip } = item.data;
  const mid = receipt.message_id;

  const act = async (fn) => {
    setBusy(true);
    setError('');
    try {
      onChange(await fn());
      setLines(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const openLines = async () => {
    setError('');
    try {
      setLines((await api.mobileCardLines(mid)).card_lines);
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="m-card">
      <div className="m-card__row">
        <ReceiptThumb receipt={receipt} size="sm" />
        <div className="m-card__main">
          <div className="m-card__title">{receipt.vendor || 'Okänd leverantör'}</div>
          <div className="m-card__amount">{money(receipt.amount, receipt.currency)}</div>
          <div className="m-muted">{day(receipt.date)}</div>
          {receipt.review_flag === 'date_corrected' && (
            <div className="m-flag">Datumet rättades – kontrollera</div>
          )}
        </div>
      </div>
      {trip && <div className="m-muted">Resa: {trip.destination} {trip.dates}</div>}

      {status === 'coupled' && <div className="m-ok">Kopplat till kortraden ✓</div>}
      {status === 'private' && <div className="m-muted">Markerat som privat.</div>}
      {status === 'waiting' && (
        <div className="m-ok">Sparat – väntar på korttransaktion</div>
      )}
      {status === 'suggested' && suggestion && (
        <div className="m-suggest">
          <div className="m-muted">Föreslagen kortrad</div>
          <div>{suggestion.description}</div>
          <div>
            {money(suggestion.amount, suggestion.currency)} · {day(suggestion.date)}
            {suggestion.conversion && (
              <span className="m-muted">
                {' '}({money(suggestion.conversion.from_amount, suggestion.conversion.from_currency)} ≈{' '}
                {money(suggestion.conversion.to_amount, suggestion.conversion.to_currency)})
              </span>
            )}
          </div>
        </div>
      )}

      {status !== 'coupled' && status !== 'private' && (
        <div className="m-actions">
          {suggestion && (
            <button type="button" className="m-btn m-btn--yes" disabled={busy}
              onClick={() => act(() => api.mobileCouple(mid, suggestion.bill_line_id))}>
              Rätt
            </button>
          )}
          <button type="button" className="m-btn" disabled={busy} onClick={openLines}>
            Annan kortrad
          </button>
          <button type="button" className="m-btn" disabled={busy}
            onClick={() => act(() => api.mobilePrivate(mid, suggestion?.bill_line_id))}>
            Privat
          </button>
          {suggestion && (
            <button type="button" className="m-btn" disabled={busy}
              onClick={() => act(() => api.mobileKeep(mid, suggestion.bill_line_id))}>
              Spara utan koppling
            </button>
          )}
        </div>
      )}
      {lines && (
        <div className="m-linelist">
          {lines.length === 0 && <div className="m-muted">Inga öppna kortrader just nu.</div>}
          {lines.slice(0, 30).map((l) => (
            <button type="button" key={l.bill_line_id} className="m-lineitem" disabled={busy}
              onClick={() => act(() => api.mobileCouple(mid, l.bill_line_id))}>
              <span>{l.description}</span>
              <span className="m-muted">{money(l.amount, l.currency)} · {day(l.date)}</span>
            </button>
          ))}
          <button type="button" className="m-btn m-btn--ghost" onClick={() => setLines(null)}>Stäng</button>
        </div>
      )}
      {error && <div className="m-error">{error}</div>}
    </div>
  );
}
