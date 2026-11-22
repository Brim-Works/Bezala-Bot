import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api/client.js';
import { day, errorText, money } from './format.js';
import ReceiptThumb from './ReceiptThumb.jsx';

/* C47 Del 3 — Travel Tinder: tre flikar, samma i mobil och desktop.
 *   Att besluta = svep-kön (höger ja, vänster nej, upp senare, ångra)
 *   Matchade    = kopplade kvitto ↔ kortrad + godkända resor
 *   Väntar      = kvitton som väntar på korttransaktion
 */

const TABS = [
  { key: 'decide', label: 'Att besluta' },
  { key: 'matched', label: 'Matchade' },
  { key: 'waiting', label: 'Väntar' },
];

const KIND_LABELS = {
  receipt: { tag: 'Kvitto ↔ kortrad', yes: 'Koppla', no: 'Inte detta par' },
  trip: { tag: 'Resa', yes: 'Godkänn resan', no: 'Ingen tjänsteresa' },
  sub: { tag: 'Prenumeration?', yes: 'Ja, sköt automatiskt', no: 'Ingen prenumeration' },
  subpriv: { tag: 'Privat prenumeration?', yes: 'Privat – markera som privat utlägg', no: 'Företagsprenumeration' },
  subdev: { tag: 'Prenumeration – nytt belopp', yes: 'OK, nytt pris', no: 'Hantera själv' },
  subnr: { tag: 'Prenumeration – inget kvitto', yes: 'Kvitto kommer inte', no: 'Vänta mer' },
  dup: { tag: 'Dubbeldragning?', yes: 'Alla är riktiga köp', no: 'Dubbel – väntar återbetalning' },
  notxn: { tag: 'Kvitto utan transaktion', yes: 'Privat / annat kort', no: 'Vänta mer' },
  refund: { tag: 'Återbetalning saknas', yes: 'Klart – stäng', no: 'Vänta mer' },
};

export default function DecisionTabs({ initialTab = 'decide', variant = 'mobile', hideTabs = false }) {
  const [tab, setTab] = useState(initialTab);
  const [count, setCount] = useState(null);
  return (
    <div className={`m-tabs-wrap m-tabs-wrap--${variant}`}>
      {!hideTabs && (
      <div className="m-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={`m-tab ${tab === t.key ? 'is-active' : ''}`} onClick={() => setTab(t.key)}>
            {t.label}
            {t.key === 'decide' && count !== null && <span className="m-badge">{count}</span>}
          </button>
        ))}
      </div>
      )}
      {tab === 'decide' && <SwipeQueue onCount={setCount} variant={variant} />}
      {tab === 'matched' && <MatchedList />}
      {tab === 'waiting' && <WaitingList />}
    </div>
  );
}

// ---------------------------------------------------------------- svep-kön

function SwipeQueue({ onCount, variant }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState('');

  const load = useCallback(async () => {
    try {
      const d = await api.queue();
      setData(d);
      onCount?.(d.count);
      setError('');
    } catch (err) {
      setError(errorText(err));
    }
  }, [onCount]);

  useEffect(() => {
    load();
  }, [load]);

  const decide = async (item, decision) => {
    if (busy) return;
    setBusy(true);
    setError('');
    // Optimistiskt: ta bort kortet direkt.
    setData((d) => d && { ...d, items: d.items.filter((i) => i.id !== item.id) });
    try {
      await api.queueDecide(item.id, decision);
      const labels = KIND_LABELS[item.kind] || {};
      setToast(decision === 'yes' ? labels.yes || 'Ja' : decision === 'no' ? labels.no || 'Nej' : 'Senare');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      load();
    }
  };

  const undo = async () => {
    setBusy(true);
    try {
      const r = await api.queueUndo();
      setToast(`Ångrat: ${r.undone}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      load();
    }
  };

  if (!data && !error) return <div className="m-muted m-pad">Laddar kön …</div>;
  const items = data?.items || [];
  const current = items[0];
  return (
    <div className="m-queue">
      {error && <div className="m-error">{error}</div>}
      {toast && <div className="m-toast" onAnimationEnd={() => setToast('')}>{toast}</div>}
      {current ? (
        variant === 'desktop' ? (
          <div className="m-desk-queue">
            {items.slice(0, 12).map((it) => (
              <QueueCard key={it.id} item={it} busy={busy} onDecide={(d) => decide(it, d)} />
            ))}
          </div>
        ) : (
          <SwipeCard key={current.id} item={current} busy={busy} onDecide={(d) => decide(current, d)} />
        )
      ) : (
        <div className="m-empty">Inget att besluta just nu 🎉</div>
      )}
      <div className="m-queue__footer">
        <span className="m-muted">{items.length} kvar</span>
        <button type="button" className="m-btn m-btn--ghost" disabled={busy || !data?.last_decision}
          onClick={undo}>
          ↶ Ångra
        </button>
      </div>
    </div>
  );
}

function SwipeCard({ item, busy, onDecide }) {
  const ref = useRef(null);
  const start = useRef(null);
  const [delta, setDelta] = useState({ x: 0, y: 0 });

  const onDown = (e) => {
    if (e.target.closest('button, a')) return;
    start.current = { x: e.clientX, y: e.clientY };
    ref.current?.setPointerCapture?.(e.pointerId);
  };
  const onMove = (e) => {
    if (!start.current) return;
    setDelta({ x: e.clientX - start.current.x, y: e.clientY - start.current.y });
  };
  const onUp = () => {
    if (!start.current) return;
    start.current = null;
    const { x, y } = delta;
    setDelta({ x: 0, y: 0 });
    if (x > 100) onDecide('yes');
    else if (x < -100) onDecide('no');
    else if (y < -100) onDecide('later');
  };
  const hint = delta.x > 40 ? 'yes' : delta.x < -40 ? 'no' : delta.y < -40 ? 'later' : '';
  return (
    <div
      ref={ref}
      className={`m-swipe ${hint ? `m-swipe--${hint}` : ''}`}
      style={{ transform: `translate(${delta.x}px, ${Math.min(0, delta.y)}px) rotate(${delta.x / 20}deg)` }}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      <QueueCard item={item} busy={busy} onDecide={onDecide} />
    </div>
  );
}

function QueueCard({ item, busy, onDecide }) {
  const labels = KIND_LABELS[item.kind] || {};
  return (
    <div className={`m-card m-qcard m-qcard--${item.kind}`}>
      <div className="m-tag">{labels.tag || item.kind}{item.later ? ' · senare' : ''}</div>
      <div className="m-card__title">{item.title}</div>
      <div className="m-muted">{item.subtitle}</div>
      {item.warning && (
        <div className="m-warning" role="alert" data-testid="queue-card-warning">⚠ {item.warning}</div>
      )}
      <CardBody item={item} />
      {item.kind === 'receipt' && (item.account || item.description_to_paste) && (
        <div className="m-muted" data-testid="queue-card-bezala-fill">
          {item.account && !item.account.missing && (
            <div>Konto: {item.account.name || item.account.account_id}
              {item.account.source ? ` (${item.account.source})` : ''}</div>
          )}
          {item.description_to_paste && (
            <div>Beskrivning att klistra in i Bezala: <code>{item.description_to_paste}</code></div>
          )}
        </div>
      )}
      <div className="m-actions m-actions--decide">
        <button type="button" className="m-btn m-btn--no" disabled={busy} onClick={() => onDecide('no')}>
          ← {labels.no || item.no_label}
        </button>
        <button type="button" className="m-btn m-btn--ghost" disabled={busy} onClick={() => onDecide('later')}>
          ↑ Senare
        </button>
        <button type="button" className="m-btn m-btn--yes" disabled={busy || item.blocked}
          title={item.blocked ? item.warning : undefined} onClick={() => onDecide('yes')}>
          {labels.yes || item.yes_label} →
        </button>
      </div>
    </div>
  );
}

function CardBody({ item }) {
  if (item.kind === 'receipt') {
    const r = item.receipt;
    const c = item.card_line;
    return (
      <div className="m-pair">
        <ReceiptThumb receipt={r} />
        <div className="m-pair__sides">
          <div>
            <div className="m-muted">Kvitto</div>
            <div>{r.vendor || '—'}</div>
            <div className="m-card__amount">{money(r.amount, r.currency)}</div>
            <div className="m-muted">{day(r.date)}</div>
          </div>
          <div>
            <div className="m-muted">Kortrad</div>
            <div>{c.description}</div>
            <div className="m-card__amount">{money(c.amount, c.currency)}</div>
            <div className="m-muted">{day(c.date)}</div>
          </div>
          {item.conversion && (
            <div className="m-muted m-pair__conv">
              {money(item.conversion.from_amount, item.conversion.from_currency)} ≈{' '}
              {money(item.conversion.to_amount, item.conversion.to_currency)} (kurs {item.conversion.rate})
            </div>
          )}
          {item.trip && <div className="m-muted">Resa: {item.trip.destination} {item.trip.dates}</div>}
        </div>
      </div>
    );
  }
  if (item.kind === 'trip') {
    const t = item.trip;
    return (
      <dl className="m-dl">
        <dt>Ort</dt><dd>{t.destination || '—'} {t.country_name ? `(${t.country_name})` : ''}</dd>
        <dt>Datum</dt><dd>{t.start_date} – {t.end_date}</dd>
        <dt>Tider</dt><dd>{t.travel_start_helsinki || '—'} → {t.travel_end_helsinki || '—'}</dd>
        <dt>Syfte</dt><dd>{t.purpose || '—'}</dd>
        {t.flags?.length > 0 && (<><dt>Flaggor</dt><dd>{t.flags.join(', ')}</dd></>)}
      </dl>
    );
  }
  if (item.receipt) return <ReceiptThumb receipt={item.receipt} />;
  if (item.subscription) {
    const s = item.subscription;
    return (
      <div className="m-muted">
        Beskrivning: {s.example_description}
        {s.flags?.includes('account_missing') && <div className="m-flag">Konto saknas – väljs inte automatiskt</div>}
      </div>
    );
  }
  return null;
}

// ---------------------------------------------------------------- Matchade

function MatchedList() {
  const [month, setMonth] = useState('');
  const [tripId, setTripId] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(null);

  const load = useCallback(async () => {
    try {
      setData(await api.queueMatched({ month, tripId }));
    } catch (err) {
      setError(errorText(err));
    }
  }, [month, tripId]);

  useEffect(() => {
    load();
  }, [load]);

  const unmatch = async (p) => {
    setError('');
    try {
      await api.queueUnmatch(p.message_id);
      setOpen(null);
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };
  const unapprove = async (t) => {
    setError('');
    try {
      await api.queueUnapproveTrip(t.id);
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="m-matched">
      <div className="m-filters">
        <select value={month} onChange={(e) => setMonth(e.target.value)} aria-label="Månad">
          <option value="">Alla månader</option>
          {(data?.months || []).map((m) => <option key={m} value={m}>{m}</option>)}
        </select>
        <select value={tripId} onChange={(e) => setTripId(e.target.value)} aria-label="Resa">
          <option value="">Alla resor</option>
          {(data?.trips || []).map((t) => (
            <option key={t.id} value={t.id}>{t.destination} {t.dates}</option>
          ))}
        </select>
      </div>
      {error && <div className="m-error">{error}</div>}
      {!data && <div className="m-muted m-pad">Laddar …</div>}
      {data && data.pairs.length === 0 && data.approved_trips.length === 0 && (
        <div className="m-empty">Inget matchat i urvalet.</div>
      )}
      {data?.pairs.map((p) => (
        <button type="button" key={p.message_id} className="m-row" onClick={() => setOpen(p)}>
          <span className="m-row__main">
            <strong>{p.receipt.vendor || '—'}</strong>
            <span className="m-muted">{p.payment.merchant || ''}</span>
          </span>
          <span className="m-row__side">
            {money(p.payment.amount ?? p.receipt.amount, p.payment.currency || p.receipt.currency)}
            <span className="m-muted">{day(p.date)}</span>
          </span>
        </button>
      ))}
      {data?.approved_trips.length > 0 && <h3 className="m-h3">Godkända resor</h3>}
      {data?.approved_trips.map((t) => (
        <div key={t.id} className="m-row">
          <span className="m-row__main">
            <strong>{t.destination}</strong>
            <span className="m-muted">{t.start_date} – {t.end_date}</span>
          </span>
          <button type="button" className="m-btn m-btn--ghost" onClick={() => unapprove(t)}>Koppla loss</button>
        </div>
      ))}
      {open && (
        <div className="m-sheet" role="dialog" aria-modal="true" onClick={() => setOpen(null)}>
          <div className="m-sheet__body" onClick={(e) => e.stopPropagation()}>
            <ReceiptThumb receipt={{ ...open.receipt, message_id: open.message_id }} size="lg" />
            <dl className="m-dl">
              <dt>Kvitto</dt>
              <dd>{open.receipt.vendor} · {money(open.receipt.amount, open.receipt.currency)} · {day(open.receipt.receipt_date)}</dd>
              <dt>Kortrad</dt>
              <dd>{open.payment.merchant || '—'} · {money(open.payment.amount, open.payment.currency)} · {day(open.payment.date)}</dd>
              <dt>Resa</dt>
              <dd>{open.trip ? `${open.trip.destination} ${open.trip.dates}` : '—'}</dd>
            </dl>
            <div className="m-actions">
              <button type="button" className="m-btn m-btn--no" onClick={() => unmatch(open)}>Koppla loss</button>
              <button type="button" className="m-btn m-btn--ghost" onClick={() => setOpen(null)}>Stäng</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Väntar

function WaitingList() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => {
    api.queueWaiting().then(setData).catch((err) => setError(errorText(err)));
  }, []);
  if (error) return <div className="m-error">{error}</div>;
  if (!data) return <div className="m-muted m-pad">Laddar …</div>;
  if (data.receipts.length === 0) return <div className="m-empty">Inga kvitton väntar.</div>;
  return (
    <div className="m-waiting">
      {data.receipts.map((r) => (
        <div key={r.message_id} className="m-row">
          <ReceiptThumb receipt={r} size="sm" />
          <span className="m-row__main">
            <strong>{r.vendor || 'Okänd'}</strong>
            <span className="m-muted">
              {day(r.date)} · {r.source === 'mobile' ? 'mobil' : r.source === 'mail_kvitto' ? '+kvitto-mail' : 'Gmail'}
            </span>
          </span>
          <span className="m-row__side">
            {money(r.amount, r.currency)}
            <span className={r.waiting_status === 'no_transaction' ? 'm-flag' : 'm-muted'}>
              {r.waiting_status === 'no_transaction' ? 'ingen transaktion' : `${r.waiting_days ?? 0} d`}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}
