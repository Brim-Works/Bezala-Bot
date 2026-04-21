import { useI18n } from '../i18n/useI18n.jsx';
import { useRouter } from '../router/useRouter.jsx';

export default function NotFound() {
  const { t } = useI18n();
  const { navigate } = useRouter();
  return (
    <section className="placeholder">
      <h1>{t.views.notFound.title}</h1>
      <p>{t.views.notFound.body}</p>
      <div>
        <button type="button" className="btn primary" onClick={() => navigate('/')}>
          {t.views.notFound.home}
        </button>
      </div>
    </section>
  );
}
