import { useI18n } from '../i18n/useI18n.jsx';
import Confidence from '../components/Confidence.jsx';
import FeedbackButtons from '../components/FeedbackButtons.jsx';
import { fmtAmount } from '../lib/format.js';
import { IconSparkle } from '../icons/index.jsx';

function Row({ label, children }) {
  return (
    <div className="drawer-kv">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export default function AiTab({ message }) {
  const { t, lang } = useI18n();
  if (!message) return null;

  const fields = [
    [t.drawer.ai.vendor, message.vendor],
    [t.drawer.ai.date, message.receipt_date],
    [t.drawer.ai.amount, message.amount != null
      ? fmtAmount(message.amount, message.currency, lang)
      : null],
    [t.drawer.ai.category, message.category],
    [t.drawer.ai.filename, message.file_name],
  ];

  return (
    <div className="drawer-section" data-testid="drawer-tab-ai-content">
      <div className="drawer-ai-banner">
        <div className="drawer-ai-banner__icon" aria-hidden="true">
          <IconSparkle className="icon sm" />
        </div>
        <div className="drawer-ai-banner__body">
          <div className="drawer-ai-banner__model">Claude Sonnet 4.6</div>
          <div className="drawer-ai-banner__meta">
            {t.drawer.ai.analysisComplete} · <Confidence value={message.ai_confidence} />
          </div>
        </div>
      </div>

      <div className="drawer-section__label">{t.drawer.ai.extractedFields}</div>
      <dl className="drawer-kv-list">
        {fields.map(([label, value]) => (
          <Row key={label} label={label}>
            {value ? (
              <span className="mono">{value}</span>
            ) : (
              <span className="muted">—</span>
            )}
          </Row>
        ))}
      </dl>

      {message.summary ? (
        <>
          <div className="drawer-section__label">{t.drawer.ai.reasoning}</div>
          <p className="drawer-reasoning">{message.summary}</p>
        </>
      ) : null}

      {message.ai_description_en ? (
        <>
          <div className="drawer-section__label">{t.drawer.ai.descriptionEn}</div>
          <p className="drawer-reasoning">{message.ai_description_en}</p>
        </>
      ) : null}

      <FeedbackButtons messageId={message.message_id} message={message} />
    </div>
  );
}
