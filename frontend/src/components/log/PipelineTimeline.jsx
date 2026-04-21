import { useI18n } from '../../i18n/useI18n.jsx';
import {
  IconMail,
  IconSparkle,
  IconDrive,
  IconBezala,
} from '../../icons/index.jsx';
import { formatDuration, runDuration } from '../../lib/runNarrative.js';

/* 4 stages. Backend saknar per-stage-timing — staplar ritas lika breda
 * (25% var) tills BACKEND-TODO är löst. Stage-status härleds från
 * run-aggregat: om errors > 0 → Bezala-stapeln röd. */

const STAGES = [
  { key: 'gmail', icon: IconMail },
  { key: 'ai', icon: IconSparkle },
  { key: 'drive', icon: IconDrive },
  { key: 'bezala', icon: IconBezala },
];

function stageStatus(stageKey, run) {
  if (!run) return 'idle';
  const errors = run.errors || 0;
  const processed = run.messages_processed || 0;
  if (stageKey === 'bezala') {
    if (errors > 0) return 'error';
    if (processed === 0) return 'idle';
    return 'ok';
  }
  if (stageKey === 'gmail' && run.messages_found === 0) return 'idle';
  if (processed === 0) return 'idle';
  return 'ok';
}

export default function PipelineTimeline({ run }) {
  const { t } = useI18n();
  if (!run) return null;
  const totalDuration = runDuration(run);
  const equalShareMs = totalDuration != null ? totalDuration / STAGES.length : null;

  return (
    <div className="card card-pad pipeline" data-testid="pipeline-timeline">
      <div className="pipeline__label">{t.log.pipelineTitle}</div>
      <div className="pipeline__list">
        {STAGES.map((stage) => {
          const Icon = stage.icon;
          const status = stageStatus(stage.key, run);
          const note = t.log.stageNote[stage.key];
          return (
            <div key={stage.key} className="pipeline__row">
              <div className="pipeline__step">
                <div className={`pipeline__icon pipeline__icon--${status}`}>
                  <Icon className="icon sm" />
                </div>
                <div className="pipeline__meta">
                  <div className="pipeline__name">{t.log.stages[stage.key]}</div>
                  <div className="pipeline__note muted">{note}</div>
                </div>
              </div>
              <div className="pipeline__bar-wrap">
                <div
                  className={`pipeline__bar pipeline__bar--${status}`}
                  style={{ width: '25%' }}
                />
                <span className="mono pipeline__dur">
                  {formatDuration(equalShareMs)}
                </span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="pipeline__footer muted">
        {t.log.stageEstimateNote}
      </div>
    </div>
  );
}
