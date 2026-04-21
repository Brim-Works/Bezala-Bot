import { useI18n } from '../../i18n/useI18n.jsx';

/* Instrument Serif-rubrik. CSS i base.css döljer den i Tema A
 * (display: none) och visar den i Tema B per design. */
export default function HeroStrip({ pendingCount }) {
  const { t } = useI18n();
  return (
    <div className="hero-strip">
      <div className="hero-strip__main">
        <h1>
          {t.hero.before} <em>{t.hero.emphasis}</em> {t.hero.after}
        </h1>
      </div>
      <div className="hero-strip__sub">
        {t.tagline}
        {pendingCount > 0 ? (
          <>
            {' '}
            · <span className="mono">{pendingCount}</span> {t.hero.waiting}
          </>
        ) : null}
      </div>
    </div>
  );
}
