import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api, ApiError } from '../api/client.js';
import { withStatuses } from '../api/adapters.js';
import { useApiData } from '../hooks/useApiData.js';
import { useToast } from '../lib/toast.jsx';
import { sortMessages } from '../lib/sortMessages.js';
import { useDrawer } from '../drawer/DrawerProvider.jsx';

import ReviewHeader from '../components/review/ReviewHeader.jsx';
import ReviewQueue from '../components/review/ReviewQueue.jsx';
import PdfPreview from '../components/review/PdfPreview.jsx';
import ReviewForm from '../components/review/ReviewForm.jsx';
import EmptyReview from '../components/review/EmptyReview.jsx';
import DeleteReasonDialog from '../components/trash/DeleteReasonDialog.jsx';
import { useDeleteFlow } from '../hooks/useDeleteFlow.js';
import { useTrashCountContext } from '../hooks/TrashCountProvider.jsx';

// Override-keys som mappar 1:1 till ProcessedMessage-kolumner och som
// AI-feedback-pipelinen lär sig av. Andra fält (vat_rate, project, ...)
// finns inte i backend-schemat ännu och hoppas över.
const FEEDBACK_FIELDS = new Set([
  'vendor',
  'amount',
  'receipt_date',
  'currency',
  'category',
]);

export default function Review() {
  const { t } = useI18n();
  const toast = useToast();
  const { closeIfFor } = useDrawer();
  const { bump: bumpTrashCount, messagesVersion } = useTrashCountContext();
  const [activeId, setActiveId] = useState(null);
  const [uploadingId, setUploadingId] = useState(null);
  // Optimistic-borttagna id:n — raden tas bort från kön direkt vid approve;
  // återställs om servern returnerar fel.
  const [optimisticallyRemoved, setOptimisticallyRemoved] = useState(
    () => new Set(),
  );

  const loader = useCallback(async () => {
    const raw = await api.messages(200);
    return (raw || []).map(withStatuses);
  }, []);

  const { data, refetch } = useApiData(loader, []);
  const allMessages = data || [];

  // Extern signal (t.ex. efter drawer-delete) → refetcha direkt.
  useEffect(() => {
    if (messagesVersion === 0) return;
    refetch().catch(() => {});
  }, [messagesVersion, refetch]);

  const queue = useMemo(() => {
    const pending = allMessages
      .filter((m) => m.bezala_status === 'pending')
      .filter((m) => !optimisticallyRemoved.has(m.id));
    // Default: senaste kvitto överst (receipt_date DESC, fallback processed_at).
    return sortMessages(pending, 'receipt_date', 'desc');
  }, [allMessages, optimisticallyRemoved]);

  const currentIndex = useMemo(() => {
    if (activeId == null) return 0;
    const idx = queue.findIndex((m) => m.id === activeId);
    return idx >= 0 ? idx : 0;
  }, [queue, activeId]);

  const active = queue[currentIndex] || null;

  // Om vald id försvinner ur kön (t.ex. efter godkännande) — välj första.
  useEffect(() => {
    if (queue.length === 0) {
      if (activeId !== null) setActiveId(null);
      return;
    }
    if (activeId == null || !queue.some((m) => m.id === activeId)) {
      setActiveId(queue[0].id);
    }
  }, [queue, activeId]);

  const onSelect = useCallback((id) => setActiveId(id), []);

  const onPrev = useCallback(() => {
    if (currentIndex > 0) setActiveId(queue[currentIndex - 1].id);
  }, [currentIndex, queue]);

  const onNext = useCallback(() => {
    if (currentIndex < queue.length - 1) setActiveId(queue[currentIndex + 1].id);
  }, [currentIndex, queue]);

  const onApprove = useCallback(
    async (msg, overrides) => {
      if (!msg || uploadingId === msg.id) return;
      setUploadingId(msg.id);
      setOptimisticallyRemoved((s) => {
        const next = new Set(s);
        next.add(msg.id);
        return next;
      });

      // Implicit feedback: skicka korrigeringar för ändrade fält parallellt
      // med upload. Wrappade i .catch — får aldrig blockera kärnflödet.
      const correctionPromises = [];
      if (overrides && msg.message_id) {
        for (const [key, val] of Object.entries(overrides)) {
          if (!FEEDBACK_FIELDS.has(key)) continue;
          const aiVal = msg[key];
          const aiStr = aiVal == null ? null : String(aiVal);
          const newStr = val == null ? null : String(val);
          if (aiStr === newStr) continue;
          correctionPromises.push(
            api
              .feedbackCorrection({
                messageId: msg.message_id,
                fieldName: key,
                aiValue: aiStr,
                correctValue: newStr,
              })
              .catch(() => null),
          );
        }
      }

      try {
        const [uploadResult] = await Promise.all([
          api.uploadToBezala(msg.id, overrides),
          Promise.allSettled(correctionPromises),
        ]);
        void uploadResult;
        toast.show({ kind: 'ok', message: t.review.toast.uploaded });
        if (correctionPromises.length > 0) {
          toast.show({
            kind: 'ok',
            message: t.review.toast.implicitLearning,
          });
        }
        // Stäng drawern om den var öppen för den godkända raden.
        closeIfFor(msg.id);
        refetch()
          .then(() => {
            setOptimisticallyRemoved(new Set());
          })
          .catch(() => {});
      } catch (err) {
        setOptimisticallyRemoved((s) => {
          const next = new Set(s);
          next.delete(msg.id);
          return next;
        });
        const detail = err instanceof ApiError ? err.message : String(err);
        toast.show({
          kind: 'err',
          message: `${t.review.toast.uploadFailed}: ${detail}`,
        });
      } finally {
        setUploadingId(null);
      }
    },
    [
      closeIfFor,
      refetch,
      t.review.toast.implicitLearning,
      t.review.toast.uploadFailed,
      t.review.toast.uploaded,
      toast,
      uploadingId,
    ],
  );

  const deleteFlow = useDeleteFlow({
    refetch: () => refetch().catch(() => {}),
    onCountChange: (delta) => bumpTrashCount(delta),
  });

  const onDelete = useCallback(
    (msg) => {
      if (!msg) return;
      closeIfFor(msg.id);
      deleteFlow.openDialog([msg.id], 'row');
    },
    [closeIfFor, deleteFlow],
  );

  const onSkip = useCallback(
    (msg) => {
      // Bara lokal navigation — ingen persistens.
      if (currentIndex < queue.length - 1) {
        setActiveId(queue[currentIndex + 1].id);
      }
    },
    [currentIndex, queue],
  );

  if (queue.length === 0) {
    return <EmptyReview />;
  }

  return (
    <>
      <ReviewHeader
        currentIndex={currentIndex}
        total={queue.length}
        onPrev={onPrev}
        onNext={onNext}
        canPrev={currentIndex > 0}
        canNext={currentIndex < queue.length - 1}
      />

      <div className="review-grid" data-testid="review-grid">
        <ReviewQueue queue={queue} activeId={active?.id ?? null} onSelect={onSelect} />
        <PdfPreview message={active} />
        <ReviewForm
          key={active?.id || 'empty'}
          message={active}
          onApprove={onApprove}
          onDelete={onDelete}
          onSkip={onSkip}
          isUploading={uploadingId === active?.id}
        />
      </div>

      <DeleteReasonDialog
        open={deleteFlow.dialogOpen}
        count={deleteFlow.pendingTargets?.ids.length || 0}
        onCancel={deleteFlow.closeDialog}
        onConfirm={deleteFlow.confirmDelete}
        busy={deleteFlow.busy}
      />
    </>
  );
}
