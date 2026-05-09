import { useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { useDrawer } from '../drawer/DrawerProvider.jsx';
import { useTrashCountContext } from '../hooks/TrashCountProvider.jsx';
import FeedbackModal from './FeedbackModal.jsx';

export default function FeedbackButtons({ messageId, message }) {
  const { t } = useI18n();
  const toast = useToast();
  const { closeIfFor } = useDrawer();
  const { bump: bumpTrashCount, bumpMessagesVersion } = useTrashCountContext();
  const [submitted, setSubmitted] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!messageId) return null;

  const onThumbsUp = async () => {
    if (submitted || busy) return;
    setBusy(true);
    try {
      await api.feedbackThumbs({ messageId, isPositive: true, fields: [] });
      setSubmitted(true);
      toast.show({ kind: 'ok', message: t.drawer.ai.feedback.thanks });
    } catch (err) {
      toast.show({ kind: 'err', message: String(err?.message || err) });
    } finally {
      setBusy(false);
    }
  };

  // FAS 8.1 — modalen kallar denna med kind='field_error' eller
  // 'not_a_receipt'. Vid not_a_receipt har raden flyttats till papper-
  // skorgen på backend, så vi stänger drawern och uppdaterar listan.
  const onModalSaved = (kind) => {
    setSubmitted(true);
    setModalOpen(false);
    if (kind === 'not_a_receipt' && message) {
      try {
        closeIfFor(message.id);
      } catch {
        // ignorera — drawer kan vara unmonted
      }
      try {
        bumpTrashCount(1);
      } catch {}
      try {
        bumpMessagesVersion();
      } catch {}
    }
  };

  return (
    <div className="ai-feedback" data-testid="feedback-buttons">
      <div className="drawer-section__label">{t.drawer.ai.feedback.title}</div>
      <p className="muted">{t.drawer.ai.feedback.lead}</p>
      {submitted ? (
        <div className="ai-feedback__done" data-testid="feedback-submitted">
          ✓ {t.drawer.ai.feedback.submitted}
        </div>
      ) : (
        <div className="ai-feedback__actions">
          <button
            type="button"
            className="btn"
            onClick={onThumbsUp}
            disabled={busy}
            data-testid="feedback-thumbs-up"
          >
            👍 {t.drawer.ai.feedback.thumbsUp}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => setModalOpen(true)}
            disabled={busy}
            data-testid="feedback-thumbs-down"
          >
            👎 {t.drawer.ai.feedback.thumbsDown}
          </button>
        </div>
      )}
      <FeedbackModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSaved={onModalSaved}
        messageId={messageId}
        message={message}
      />
    </div>
  );
}
