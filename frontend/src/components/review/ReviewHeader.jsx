import { useI18n } from '../../i18n/useI18n.jsx';

/* Toppbar på Granska-vyn: titel + sub + N/M-progress + Prev/Next/Approve-all. */
export default function ReviewHeader({
  currentIndex,
  total,
  onPrev,
  onNext,
  canPrev,
  canNext,
}) {
  const { t } = useI18n();
  return (
    <div className="rev-head">
      <div className="rev-head__intro">
        <h1>{t.review.title}</h1>
        <p>{t.review.subtitle}</p>
      </div>
      <div className="rev-head__nav">
        <span className="rev-progress mono">
          {total > 0 ? `${currentIndex + 1} / ${total}` : '0 / 0'}
        </span>
        <button type="button" className="btn" onClick={onPrev} disabled={!canPrev}>
          ← {t.review.prev}
        </button>
        <button type="button" className="btn" onClick={onNext} disabled={!canNext}>
          {t.review.next} →
        </button>
      </div>
    </div>
  );
}
