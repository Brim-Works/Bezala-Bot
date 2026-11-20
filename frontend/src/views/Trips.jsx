import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api, ApiError } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { fmtAmount } from '../lib/format.js';
import VendorLogo from '../components/VendorLogo.jsx';
import TripSuggestionCard from '../components/trips/TripSuggestionCard.jsx';
import CalendarTripCard from '../components/trips/CalendarTripCard.jsx';
import TripActiveCard from '../components/trips/TripActiveCard.jsx';
import TripDetailDrawer from '../components/trips/TripDetailDrawer.jsx';
import TripEditModal from '../components/trips/TripEditModal.jsx';
import TripFeedbackModal from '../components/trips/TripFeedbackModal.jsx';
import AddReceiptToTripModal from '../components/trips/AddReceiptToTripModal.jsx';
import PerDiemModal from '../components/per-diem/PerDiemModal.jsx';

/* FAS 11.1 — Resor.
 *
 * AI grupperar kvitton till resor (flygbiljett som anchor + relaterade
 * kostnader). Användaren accepterar/avvisar/justerar förslagen.
 */

function formatString(template, params) {
  if (!template) return '';
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    params && params[key] != null ? params[key] : '',
  );
}

export default function Trips() {
  const { t, lang } = useI18n();
  const toast = useToast();

  const [suggestions, setSuggestions] = useState([]);
  const [activeTrips, setActiveTrips] = useState([]);
  const [stats, setStats] = useState({ active: 0, suggested: 0, total_amount_eur: 0 });
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const [detailTrip, setDetailTrip] = useState(null);
  const [editTrip, setEditTrip] = useState(null);
  const [feedbackTrip, setFeedbackTrip] = useState(null);
  const [addReceiptTrip, setAddReceiptTrip] = useState(null);
  const [perDiemTrip, setPerDiemTrip] = useState(null);

  // Resor R1 — kalenderresor (read-only).
  const [calendarTrips, setCalendarTrips] = useState([]);
  const [calendarState, setCalendarState] = useState(null);
  const [calendarUngrouped, setCalendarUngrouped] = useState([]);
  const [calendarRefreshing, setCalendarRefreshing] = useState(false);

  const loadCalendar = useCallback(async () => {
    try {
      const res = await api.tripsCalendarSuggestions();
      setCalendarTrips((res && res.trips) || []);
    } catch (err) {
      setCalendarTrips([]);
    }
  }, []);

  useEffect(() => {
    loadCalendar();
  }, [loadCalendar]);

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

  const loadAll = useCallback(async () => {
    try {
      const [s, a, st] = await Promise.all([
        api.tripsSuggestions(),
        api.tripsActive(),
        api.tripsStats(),
      ]);
      setSuggestions((s && s.trips) || []);
      setActiveTrips((a && a.trips) || []);
      setStats(st || { active: 0, suggested: 0, total_amount_eur: 0 });
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

  const totalAmountStr = useMemo(
    () => fmtAmount(stats.total_amount_eur, 'EUR', lang),
    [stats.total_amount_eur, lang],
  );

  if (loading) {
    return (
      <div className="settings-loading muted" data-testid="trips-loading">
        {t.common.loading}
      </div>
    );
  }

  return (
    <div className="trips-view" data-testid="trips-view">
      <header className="trips-view__header">
        <div>
          <h2 className="trips-view__title">{t.trips.title}</h2>
          <p className="muted">{t.trips.description}</p>
        </div>
        <div className="trips-view__actions">
          <span
            className="pill pill--muted mono"
            data-testid="trips-stats-summary"
          >
            {formatString(t.trips.statsActive, { count: stats.active })}
            {' · '}
            {formatString(t.trips.statsSuggested, { count: stats.suggested })}
            {' · '}
            {formatString(t.trips.statsTotal, { amount: totalAmountStr })}
          </span>
          <button
            type="button"
            className="btn"
            onClick={onRefresh}
            disabled={refreshing}
            data-testid="trips-refresh"
          >
            {refreshing ? t.trips.refreshing : t.trips.refreshButton}
          </button>
        </div>
      </header>

      <section className="trips-view__section" data-testid="trips-calendar">
        <div className="cal-trips__head">
          <div>
            <h3 className="trips-view__section-title">{t.trips.calendar.title}</h3>
            <p className="muted cal-trips__desc">{t.trips.calendar.description}</p>
          </div>
          <button
            type="button"
            className="btn"
            onClick={onCalendarRefresh}
            disabled={calendarRefreshing}
            data-testid="trips-calendar-refresh"
          >
            {calendarRefreshing
              ? t.trips.calendar.refreshing
              : t.trips.calendar.refreshButton}
          </button>
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
        {calendarTrips.length === 0 ? (
          <div className="card card-pad muted" data-testid="trips-calendar-empty">
            {t.trips.calendar.none}
          </div>
        ) : (
          <div className="trips-list">
            {calendarTrips.map((trip) => (
              <CalendarTripCard key={trip.id} trip={trip} />
            ))}
          </div>
        )}
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
      </section>

      <section className="trips-view__section" data-testid="trips-suggestions">
        <h3 className="trips-view__section-title">
          {t.trips.suggestionsTitle}
        </h3>
        {suggestions.length === 0 ? (
          <div className="card card-pad muted" data-testid="trips-suggestions-empty">
            {t.trips.suggestionsNone}
          </div>
        ) : (
          <div className="trips-list">
            {suggestions.map((trip) => (
              <TripSuggestionCard
                key={trip.id}
                trip={trip}
                onAccept={() => onAccept(trip)}
                onReject={() => onReject(trip)}
                onEdit={() => setEditTrip(trip)}
                onShow={() => setDetailTrip(trip)}
              />
            ))}
          </div>
        )}
      </section>

      <section className="trips-view__section" data-testid="trips-active">
        <h3 className="trips-view__section-title">{t.trips.activeTitle}</h3>
        {activeTrips.length === 0 ? (
          <div className="card card-pad muted" data-testid="trips-active-empty">
            {t.trips.activeNone}
          </div>
        ) : (
          <div className="trips-list">
            {activeTrips.map((trip) => (
              <TripActiveCard
                key={trip.id}
                trip={trip}
                onShow={() => setDetailTrip(trip)}
              />
            ))}
          </div>
        )}
      </section>

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
