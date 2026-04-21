import { useCallback, useState } from 'react';
import { api } from '../api/client.js';
import { useI18n } from '../i18n/useI18n.jsx';
import { useToast } from '../lib/toast.jsx';

/* Delar delete-flödet mellan Dashboard, Review och Log.
 * - openReasonDialog() visar reason-modalen
 * - confirmDelete(reason) soft-deletar + visar undo-toast
 * - bulkDelete(ids, reason) soft-deletar flera + undo-toast
 *
 * Refetch-callbacken ansvarar vyn själv — vi triggar den efter delete
 * och efter eventuell undo. */
export function useDeleteFlow({ refetch, onCountChange }) {
  const { t } = useI18n();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [pendingTargets, setPendingTargets] = useState(null);
  // { ids: number[], mode: 'row' | 'bulk' }

  const openDialog = useCallback((ids, mode = 'row') => {
    setPendingTargets({ ids, mode });
  }, []);

  const closeDialog = useCallback(() => setPendingTargets(null), []);

  const undoSoftDelete = useCallback(
    async (ids) => {
      try {
        await Promise.all(ids.map((id) => api.restoreMessage(id)));
        onCountChange?.(-ids.length);
        toast.show({ kind: 'ok', message: t.trash.toast.restored });
        refetch?.();
      } catch (err) {
        toast.show({
          kind: 'err',
          message: `${t.trash.toast.restoreFailed}: ${err.message || err}`,
        });
      }
    },
    [onCountChange, refetch, t.trash.toast.restoreFailed, t.trash.toast.restored, toast],
  );

  const confirmDelete = useCallback(
    async (reason) => {
      if (!pendingTargets) return;
      const { ids, mode } = pendingTargets;
      setBusy(true);
      try {
        if (mode === 'bulk' || ids.length > 1) {
          await api.bulkDelete({ ids, reason });
        } else {
          await api.softDeleteMessage(ids[0], reason);
        }
        onCountChange?.(ids.length);
        toast.show({
          kind: 'ok',
          message:
            ids.length > 1
              ? `${t.trash.toast.deletedBulk} (${ids.length})`
              : t.trash.toast.deleted,
          timeout: 5000,
          action: { label: t.trash.toast.undo, onClick: () => undoSoftDelete(ids) },
        });
        refetch?.();
      } catch (err) {
        toast.show({
          kind: 'err',
          message: `${t.trash.toast.deleteFailed}: ${err.message || err}`,
        });
      } finally {
        setBusy(false);
        setPendingTargets(null);
      }
    },
    [onCountChange, pendingTargets, refetch, t.trash.toast, toast, undoSoftDelete],
  );

  return {
    pendingTargets,
    dialogOpen: pendingTargets !== null,
    openDialog,
    closeDialog,
    confirmDelete,
    busy,
  };
}
