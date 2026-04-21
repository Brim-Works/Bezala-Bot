import Pill from '../Pill.jsx';
import PipelineTimeline from './PipelineTimeline.jsx';
import RunMessages from './RunMessages.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtDate } from '../../lib/format.js';
import {
  formatDuration,
  runDuration,
  runNarrative,
  runStatusKind,
} from '../../lib/runNarrative.js';

function pillLabel(t, run) {
  if (!run) return '';
  if ((run.errors || 0) > 0) return t.log.pill.partial;
  if ((run.messages_processed || 0) === 0) return t.log.pill.idle;
  return t.log.pill.ok;
}

export default function RunDetail({ run, messages, onOpenMessage }) {
  const { t, lang } = useI18n();
  if (!run) {
    return (
      <div className="card card-pad log-empty" data-testid="run-detail-empty">
        <p className="serif log-empty__prompt">{t.log.selectPrompt}</p>
      </div>
    );
  }

  const tone = runStatusKind(run);

  return (
    <div className="log-detail" data-testid="run-detail">
      <div className="card card-pad log-detail__head">
        <div className="log-detail__intro">
          <div className="log-detail__kicker">
            {t.log.runLabel} #{run.id}
          </div>
          <div className="serif log-detail__date">
            {fmtDate(run.started_at, lang)}
          </div>
          <p className="log-detail__narrative">{runNarrative(run, lang)}</p>
        </div>
        <div className="log-detail__status">
          <Pill kind={tone}>{pillLabel(t, run)}</Pill>
          <span className="mono muted">
            {formatDuration(runDuration(run))}
          </span>
        </div>
      </div>

      <PipelineTimeline run={run} />

      <RunMessages messages={messages} onOpenMessage={onOpenMessage} />
    </div>
  );
}
