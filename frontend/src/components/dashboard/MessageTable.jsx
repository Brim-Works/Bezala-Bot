import { useCallback, useRef } from 'react';
import VendorLogo from '../VendorLogo.jsx';
import Confidence from '../Confidence.jsx';
import StatusCell from '../StatusCell.jsx';
import BulkCheckbox from '../trash/BulkCheckbox.jsx';
import { SkeletonRow } from '../Skeleton.jsx';
import { IconDownload, IconTrash } from '../../icons/index.jsx';
import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount, fmtRelative } from '../../lib/format.js';

const SKELETON_ROWS = 6;

/* Tabellen har full keyboard-support:
 *  - Tab flyttar in fokus via första raden
 *  - ArrowDown / J flyttar till nästa rad
 *  - ArrowUp / K flyttar till föregående
 *  - Enter aktiverar raden (öppnar drawer)
 *  - Space aktiverar raden (samma som Enter)
 * Rad-klick väljer raden + öppnar drawer. Bulk-checkbox i första kolumnen
 * (valfri via prop selection) stoppar click-propagation så raden inte
 * aktiveras när user bara markerar. */
export default function MessageTable({
  messages,
  selectedId,
  onSelect,
  onActivate,
  isLoading,
  selection,
  onDeleteRow,
  onDownloadRow,
}) {
  const { t, lang } = useI18n();
  const tbodyRef = useRef(null);
  const hasSelection = selection != null;
  const hasDelete = typeof onDeleteRow === 'function';
  const hasDownload = typeof onDownloadRow === 'function';

  const focusRow = useCallback((id) => {
    if (!tbodyRef.current) return;
    const node = tbodyRef.current.querySelector(`[data-row-id="${id}"]`);
    if (node) node.focus();
  }, []);

  const handleKey = useCallback(
    (event, id) => {
      const { key } = event;
      const idx = messages.findIndex((m) => m.id === id);
      if (idx < 0) return;
      if (key === 'ArrowDown' || key === 'j' || key === 'J') {
        event.preventDefault();
        const next = messages[Math.min(messages.length - 1, idx + 1)];
        if (next) {
          onSelect(next.id);
          focusRow(next.id);
        }
      } else if (key === 'ArrowUp' || key === 'k' || key === 'K') {
        event.preventDefault();
        const prev = messages[Math.max(0, idx - 1)];
        if (prev) {
          onSelect(prev.id);
          focusRow(prev.id);
        }
      } else if (key === 'Enter' || key === ' ') {
        event.preventDefault();
        onActivate?.(id);
      }
    },
    [focusRow, messages, onActivate, onSelect],
  );

  const colCount = 7 + (hasSelection ? 1 : 0) + (hasDelete ? 1 : 0);

  const headCells = (
    <tr>
      {hasSelection ? (
        <th className="tbl__col-check">
          <input
            type="checkbox"
            checked={messages.length > 0 && selection.size === messages.length}
            onChange={() => {
              if (selection.size === messages.length) selection.clear();
              else selection.selectAll(messages.map((m) => m.id));
            }}
            aria-label={t.trash.bulk.selectAll}
            data-testid="select-all"
          />
        </th>
      ) : null}
      <th className="tbl__col-time">{t.cols.time}</th>
      <th className="tbl__col-vendor">{t.cols.vendor}</th>
      <th className="tbl__col-subject">{t.cols.subject}</th>
      <th className="tbl__col-file">{t.cols.file}</th>
      <th className="tbl__col-amount">{t.cols.amount}</th>
      <th className="tbl__col-conf">{t.cols.confidence}</th>
      <th className="tbl__col-status">{t.cols.status}</th>
      {hasDelete ? <th className="tbl__col-actions" /> : null}
    </tr>
  );

  if (isLoading && messages.length === 0) {
    return (
      <div className="card table-card" data-testid="message-table-loading">
        <table className="tbl">
          <thead>{headCells}</thead>
          <tbody>
            {Array.from({ length: SKELETON_ROWS }).map((_, i) => (
              <SkeletonRow key={i} cols={colCount} testId={`skeleton-row-${i}`} />
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="card table-empty">
        <p className="muted">{t.empty.noMessages}</p>
      </div>
    );
  }

  return (
    <div className="card table-card">
      <table className="tbl">
        <thead>{headCells}</thead>
        <tbody ref={tbodyRef}>
          {messages.map((m) => {
            const isSelected = selectedId === m.id;
            const isFocusable = isSelected || (selectedId == null && m === messages[0]);
            const isChecked = hasSelection && selection.has(m.id);
            return (
              <tr
                key={m.id}
                tabIndex={isFocusable ? 0 : -1}
                data-row-id={m.id}
                className={`${isSelected ? 'is-selected' : ''} ${isChecked ? 'is-checked' : ''}`}
                onClick={() => {
                  onSelect(m.id);
                  onActivate?.(m.id);
                }}
                onKeyDown={(e) => handleKey(e, m.id)}
                aria-selected={isSelected}
              >
                {hasSelection ? (
                  <td>
                    <BulkCheckbox
                      checked={isChecked}
                      onToggle={() => selection.toggle(m.id)}
                      onRangeSelect={() =>
                        selection.selectRange(
                          messages.map((x) => x.id),
                          m.id,
                        )
                      }
                      ariaLabel={`${t.trash.bulk.selectRow}: ${m.vendor || m.subject || m.id}`}
                    />
                  </td>
                ) : null}
                <td className="mono tbl__time">{fmtRelative(m.processed_at, lang)}</td>
                <td>
                  <span className="vchip">
                    <VendorLogo name={m.vendor} />
                    <span>{m.vendor || <span className="muted">—</span>}</span>
                  </span>
                </td>
                <td className="tbl__subject">
                  {m.subject || <span className="muted">—</span>}
                </td>
                <td className="mono tbl__file">
                  {m.file_name || <span className="muted">—</span>}
                </td>
                <td className="mono tbl__amount">
                  {m.amount != null ? (
                    fmtAmount(m.amount, m.currency, lang)
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
                <td>
                  <Confidence value={m.ai_confidence} />
                </td>
                <td>
                  <StatusCell fileStatus={m.file_status} bezalaStatus={m.bezala_status} />
                </td>
                {hasDelete ? (
                  <td>
                    <div className="row-actions">
                      {hasDownload && m.file_status === 'needs_download' ? (
                        <button
                          type="button"
                          className="row-action row-action--download"
                          title={t.dashboard.downloadRow}
                          aria-label={t.dashboard.downloadRow}
                          data-testid={`row-download-${m.id}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            onDownloadRow(m.id);
                          }}
                        >
                          <IconDownload className="icon sm" />
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className="row-action"
                        title={t.trash.deleteRow}
                        aria-label={t.trash.deleteRow}
                        data-testid={`row-delete-${m.id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          onDeleteRow(m.id);
                        }}
                      >
                        <IconTrash className="icon sm" />
                      </button>
                    </div>
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
