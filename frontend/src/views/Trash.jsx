import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { useApiData } from '../hooks/useApiData.js';
import { useSelection } from '../hooks/useSelection.js';
import { useToast } from '../lib/toast.jsx';
import { fmtAmount, fmtDate } from '../lib/format.js';
import { displayVendor } from '../lib/vendorFromSender.js';

import VendorLogo from '../components/VendorLogo.jsx';
import Pill from '../components/Pill.jsx';
import BulkCheckbox from '../components/trash/BulkCheckbox.jsx';
import HardDeleteDialog from '../components/trash/HardDeleteDialog.jsx';
import { IconRestore, IconTrash } from '../icons/index.jsx';

const REASON_KIND = {
  manual: 'muted',
  calendar: 'accent',
  spam: 'err',
  misclassified: 'warn',
};

function formatReason(t, reason) {
  if (!reason) return t.trash.reasons.manual;
  return t.trash.reasons[reason] || reason;
}

export default function Trash() {
  const { t, lang } = useI18n();
  const toast = useToast();
  const selection = useSelection();
  const [busyIds, setBusyIds] = useState(() => new Set());
  const [hardDialog, setHardDialog] = useState({ open: false, ids: null, mode: 'row' });

  const loader = useCallback(() => api.trashList(200), []);
  const { data: rawRows, isLoading, refetch } = useApiData(loader, []);
  const rows = rawRows || [];

  // Nollställ valda id:n som inte längre finns (t.ex. efter restore/hard-delete)
  useEffect(() => {
    const existing = new Set(rows.map((r) => r.id));
    const stillValid = selection.ids.filter((id) => existing.has(id));
    if (stillValid.length !== selection.ids.length) {
      selection.selectAll(stillValid);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  const markBusy = (ids) =>
    setBusyIds((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.add(id));
      return next;
    });
  const clearBusy = (ids) =>
    setBusyIds((prev) => {
      const next = new Set(prev);
      ids.forEach((id) => next.delete(id));
      return next;
    });

  const restoreRow = useCallback(
    async (id) => {
      markBusy([id]);
      try {
        await api.restoreMessage(id);
        toast.show({ kind: 'ok', message: t.trash.toast.restored });
        await refetch();
      } catch (err) {
        toast.show({
          kind: 'err',
          message: `${t.trash.toast.restoreFailed}: ${err.message || err}`,
        });
      } finally {
        clearBusy([id]);
      }
    },
    [refetch, t.trash.toast.restoreFailed, t.trash.toast.restored, toast],
  );

  const bulkRestore = useCallback(async () => {
    const ids = [...selection.ids];
    if (ids.length === 0) return;
    markBusy(ids);
    try {
      await Promise.all(ids.map((id) => api.restoreMessage(id)));
      selection.clear();
      toast.show({
        kind: 'ok',
        message: `${t.trash.toast.restoredBulk} (${ids.length})`,
      });
      await refetch();
    } catch (err) {
      toast.show({
        kind: 'err',
        message: `${t.trash.toast.restoreFailed}: ${err.message || err}`,
      });
    } finally {
      clearBusy(ids);
    }
  }, [refetch, selection, t.trash.toast.restoreFailed, t.trash.toast.restoredBulk, toast]);

  const openHardDelete = useCallback((ids, mode = 'row') => {
    setHardDialog({ open: true, ids, mode });
  }, []);

  const closeHardDelete = () => setHardDialog({ open: false, ids: null, mode: 'row' });

  const confirmHardDelete = useCallback(
    async ({ purge_drive }) => {
      const { ids, mode } = hardDialog;
      if (!ids && mode !== 'empty-trash') return;
      markBusy(ids || []);
      try {
        if (mode === 'empty-trash') {
          await api.emptyTrash({ purgeDrive: purge_drive });
          selection.clear();
          toast.show({ kind: 'ok', message: t.trash.toast.emptied });
        } else if (ids.length === 1) {
          await api.hardDeleteMessage(ids[0], { purgeDrive: purge_drive });
          toast.show({ kind: 'ok', message: t.trash.toast.hardDeleted });
        } else {
          await api.bulkDelete({ ids, permanent: true, purge_drive });
          selection.clear();
          toast.show({
            kind: 'ok',
            message: `${t.trash.toast.hardDeletedBulk} (${ids.length})`,
          });
        }
        await refetch();
      } catch (err) {
        toast.show({
          kind: 'err',
          message: `${t.trash.toast.hardDeleteFailed}: ${err.message || err}`,
        });
      } finally {
        if (ids) clearBusy(ids);
        closeHardDelete();
      }
    },
    [hardDialog, refetch, selection, t.trash.toast, toast],
  );

  const allSelected =
    rows.length > 0 && selection.size === rows.length;

  const toggleAll = () => {
    if (allSelected) selection.clear();
    else selection.selectAll(rows.map((r) => r.id));
  };

  const showBulkBar = selection.size > 0;

  return (
    <>
      <div className="section-header" data-testid="trash-view">
        <h2>{t.trash.title}</h2>
        <div className="section-header__meta">
          <span className="muted">
            <span className="mono">{rows.length}</span> {t.trash.rowCount}
          </span>
          <button
            type="button"
            className="btn danger"
            onClick={() => openHardDelete([], 'empty-trash')}
            disabled={rows.length === 0}
            data-testid="empty-trash"
          >
            <IconTrash className="icon sm" />
            {t.trash.emptyTrash}
          </button>
        </div>
      </div>

      {showBulkBar ? (
        <div className="bulk-bar" data-testid="trash-bulk-bar">
          <span className="bulk-bar__count">
            <span className="mono">{selection.size}</span> {t.trash.bulk.selected}
          </span>
          <div className="bulk-bar__spacer" />
          <button
            type="button"
            className="btn ghost"
            onClick={selection.clear}
          >
            {t.trash.bulk.clear}
          </button>
          <button
            type="button"
            className="btn"
            onClick={bulkRestore}
            data-testid="bulk-restore"
          >
            <IconRestore className="icon sm" /> {t.trash.bulk.restore}
          </button>
          <button
            type="button"
            className="btn danger"
            onClick={() => openHardDelete([...selection.ids])}
            data-testid="bulk-hard-delete"
          >
            <IconTrash className="icon sm" /> {t.trash.bulk.hardDelete}
          </button>
        </div>
      ) : null}

      {isLoading && rows.length === 0 ? (
        <div className="card table-empty">
          <p className="muted">{t.common.loading}</p>
        </div>
      ) : rows.length === 0 ? (
        <div className="card table-empty">
          <p className="muted">{t.trash.empty}</p>
        </div>
      ) : (
        <div className="card table-card">
          <table className="tbl">
            <thead>
              <tr>
                <th className="tbl__col-check">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    aria-label={t.trash.bulk.selectAll}
                    data-testid="trash-select-all"
                  />
                </th>
                <th>{t.trash.cols.deletedAt}</th>
                <th>{t.cols.vendor}</th>
                <th>{t.cols.subject}</th>
                <th>{t.cols.amount}</th>
                <th>{t.trash.cols.reason}</th>
                <th>{t.trash.cols.actions}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m) => (
                <tr
                  key={m.id}
                  className={selection.has(m.id) ? 'is-selected' : ''}
                  data-row-id={m.id}
                >
                  <td>
                    <BulkCheckbox
                      checked={selection.has(m.id)}
                      onToggle={() => selection.toggle(m.id)}
                      onRangeSelect={() =>
                        selection.selectRange(rows.map((r) => r.id), m.id)
                      }
                      ariaLabel={`${t.trash.cols.select} ${displayVendor(m) || m.subject || m.id}`}
                    />
                  </td>
                  <td className="mono">{fmtDate(m.deleted_at, lang)}</td>
                  <td>
                    <span className="vchip">
                      <VendorLogo name={displayVendor(m)} />
                      <span>{displayVendor(m) || <span className="muted">—</span>}</span>
                    </span>
                  </td>
                  <td className="tbl__subject">
                    {m.subject || <span className="muted">—</span>}
                  </td>
                  <td className="mono tbl__amount">
                    {m.amount != null ? (
                      fmtAmount(m.amount, m.currency, lang)
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td>
                    <Pill kind={REASON_KIND[m.delete_reason] || 'muted'}>
                      {formatReason(t, m.delete_reason)}
                    </Pill>
                  </td>
                  <td>
                    <div className="trash-row__actions">
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => restoreRow(m.id)}
                        disabled={busyIds.has(m.id)}
                        data-testid={`restore-${m.id}`}
                      >
                        <IconRestore className="icon sm" /> {t.trash.restore}
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm danger"
                        onClick={() => openHardDelete([m.id])}
                        disabled={busyIds.has(m.id)}
                        data-testid={`hard-delete-${m.id}`}
                      >
                        <IconTrash className="icon sm" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <HardDeleteDialog
        open={hardDialog.open}
        count={hardDialog.mode === 'empty-trash' ? rows.length : hardDialog.ids?.length || 0}
        onCancel={closeHardDelete}
        onConfirm={confirmHardDelete}
        mode={hardDialog.mode}
        busy={
          hardDialog.ids
            ? hardDialog.ids.some((id) => busyIds.has(id))
            : false
        }
      />
    </>
  );
}
