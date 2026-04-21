import { IconReview } from '../../icons/index.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';

/* Tom-state: ingen emoji, linjeikon enligt CLAUDE.md-regeln. */
export default function EmptyReview() {
  const { t } = useI18n();
  return (
    <div className="card card-pad review-empty" data-testid="review-empty">
      <div className="review-empty__icon" aria-hidden="true">
        <IconReview className="icon" />
      </div>
      <h2 className="serif review-empty__title">{t.review.empty.title}</h2>
      <p className="muted">{t.review.empty.body}</p>
    </div>
  );
}
