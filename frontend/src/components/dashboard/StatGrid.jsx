import StatCard from './StatCard.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { useRouter } from '../../router/useRouter.jsx';
import { routeForView } from '../../routes.js';

export default function StatGrid({ stats }) {
  const { t } = useI18n();
  const { navigate } = useRouter();

  return (
    <div className="stat-grid">
      <StatCard
        accent
        label={t.stats.pending}
        value={stats.pending}
        sub={
          <span className="stat__link">
            {t.nav.review} →
          </span>
        }
        onClick={() => navigate(routeForView('review'))}
      />
      <StatCard
        label={t.stats.transferredToday}
        value={stats.transferredToday}
        sub={t.stats.transferredTodaySub}
      />
      <StatCard
        label={t.stats.errors}
        value={stats.errors}
        sub={t.stats.errorsSub}
        emphasizeError
      />
      <StatCard
        label={t.stats.totalThisWeek}
        value={stats.totalThisWeek}
        sub={t.stats.totalThisWeekSub}
      />
    </div>
  );
}
