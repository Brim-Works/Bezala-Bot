import { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api, ApiError } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';

/* FAS 11.1.1 — alltid synlig sektion i Drawer:n som visar resor som
 * detta kvitto kan kopplas till + status (AI-förslag eller manuellt).
 *
 * Användaren kan toggla checkbox per resa → koppla / koppla bort.
 * Endpoint är idempotent så snabba dubbla klickar är säkra.
 */

export default function TripLinkSection({ message }) {
  const { t } = useI18n();
  const toast = useToast();
  const [trips, setTrips] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busyTripId, setBusyTripId] = useState(null);

  const messageKey = message?.message_id;

  const reload = useCallback(async () => {
    if (!messageKey) {
      setTrips([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const data = await api.tripsAvailableForMessage(messageKey);
      setTrips((data && data.trips) || []);
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) {
        // 404 = meddelandet inte i DB ännu (preview/snippet) → bara tom lista
        toast.show({
          kind: 'err',
          message: t.drawer.trips.loadFailed,
        });
      }
      setTrips([]);
    } finally {
      setLoading(false);
    }
  }, [messageKey, t, toast]);

  useEffect(() => {
    reload();
  }, [reload]);

  const onToggle = async (trip) => {
    if (busyTripId) return;
    setBusyTripId(trip.id);
    try {
      if (trip.is_linked) {
        await api.tripsUnlinkMessage(messageKey, trip.id);
        toast.show({ kind: 'ok', message: t.drawer.trips.unlinked });
      } else {
        await api.tripsLinkMessage(messageKey, trip.id);
        toast.show({ kind: 'ok', message: t.drawer.trips.linked });
      }
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.drawer.trips.toggleFailed}: ${detail}`,
      });
    } finally {
      setBusyTripId(null);
    }
  };

  if (loading) {
    return (
      <section
        className="drawer__trip-link"
        data-testid="drawer-trip-link"
      >
        <h4 className="drawer__trip-link-title">
          🌍 {t.drawer.trips.title}
        </h4>
        <p className="muted">{t.common.loading}</p>
      </section>
    );
  }

  const linked = trips.filter((tr) => tr.is_linked);
  const available = trips.filter((tr) => !tr.is_linked);

  return (
    <section
      className="drawer__trip-link"
      data-testid="drawer-trip-link"
    >
      <h4 className="drawer__trip-link-title">
        🌍 {t.drawer.trips.title}
      </h4>

      {linked.length > 0 ? (
        <div className="drawer__trip-linked">
          <div className="muted drawer__trip-link-sub">
            {t.drawer.trips.linkedTo}
          </div>
          <ul className="drawer__trip-link-list">
            {linked.map((trip) => (
              <li
                key={trip.id}
                className="drawer__trip-link-row drawer__trip-link-row--linked"
                data-testid={`drawer-trip-linked-${trip.id}`}
              >
                <label className="drawer__trip-link-label">
                  <input
                    type="checkbox"
                    checked
                    onChange={() => onToggle(trip)}
                    disabled={busyTripId === trip.id}
                    data-testid={`drawer-trip-toggle-${trip.id}`}
                  />
                  <span className="drawer__trip-link-name">
                    {trip.title}
                  </span>
                  <span
                    className={
                      'pill mono ' +
                      (trip.added_by === 'manual'
                        ? 'pill--accent'
                        : 'pill--muted')
                    }
                    data-testid={`drawer-trip-source-${trip.id}`}
                  >
                    {trip.added_by === 'manual'
                      ? t.drawer.trips.manuallyAdded
                      : t.drawer.trips.aiSuggested}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {available.length > 0 ? (
        <div className="drawer__trip-available">
          <div className="muted drawer__trip-link-sub">
            {t.drawer.trips.availableTrips}
          </div>
          <ul className="drawer__trip-link-list">
            {available.map((trip) => (
              <li
                key={trip.id}
                className="drawer__trip-link-row"
                data-testid={`drawer-trip-available-${trip.id}`}
              >
                <label className="drawer__trip-link-label">
                  <input
                    type="checkbox"
                    checked={false}
                    onChange={() => onToggle(trip)}
                    disabled={busyTripId === trip.id}
                    data-testid={`drawer-trip-toggle-${trip.id}`}
                  />
                  <span className="drawer__trip-link-name">
                    {trip.title}
                  </span>
                  <span className="muted mono">
                    {trip.start_date} – {trip.end_date}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {linked.length === 0 && available.length === 0 ? (
        <p className="muted" data-testid="drawer-trip-empty">
          {t.drawer.trips.noAvailable}
        </p>
      ) : null}
    </section>
  );
}
