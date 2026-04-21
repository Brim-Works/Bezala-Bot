import StatCard from '../dashboard/StatCard.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';

/* 4 KPI:er för Logg-vyn. 'AI-kostnad' renderas som '—' eftersom backend
 * inte exponerar token-kostnad ännu. */
export default function KpiStrip({ runs24h, autoRate, autoCount, processedCount, errorCount }) {
  const { t } = useI18n();
  const autoRateValue =
    autoRate == null || Number.isNaN(autoRate)
      ? '—'
      : `${autoRate}%`;
  const autoSub =
    processedCount == null
      ? t.log.kpi.autoRateSub
      : `${autoCount} / ${processedCount} ${t.log.kpi.transferred}`;

  return (
    <div className="stat-grid kpi-strip">
      <StatCard
        label={t.log.kpi.runs24h}
        value={runs24h ?? '—'}
        sub={t.log.kpi.runs24hSub}
      />
      <StatCard
        label={t.log.kpi.autoRate}
        value={autoRateValue}
        sub={autoSub}
      />
      <StatCard
        label={t.log.kpi.aiSpend}
        value="—"
        sub={t.log.kpi.aiSpendSub}
      />
      <StatCard
        label={t.log.kpi.errors}
        value={errorCount ?? 0}
        sub={errorCount > 0 ? t.log.kpi.errorsNeedAction : t.log.kpi.errorsClean}
        emphasizeError
      />
    </div>
  );
}
