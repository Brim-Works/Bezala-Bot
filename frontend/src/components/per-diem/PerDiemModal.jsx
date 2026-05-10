import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { api, ApiError } from '../../api/client.js';
import { useToast } from '../../lib/toast.jsx';
import { fmtAmount } from '../../lib/format.js';

/* FAS 11.5.1 — Per Diem Calculator Modal.
 *
 * Tre steg i samma modal:
 *  1) Restider (AI-extraherade flygtider eller manuell input)
 *  2) Destinationsland (AI-förslag + dropdown)
 *  3) Beräkning per dygn (heldag/halvdag, mat-toggle, total)
 *
 * Modalen sparar via POST /calculate-per-diem och visar resultatet inline.
 */

const SUPPORTED_COUNTRIES = ['FI', 'SE', 'NO', 'LV'];

function formatString(template, params) {
  if (!template) return '';
  return template.replace(/\{(\w+)\}/g, (_, key) =>
    params && params[key] != null ? params[key] : '',
  );
}

function toLocalInputValue(iso) {
  if (!iso) return '';
  // Trim sub-seconds and timezone for `<input type="datetime-local">`.
  // Server returnerar ISO som kan ha tidszon — vi visar bara "YYYY-MM-DDTHH:MM".
  const s = String(iso);
  const m = s.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/);
  return m ? m[1] : '';
}

function fromLocalInputValue(local) {
  // Returnerar samma sträng — backend tolkar naive datetime som lokaltid.
  return local && local.length >= 16 ? `${local}:00` : local || null;
}

function dygnTypeLabel(t, dygn) {
  const hours = dygn.hours;
  switch (dygn.type) {
    case 'full_day_abroad':
      return formatString(t.perDiem.step3FullDayAbroad, { hours });
    case 'full_day_domestic':
      return formatString(t.perDiem.step3FullDayDomestic, { hours });
    case 'half_day_abroad':
      return formatString(t.perDiem.step3HalfDayAbroad, { hours });
    case 'half_day_domestic':
      return formatString(t.perDiem.step3HalfDayDomestic, { hours });
    default:
      return dygn.type;
  }
}

export default function PerDiemModal({ trip, onClose, onSaved }) {
  const { t, lang } = useI18n();
  const toast = useToast();

  const [departureHome, setDepartureHome] = useState(
    toLocalInputValue(trip.departure_home_at),
  );
  const [returnHome, setReturnHome] = useState(
    toLocalInputValue(trip.return_home_at),
  );
  const [destinationCountry, setDestinationCountry] = useState(
    trip.destination_country || 'FI',
  );
  const [aiSuggestedCountry, setAiSuggestedCountry] = useState(null);
  const [tripRoute, setTripRoute] = useState(trip.trip_route || '');
  const [calculation, setCalculation] = useState(
    trip.per_diem_calculation || null,
  );
  const [mealToggles, setMealToggles] = useState(() => {
    const initial = {};
    if (trip.per_diem_calculation && trip.per_diem_calculation.dygnet) {
      for (const d of trip.per_diem_calculation.dygnet) {
        initial[String(d.day_number)] = !!d.meal_deduction;
      }
    }
    return initial;
  });
  const [extracting, setExtracting] = useState(false);
  const [calculating, setCalculating] = useState(false);
  const [saving, setSaving] = useState(false);

  const countryOptions = useMemo(() => {
    return SUPPORTED_COUNTRIES.map((code) => ({
      code,
      label: (t.perDiem.countries && t.perDiem.countries[code]) || code,
    }));
  }, [t]);

  // Auto-extract om vi inte har tider sparade ännu
  useEffect(() => {
    let cancelled = false;
    if (departureHome || returnHome) return undefined;
    setExtracting(true);
    (async () => {
      try {
        const data = await api.tripsExtractFlightTimes(trip.id);
        if (cancelled) return;
        if (data.departure_home_at) {
          setDepartureHome(toLocalInputValue(data.departure_home_at));
        }
        if (data.return_home_at) {
          setReturnHome(toLocalInputValue(data.return_home_at));
        }
        if (data.destination_country_suggestion) {
          setAiSuggestedCountry(data.destination_country_suggestion);
          if (!trip.destination_country) {
            setDestinationCountry(data.destination_country_suggestion);
          }
        }
        if (data.trip_route) setTripRoute(data.trip_route);
      } catch (err) {
        if (!cancelled) {
          toast.show({
            kind: 'err',
            message: t.perDiem.toastExtractFailed,
          });
        }
      } finally {
        if (!cancelled) setExtracting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trip.id]);

  const calculate = useCallback(async () => {
    if (!departureHome || !returnHome) {
      toast.show({ kind: 'err', message: t.perDiem.toastFailed });
      return;
    }
    setCalculating(true);
    try {
      const data = await api.tripsCalculatePerDiem(trip.id, {
        departure_home_at: fromLocalInputValue(departureHome),
        return_home_at: fromLocalInputValue(returnHome),
        destination_country: destinationCountry,
        meal_toggles: mealToggles,
      });
      setCalculation(data);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.perDiem.toastFailed}: ${detail}`,
      });
    } finally {
      setCalculating(false);
    }
  }, [
    trip.id,
    departureHome,
    returnHome,
    destinationCountry,
    mealToggles,
    t,
    toast,
  ]);

  const onMealToggle = useCallback(
    async (dayNumber, included) => {
      const newToggles = { ...mealToggles, [String(dayNumber)]: included };
      setMealToggles(newToggles);
      // Live-uppdatera om vi redan har en beräkning
      if (calculation) {
        try {
          const data = await api.tripsUpdatePerDiem(trip.id, {
            meal_toggles: newToggles,
          });
          setCalculation(data);
        } catch (err) {
          // Tyst — användaren kan trycka Beräkna igen
        }
      }
    },
    [trip.id, calculation, mealToggles],
  );

  const onSave = useCallback(async () => {
    if (!calculation) {
      await calculate();
      return;
    }
    setSaving(true);
    try {
      // calculate-endpointen sparar redan, men kör en gång till med
      // aktuella toggles för att garantera persistens.
      const data = await api.tripsCalculatePerDiem(trip.id, {
        departure_home_at: fromLocalInputValue(departureHome),
        return_home_at: fromLocalInputValue(returnHome),
        destination_country: destinationCountry,
        meal_toggles: mealToggles,
      });
      const amountStr = fmtAmount(data.total_amount, data.currency || 'EUR', lang);
      toast.show({
        kind: 'ok',
        message: formatString(t.perDiem.toastSaved, { amount: amountStr }),
      });
      if (typeof onSaved === 'function') onSaved(data);
      onClose();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.perDiem.toastFailed}: ${detail}`,
      });
    } finally {
      setSaving(false);
    }
  }, [
    calculation,
    calculate,
    trip.id,
    departureHome,
    returnHome,
    destinationCountry,
    mealToggles,
    lang,
    onClose,
    onSaved,
    t,
    toast,
  ]);

  const totalLabel = useMemo(() => {
    if (!calculation) return null;
    return fmtAmount(
      calculation.total_amount,
      calculation.currency || 'EUR',
      lang,
    );
  }, [calculation, lang]);

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-label={t.perDiem.title}
      data-testid={`per-diem-modal-${trip.id}`}
    >
      <form
        className="modal-card card-pad"
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <h3 className="modal-card__title">{t.perDiem.title}</h3>
        <p className="muted">{t.perDiem.description}</p>

        {/* Steg 1 — restider */}
        <section className="per-diem__section" data-testid="per-diem-step-1">
          <h4>{t.perDiem.step1Title}</h4>
          {extracting ? (
            <div className="muted" data-testid="per-diem-extracting">
              {t.perDiem.step1Extracting}
            </div>
          ) : null}
          {tripRoute ? (
            <div className="muted mono" data-testid="per-diem-route">
              {t.perDiem.step1Route}: {tripRoute}
            </div>
          ) : null}
          <div className="form-row-grid">
            <label className="form-row">
              <span>{t.perDiem.step1DepartureHome}</span>
              <input
                type="datetime-local"
                value={departureHome}
                onChange={(event) => setDepartureHome(event.target.value)}
                data-testid="per-diem-departure-home"
              />
            </label>
            <label className="form-row">
              <span>{t.perDiem.step1ReturnHome}</span>
              <input
                type="datetime-local"
                value={returnHome}
                onChange={(event) => setReturnHome(event.target.value)}
                data-testid="per-diem-return-home"
              />
            </label>
          </div>
        </section>

        {/* Steg 2 — destination */}
        <section className="per-diem__section" data-testid="per-diem-step-2">
          <h4>{t.perDiem.step2Title}</h4>
          <label className="form-row">
            <span>{t.perDiem.step2Country}</span>
            <select
              value={destinationCountry}
              onChange={(event) => setDestinationCountry(event.target.value)}
              data-testid="per-diem-country"
            >
              {countryOptions.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          {aiSuggestedCountry ? (
            <div className="muted mono" data-testid="per-diem-ai-suggestion">
              {formatString(t.perDiem.step2AiSuggested, {
                country:
                  (t.perDiem.countries &&
                    t.perDiem.countries[aiSuggestedCountry]) ||
                  aiSuggestedCountry,
              })}
            </div>
          ) : null}
        </section>

        {/* Steg 3 — beräkning */}
        <section className="per-diem__section" data-testid="per-diem-step-3">
          <h4>{t.perDiem.step3Title}</h4>
          {!calculation ? (
            <button
              type="button"
              className="btn"
              onClick={calculate}
              disabled={calculating || !departureHome || !returnHome}
              data-testid="per-diem-calculate"
            >
              {calculating
                ? t.perDiem.step3Calculating
                : t.perDiem.step3Calculate}
            </button>
          ) : (
            <>
              <ul className="per-diem__dygnet">
                {(calculation.dygnet || []).map((dygn) => (
                  <li
                    key={dygn.day_number}
                    className="per-diem__dygn"
                    data-testid={`per-diem-dygn-${dygn.day_number}`}
                  >
                    <div className="per-diem__dygn-head">
                      <strong>
                        {dygn.hours >= 24
                          ? formatString(t.perDiem.step3DayLabel, {
                              n: dygn.day_number,
                            })
                          : formatString(t.perDiem.step3PartialLabel, {
                              n: dygn.day_number,
                            })}
                      </strong>
                      <span className="muted mono">
                        {dygnTypeLabel(t, dygn)}
                      </span>
                    </div>
                    <label className="per-diem__meal-toggle">
                      <input
                        type="checkbox"
                        checked={
                          !!mealToggles[String(dygn.day_number)]
                        }
                        disabled={
                          dygn.rule_applied ===
                          'puolikas_ulkomaanpäiväraha_deldygn'
                        }
                        onChange={(event) =>
                          onMealToggle(dygn.day_number, event.target.checked)
                        }
                        data-testid={`per-diem-meal-${dygn.day_number}`}
                      />
                      <span>{t.perDiem.step3MealIncluded}</span>
                    </label>
                    <div className="per-diem__dygn-amount mono">
                      {fmtAmount(
                        dygn.final_amount,
                        dygn.rate_currency || 'EUR',
                        lang,
                      )}
                      {dygn.meal_deduction ? (
                        <span className="muted">
                          {' '}
                          {t.perDiem.step3MealDeduction}
                        </span>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
              <div
                className="per-diem__total mono"
                data-testid="per-diem-total"
              >
                {t.perDiem.step3Total}: {totalLabel}
              </div>
              {calculation.is_short_foreign_trip ? (
                <div className="muted">{t.perDiem.warningShortForeignTrip}</div>
              ) : null}
              {(calculation.warnings || []).map((w) => (
                <div key={w} className="muted">
                  ⚠ {w}
                </div>
              ))}
            </>
          )}
        </section>

        <footer className="modal-card__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onClose}
            disabled={saving}
            data-testid="per-diem-cancel"
          >
            {t.perDiem.cancel}
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={saving || !calculation}
            data-testid="per-diem-save"
          >
            {saving ? t.perDiem.saving : t.perDiem.save}
          </button>
        </footer>
      </form>
    </div>
  );
}
