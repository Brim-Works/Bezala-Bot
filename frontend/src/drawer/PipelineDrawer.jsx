import { useEffect, useRef, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { useDrawer } from './DrawerProvider.jsx';
import DrawerTabs from './DrawerTabs.jsx';
import GmailTab from './GmailTab.jsx';
import AiTab from './AiTab.jsx';
import DriveTab from './DriveTab.jsx';
import BezalaTab from './BezalaTab.jsx';
import TripLinkSection from './TripLinkSection.jsx';
import { IconMail, IconRefresh, IconTrash } from '../icons/index.jsx';
import { useDeleteFlow } from '../hooks/useDeleteFlow.js';
import { useTrashCountContext } from '../hooks/TrashCountProvider.jsx';
import DeleteReasonDialog from '../components/trash/DeleteReasonDialog.jsx';
import { withStatuses } from '../api/adapters.js';
import { api, ApiError } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';

function stepStatusMap(message) {
  if (!message) return {};
  const ok = 'ok';
  const err = 'err';
  const idle = 'idle';
  const fileStatus = message.file_status || 'saved';
  return {
    gmail: ok,
    ai: message.ai_confidence != null ? ok : idle,
    drive: fileStatus === 'saved' ? ok : fileStatus === 'error' ? err : idle,
    bezala:
      message.bezala_status === 'transferred'
        ? ok
        : message.bezala_status === 'error'
          ? err
          : idle,
  };
}

/* Focus-trap är enkel: vi fokuserar close-knappen när drawern öppnas
 * och returnerar fokus till trigger-elementet vid stängning (tack vare
 * React:s default-beteende). */
export default function PipelineDrawer({ onRefetch }) {
  const { t } = useI18n();
  const {
    selectedMessage,
    activeTab,
    isOpen,
    setTab,
    closeDrawer,
    closeIfFor,
    selectMessage,
  } = useDrawer();
  const { bump: bumpTrashCount, bumpMessagesVersion } = useTrashCountContext();
  const closeBtnRef = useRef(null);
  const toast = useToast();
  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessWarning, setReprocessWarning] = useState(null);
  // null | { id, message }

  // Gemensam handler: när en tab uppdaterar raden (t.ex. fetch-pdf-from-url
  // lyckas) → uppdatera drawer-state + refresha Dashboard-listan.
  const onMessageUpdated = (updated) => {
    if (!updated) return;
    selectMessage(withStatuses(updated));
    bumpMessagesVersion();
    onRefetch?.();
  };

  const deleteFlow = useDeleteFlow({
    refetch: () => {
      bumpMessagesVersion();
      onRefetch?.();
    },
    onCountChange: (delta) => bumpTrashCount(delta),
  });

  const deletingId = selectedMessage?.id ?? null;

  const openDeleteDialog = () => {
    if (deletingId == null) return;
    // Stäng drawern direkt — användaren har bekräftat val genom att klicka
    // ikonen. Dialogen renderas utanför drawer-subträdet så den kvarstår.
    closeIfFor(deletingId);
    deleteFlow.openDialog([deletingId], 'row');
  };

  const runReprocess = async (force) => {
    if (deletingId == null) return;
    setReprocessing(true);
    try {
      const result = await api.reprocessMessageFull(deletingId, { force });
      if (result?.warning && result?.is_coupled) {
        // Backend ber om bekräftelse innan vi raderar en kopplad rad
        setReprocessWarning({
          id: deletingId,
          message:
            result.message || t.drawer.reprocess.confirmCoupled,
        });
        return;
      }
      toast.show({ kind: 'ok', message: t.drawer.reprocess.success });
      // Stäng drawern + refresha listan — raden är borta tills nästa scan
      // tar in den på nytt.
      setReprocessWarning(null);
      bumpMessagesVersion();
      onRefetch?.();
      closeDrawer();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: t.drawer.reprocess.error.replace('{error}', detail),
      });
    } finally {
      setReprocessing(false);
    }
  };

  const onReprocessClick = () => runReprocess(false);
  const onReprocessConfirm = () => runReprocess(true);
  const onReprocessCancel = () => {
    if (reprocessing) return;
    setReprocessWarning(null);
  };

  useEffect(() => {
    if (!isOpen) return undefined;
    function onKey(e) {
      if (e.key === 'Escape') closeDrawer();
    }
    window.addEventListener('keydown', onKey);
    // Flytta fokus till close-knappen för tillgänglighet
    const t0 = setTimeout(() => closeBtnRef.current?.focus(), 50);
    // Låt body inte scrolla när drawern är öppen
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', onKey);
      clearTimeout(t0);
      document.body.style.overflow = prev;
    };
  }, [isOpen, closeDrawer]);

  // Dialog hålls monterad även när drawern stängs så delete-flödet kan
  // avslutas utanför drawer-subträdet.
  const reprocessConfirm = reprocessWarning ? (
    <div
      className="modal-shell"
      role="dialog"
      aria-modal="true"
      aria-label={t.drawer.reprocess.button}
      onClick={(e) => {
        if (e.target === e.currentTarget) onReprocessCancel();
      }}
      data-testid="drawer-reprocess-confirm"
    >
      <div className="modal-card card-pad">
        <h3 className="modal-card__title">{t.drawer.reprocess.button}</h3>
        <p className="muted">{reprocessWarning.message}</p>
        <footer className="modal-card__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onReprocessCancel}
            disabled={reprocessing}
          >
            {t.common.cancel}
          </button>
          <button
            type="button"
            className="btn primary"
            onClick={onReprocessConfirm}
            disabled={reprocessing}
            data-testid="drawer-reprocess-confirm-btn"
          >
            {reprocessing
              ? t.drawer.reprocess.loading
              : t.drawer.reprocess.confirmAction}
          </button>
        </footer>
      </div>
    </div>
  ) : null;

  const dialog = (
    <>
      <DeleteReasonDialog
        open={deleteFlow.dialogOpen}
        count={deleteFlow.pendingTargets?.ids.length || 0}
        onCancel={deleteFlow.closeDialog}
        onConfirm={deleteFlow.confirmDelete}
        busy={deleteFlow.busy}
      />
      {reprocessConfirm}
    </>
  );

  if (!isOpen || !selectedMessage) return dialog;

  const statusMap = stepStatusMap(selectedMessage);

  return (
    <>
      <div
        className="drawer-overlay"
        onClick={closeDrawer}
        data-testid="drawer-overlay"
        aria-hidden="true"
      />
      <aside
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-label={t.drawer.title}
        data-testid="drawer"
      >
        <header className="drawer__head">
          <div className="drawer__head-icon" aria-hidden="true">
            <IconMail className="icon" />
          </div>
          <div className="drawer__head-body">
            <div className="drawer__head-title">{t.drawer.title}</div>
            <div className="drawer__head-sub mono">
              {selectedMessage.file_name || selectedMessage.subject || '—'}
            </div>
          </div>
          <button
            type="button"
            className="drawer__head-action"
            onClick={onReprocessClick}
            disabled={reprocessing}
            aria-label={t.drawer.reprocess.button}
            title={t.drawer.reprocess.button}
            data-testid="drawer-reprocess"
          >
            <IconRefresh className="icon sm" />
          </button>
          <button
            type="button"
            className="drawer__head-action"
            onClick={openDeleteDialog}
            aria-label={t.drawer.deleteLabel}
            title={t.drawer.delete}
            data-testid="drawer-delete"
          >
            <IconTrash className="icon sm" />
          </button>
          <button
            ref={closeBtnRef}
            type="button"
            className="btn ghost"
            onClick={closeDrawer}
            aria-label={t.common.close}
            data-testid="drawer-close"
          >
            ×
          </button>
        </header>

        <DrawerTabs active={activeTab} onChange={setTab} statusMap={statusMap} />

        <div className="drawer__body">
          {activeTab === 'gmail' ? (
            <GmailTab
              message={selectedMessage}
              onUpdated={onMessageUpdated}
              onTabChange={setTab}
            />
          ) : null}
          {activeTab === 'ai' ? <AiTab message={selectedMessage} /> : null}
          {activeTab === 'drive' ? (
            <DriveTab
              message={selectedMessage}
              onUpdated={onMessageUpdated}
            />
          ) : null}
          {activeTab === 'bezala' ? (
            <BezalaTab
              message={selectedMessage}
              onRefetch={onRefetch}
              onClose={closeDrawer}
            />
          ) : null}

          <TripLinkSection message={selectedMessage} />
        </div>
      </aside>
      {dialog}
    </>
  );
}
