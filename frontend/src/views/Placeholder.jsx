import { useI18n } from '../i18n/useI18n.jsx';

/* Delad placeholder-komponent. Varje vy skickar bara in sin i18n-nyckel. */
export default function Placeholder({ viewKey }) {
  const { t } = useI18n();
  const copy = t.views[viewKey];
  return (
    <section className="placeholder">
      <h1>{copy.title}</h1>
      <p>{copy.placeholder}</p>
      <div className="note">
        Commit 1 levererar layout, tema, i18n och API-klient. Innehåll kopplas
        in i kommande commits enligt SPEC.md.
      </div>
    </section>
  );
}
