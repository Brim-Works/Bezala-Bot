import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api, ApiError, request } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import TripSuggestionCard from '../components/trips/TripSuggestionCard.jsx';
import CalendarTripRow from '../components/trips/CalendarTripRow.jsx';
import TripActiveCard from '../components/trips/TripActiveCard.jsx';
import TripDetailDrawer from '../components/trips/TripDetailDrawer.jsx';
import TripEditModal from '../components/trips/TripEditModal.jsx';
import TripFeedbackModal from '../components/trips/TripFeedbackModal.jsx';
import AddReceiptToTripModal from '../components/trips/AddReceiptToTripModal.jsx';
import PerDiemModal from '../components/per-diem/PerDiemModal.jsx';
import NetvisorOverview from '../components/netvisor/NetvisorOverview.jsx';
import EmissionsOverview from '../components/emissions/EmissionsOverview.jsx';
import { fill, fmtRange, suggestionCity } from '../components/trips/tripFormat.js';

/* FAS 11.1 — Resor. C56: kort och tydlig.
 *
 * Överst en rad: "22 resor · X väntar på ditt beslut · Y saknar km".
 * Flikar: Väntar på beslut (standard) | Godkända | Netvisor-status | Km.
 * Varje resa är en hopfälld rad; detaljer först vid klick. AI-förslag som
 * redan täcks av en kalenderresa, är från före 2026 eller är dubbletter
 * visas inte som förslag (backend /api/c56/suggestions räknar ut det).
 * Godkänn ändrar bara bottens egen status — inget skickas till Netvisor.
 */

const TABS = ['waiting', 'approved', 'netvisor', 'km'];
const YEAR = 2026;

function formatString(template, params) {
  return fill(template, params);
}

export default function Trips() {
  const { t, lang } = useI18n();
  const toast = useToast();

  const [tab, setTab] = useState('waiting');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [calendarTrips, setCalendarTrips] = useState([]);
  const [sugg, setSugg] = useState({ suggestions: [], counts: {} });
  const [activeTrips, setActiveTrips] = useState([]);
  const [nvTexts, setNvTexts] = useState({});
  const [kmByTrip, setKmByTrip] = useState({});

  const [calendarState, setCalendarState] = useState(null);
  const [calendarUngrouped, setCalendarUngrouped] = useState([]);
  const [calendarRefreshing, setCalendarRefreshing] = useState(false);

  const [detailTrip, setDetailTrip] = useState(null);
  const [editTrip, setEditTrip] = useState(null);
  const [feedbackTrip, setFeedbackTrip] = useState(null);
  const [addReceiptTrip, setAddReceiptTrip] = useState(null);
  const [perDiemTrip, setPerDiemTrip] = useState(null);

  const loadAll = useCallback(async () => {
    const safe = (p, fallback) => p.catch(() => fallback);
    try {
      const [cal, s, a, nv, em] = await Promise.all([
        safe(api.tripsCalendarSuggestions(), { trips: [] }),
        safe(request('/api/c56/suggestions'), { suggestions: [], counts: {} }),
        safe(api.tripsActive(), { trips: [] }),
        safe(request(`/api/netvisor/descriptions?year=${YEAR}`), { trips: [] }),
        safe(request(`/api/emissions/summary?year=${YEAR}`), { per_trip: [] }),
      ]);
      setCalendarTrips((cal && cal.trips) || []);
      setSugg(s || { suggestions: [], counts: {} });
      setActiveTrips((a && a.trips) || []);
      const texts = {};
      for (const r of (nv && nv.trips) || []) texts[r.trip_id] = r.netvisor_description;
      setNvTexts(texts);
      const km = {};
      for (const r of (em && em.per_trip) || []) {
        km[r.trip_id] = {
          km: r.km || 0,
          missing: (r.legs_missing_km || 0) > 0 || (r.flags || []).includes('km_missing'),
        };
      }
      setKmByTrip(km);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `${t.trips.toast.operationFailed}: ${detail}` });
    } finally {
      setLoading(false);
    }
  }, [t, toast]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  const liveTrips = useMemo(
    () => calendarTrips
      .filter((tr) => tr.status !== 'rejected')
      .slice()
      .sort((a, b) => (a.start_date < b.start_date ? -1 : a.start_date > b.start_date ? 1 : a.id - b.id)),
    [calendarTrips],
  );
  const waitingTrips = liveTrips.filter((tr) => tr.status !== 'approved');
  const approvedTrips = liveTrips.filter((tr) => tr.status === 'approved');
  const allSuggestions = sugg.suggestions || [];
  const visibleSuggestions = allSuggestions.filter((s) => s.visible);
  const coveredSuggestions = allSuggestions.filter((s) => s.covered_by && !s.old);
  const counts = sugg.counts || {};
  const kmMissing = liveTrips.filter((tr) => kmByTrip[tr.id] && kmByTrip[tr.id].missing).length;
  const waitingCount = waitingTrips.length + visibleSuggestions.length;

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await api.tripsRefreshSuggestions();
      toast.show({ kind: 'ok', message: t.trips.refreshed });
      await loadAll();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `${t.trips.refreshFailed}: ${detail}` });
    } finally {
      setRefreshing(false);
    }
  }, [loadAll, t, toast]);

  const onCalendarRefresh = useCallback(async () => {
    setCalendarRefreshing(true);
    try {
      const res = await api.tripsCalendarRefresh();
      const status = (res && res.status) || 'error';
      setCalendarState(status);
      if (status === 'ok') {
        setCalendarTrips(res.trips || []);
        setCalendarUngrouped(res.ungrouped || []);
        toast.show({
          kind: 'ok',
          message: formatString(t.trips.calendar.refreshed, {
            count: (res.stats && res.stats.trips) || 0,
          }),
        });
      } else if (status === 'error') {
        toast.show({
          kind: 'err',
          message: `${t.trips.calendar.refreshFailed}: ${res.detail || ''}`,
        });
      }
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `${t.trips.calendar.refreshFailed}: ${detail}` });
    } finally {
      setCalendarRefreshing(false);
    }
  }, [t, toast]);

  const calendarNotice = {
    consent_required: t.trips.calendar.consentRequired,
    not_configured: t.trips.calendar.notConfigured,
    api_disabled: t.trips.calendar.apiDisabled,
  }[calendarState];

  const onApprove = useCallback(
    async (trip, nvText) => {
      const msg = nvText ? `${t.trips.confirmApprove}\n\n${nvText}` : t.trips.confirmApprove;
      if (!window.confirm(msg)) return;
      try {
        await request(`/api/c56/calendar-trips/${trip.id}/approve`, { method: 'POST' });
        toast.show({ kind: 'ok', message: t.trips.toastApproved });
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [loadAll, t, toast],
  );

  const onUnapprove = useCallback(
    async (trip) => {
      try {
        await request(`/api/c56/calendar-trips/${trip.id}/unapprove`, { method: 'POST' });
        toast.show({ kind: 'ok', message: t.trips.toastUnapproved });
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [loadAll, t, toast],
  );

  const onAccept = useCallback(
    async (trip) => {
      try {
        await api.tripsAccept(trip.id);
        toast.show({ kind: 'ok', message: t.trips.toast.accepted });
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [loadAll, t, toast],
  );

  const onReject = useCallback(
    async (trip) => {
      if (!window.confirm(t.trips.confirmReject)) return;
      try {
        await api.tripsReject(trip.id);
        toast.show({ kind: 'ok', message: t.trips.toast.rejected });
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [loadAll, t, toast],
  );

  const onShow = useCallback(
    async (trip) => {
      try {
        setDetailTrip(await api.tripsGet(trip.id));
      } catch (err) {
        setDetailTrip(trip);
      }
    },
    [],
  );

  const onArchive = useCallback(
    async (trip) => {
      if (!window.confirm(t.trips.confirmArchive)) return;
      try {
        await api.tripsArchive(trip.id);
        toast.show({ kind: 'ok', message: t.trips.toast.archived });
        setDetailTrip(null);
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [loadAll, t, toast],
  );

  const onSaveEdit = useCallback(
    async (payload) => {
      if (!editTrip) return;
      try {
        const updated = await api.tripsEdit(editTrip.id, payload);
        toast.show({ kind: 'ok', message: t.trips.toast.edited });
        setEditTrip(null);
        if (detailTrip && detailTrip.id === updated.id) setDetailTrip(updated);
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [editTrip, detailTrip, loadAll, t, toast],
  );

  const onSaveFeedback = useCallback(
    async (payload) => {
      if (!feedbackTrip) return;
      try {
        await api.tripsFeedback(feedbackTrip.id, payload);
        toast.show({ kind: 'ok', message: t.trips.toast.feedbackSaved });
        setFeedbackTrip(null);
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [feedbackTrip, t, toast],
  );

  const onGoodFeedback = useCallback(
    async (trip) => {
      try {
        await api.tripsFeedback(trip.id, { feedback_type: 'good_grouping' });
        toast.show({ kind: 'ok', message: t.trips.toast.feedbackSaved });
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [t, toast],
  );

  const onAddReceipts = useCallback(
    async (messageIds) => {
      if (!addReceiptTrip || !messageIds.length) {
        setAddReceiptTrip(null);
        return;
      }
      try {
        const updated = await api.tripsEdit(addReceiptTrip.id, {
          add_message_ids: messageIds,
        });
        toast.show({ kind: 'ok', message: t.trips.toast.addedReceipts });
        setAddReceiptTrip(null);
        if (detailTrip && detailTrip.id === updated.id) setDetailTrip(updated);
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [addReceiptTrip, detailTrip, loadAll, t, toast],
  );

  const onRemoveReceipt = useCallback(
    async (trip, messageId) => {
      try {
        const updated = await api.tripsEdit(trip.id, {
          remove_message_ids: [messageId],
        });
        toast.show({ kind: 'ok', message: t.trips.toast.removedReceipt });
        if (detailTrip && detailTrip.id === updated.id) setDetailTrip(updated);
        await loadAll();
      } catch (err) {
        toast.show({ kind: 'err', message: t.trips.toast.operationFailed });
      }
    },
    [detailTrip, loadAll, t, toast],
  );

  if (loading) {
    return (
      <div className="settings-loading muted" data-testid="trips-loading">
        {t.common.loading}
      </div>
    );
  }

  const tabCount = {
    waiting: waitingCount,
    approved: approvedTrips.length + activeTrips.length,
  };

  const tripRow = (trip) => (
    <CalendarTripRow
      key={trip.id}
      trip={trip}
      nvText={nvTexts[trip.id]}
      km={kmByTrip[trip.id]}
      onApprove={onApprove}
      onUnapprove={onUnapprove}
    />
  );

  return (
    <div className="trips-view" data-testid="trips-view">
      <header className="trips-view__header">
        <div>
          <h2 className="trips-view__title">{t.trips.title}</h2>
          <p className="trips-view__summary" data-testid="trips-stats-summary">
            {formatString(t.trips.summaryTrips, { count: liveTrips.length })}
            {' · '}
            <strong>{formatString(t.trips.summaryWaiting, { count: waitingCount })}</strong>
            {' · '}
            {formatString(t.trips.summaryKmMissing, { count: kmMissing })}
          </p>
        </div>
        <div className="trips-view__actions">
          <button
            type="button"
            className="btn btn-sm"
            onClick={onCalendarRefresh}
            disabled={calendarRefreshing}
            data-testid="trips-calendar-refresh"
          >
            {calendarRefreshing ? t.trips.calendar.refreshing : t.trips.calendar.refreshButton}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={onRefresh}
            disabled={refreshing}
            data-testid="trips-refresh"
          >
            {refreshing ? t.trips.refreshing : t.trips.refreshButton}
          </button>
        </div>
      </header>

      <div className="fbar__segmented trips-view__tabs" role="tablist">
        {TABS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={tab === key}
            className={`fbar__tab ${tab === key ? 'is-active' : ''}`}
            onClick={() => setTab(key)}
            data-testid={`trips-tab-${key}`}
          >
            {t.trips.tabs[key]}
            {tabCount[key] != null ? <span className="fbar__count">{tabCount[key]}</span> : null}
          </button>
        ))}
      </div>

      {calendarNotice ? (
        <div className="card card-pad cal-trips__notice" data-testid="trips-calendar-notice">
          <span>{calendarNotice}</span>
          {calendarState === 'consent_required' ? (
            <a className="btn primary" href="/api/auth/gmail/start">
              {t.trips.calendar.reconnect}
            </a>
          ) : null}
        </div>
      ) : null}

      {tab === 'waiting' ? (
        <>
          <section className="trips-view__section" data-testid="trips-calendar">
            {waitingTrips.length === 0 && visibleSuggestions.length === 0 ? (
              <div className="card card-pad muted" data-testid="trips-waiting-empty">
                {t.trips.waitingNone}
              </div>
            ) : null}
            {waitingTrips.length ? (
              <div className="trips-list trips-list--compact">{waitingTrips.map(tripRow)}</div>
            ) : null}
          </section>

          <section className="trips-view__section" data-testid="trips-suggestions">
            {visibleSuggestions.length ? (
              <>
                <h3 className="trips-view__section-title">{t.trips.suggestionsTitle}</h3>
                <div className="trips-list trips-list--compact">
                  {visibleSuggestions.map((trip) => (
                    <TripSuggestionCard
                      key={trip.id}
                      trip={trip}
                      onAccept={() => onAccept(trip)}
                      onReject={() => onReject(trip)}
                      onEdit={() => setEditTrip(trip)}
                      onShow={() => onShow(trip)}
                    />
                  ))}
                </div>
              </>
            ) : null}
            {coveredSuggestions.length ? (
              <details className="trips-view__hidden" data-testid="trips-covered">
                <summary className="muted">
                  {formatString(t.trips.coveredSummary, { count: coveredSuggestions.length })}
                </summary>
                <ul>
                  {coveredSuggestions.map((s) => (
                    <li key={s.id} data-testid={`trips-covered-${s.id}`}>
                      <span>{suggestionCity(t.trips.calendar, s)}</span>
                      {' '}
                      <span className="mono muted">{fmtRange(s.start_date, s.end_date, lang)}</span>
                      {' → '}
                      <span>{formatString(t.trips.coveredBy, { id: s.covered_by.trip_id })}</span>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
            {counts.old ? (
              <div className="muted trips-view__note" data-testid="trips-old-hidden">
                {formatString(t.trips.oldHidden, { count: counts.old })}
              </div>
            ) : null}
          </section>

          {calendarUngrouped.length ? (
            <details className="card card-pad cal-trips__ungrouped" data-testid="trips-calendar-ungrouped">
              <summary>
                {t.trips.calendar.ungroupedTitle} ({calendarUngrouped.length})
              </summary>
              <ul>
                {calendarUngrouped.map((u) => (
                  <li key={u.id} className="cal-trip__meeting">
                    <span className="mono muted">{u.date}</span>
                    <span>
                      {u.summary}
                      {' · '}
                      <span className="muted">
                        {t.trips.calendar.reasons[u.reason] || u.reason}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      ) : null}

      {tab === 'approved' ? (
        <section className="trips-view__section" data-testid="trips-active">
          {approvedTrips.length === 0 && activeTrips.length === 0 ? (
            <div className="card card-pad muted" data-testid="trips-active-empty">
              {t.trips.approvedNone}
            </div>
          ) : null}
          {approvedTrips.length ? (
            <div className="trips-list trips-list--compact">{approvedTrips.map(tripRow)}</div>
          ) : null}
          {activeTrips.length ? (
            <>
              <h3 className="trips-view__section-title">{t.trips.legacyActiveTitle}</h3>
              <div className="trips-list trips-list--compact">
                {activeTrips.map((trip) => (
                  <TripActiveCard key={trip.id} trip={trip} onShow={() => setDetailTrip(trip)} />
                ))}
              </div>
            </>
          ) : null}
        </section>
      ) : null}

      {tab === 'netvisor' ? <NetvisorOverview year={YEAR} /> : null}
      {tab === 'km' ? <EmissionsOverview year={YEAR} /> : null}

      {detailTrip ? (
        <TripDetailDrawer
          trip={detailTrip}
          onClose={() => setDetailTrip(null)}
          onEdit={() => setEditTrip(detailTrip)}
          onArchive={() => onArchive(detailTrip)}
          onAddReceipt={() => setAddReceiptTrip(detailTrip)}
          onRemoveReceipt={(msgId) => onRemoveReceipt(detailTrip, msgId)}
          onGoodFeedback={() => onGoodFeedback(detailTrip)}
          onBadFeedback={() => setFeedbackTrip(detailTrip)}
          onCalculatePerDiem={
            detailTrip.status === 'active'
              ? () => setPerDiemTrip(detailTrip)
              : null
          }
        />
      ) : null}

      {perDiemTrip ? (
        <PerDiemModal
          trip={perDiemTrip}
          onClose={() => setPerDiemTrip(null)}
          onSaved={() => {
            setPerDiemTrip(null);
            loadAll();
          }}
        />
      ) : null}

      {editTrip ? (
        <TripEditModal
          trip={editTrip}
          onClose={() => setEditTrip(null)}
          onSave={onSaveEdit}
        />
      ) : null}

      {feedbackTrip ? (
        <TripFeedbackModal
          trip={feedbackTrip}
          onClose={() => setFeedbackTrip(null)}
          onSave={onSaveFeedback}
        />
      ) : null}

      {addReceiptTrip ? (
        <AddReceiptToTripModal
          trip={addReceiptTrip}
          onClose={() => setAddReceiptTrip(null)}
          onSave={onAddReceipts}
        />
      ) : null}
    </div>
  );
}

export { formatString };
