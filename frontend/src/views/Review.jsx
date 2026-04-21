import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api, ApiError } from '../api/client.js';
import { withStatuses } from '../api/adapters.js';
import { useApiData } from '../hooks/useApiData.js';
import { useToast } from '../lib/toast.jsx';
import { useDrawer } from '../drawer/DrawerProvider.jsx';

import ReviewHeader from '../components/review/ReviewHeader.jsx';
import ReviewQueue from '../components/review/ReviewQueue.jsx';
import PdfPreview from '../components/review/PdfPreview.jsx';
import ReviewForm from '../components/review/ReviewForm.jsx';
import EmptyReview from '../components/review/EmptyReview.jsx';

function sortOldestFirst(a, b) {
  const ta = new Date(a.processed_at || 0).getTime();
  const tb = new Date(b.processed_at || 0).getTime();
  return ta - tb;
}

export default function Review() {
  const { t } = useI18n();
  const toast = useToast();
  const { closeIfFor } = useDrawer();
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

  const queue = useMemo(() => {
    return allMessages
      .filter((m) => m.bezala_status === 'pending')
      .filter((m) => !optimisticallyRemoved.has(m.id))
      .sort(sortOldestFirst);
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
    async (msg) => {
      if (!msg || uploadingId === msg.id) return;
      setUploadingId(msg.id);
      setOptimisticallyRemoved((s) => {
        const next = new Set(s);
        next.add(msg.id);
        return next;
      });
      try {
        await api.uploadToBezala(msg.id);
        toast.show({ kind: 'ok', message: t.review.toast.uploaded });
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
    [closeIfFor, refetch, t.review.toast.uploadFailed, t.review.toast.uploaded, toast, uploadingId],
  );

  const onReject = useCallback(
    (msg) => {
      toast.show({ kind: 'warn', message: t.review.toast.rejectUnsupported });
      if (currentIndex < queue.length - 1) {
        setActiveId(queue[currentIndex + 1].id);
      } else if (queue.length > 1) {
        setActiveId(queue[0].id);
      }
    },
    [currentIndex, queue, t.review.toast.rejectUnsupported, toast],
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
          onReject={onReject}
          onSkip={onSkip}
          isUploading={uploadingId === active?.id}
        />
      </div>
    </>
  );
}
