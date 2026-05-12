import { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { api, ApiError } from '../../api/client.js';
import { useToast } from '../../lib/toast.jsx';
import BezalaConfigModal from './BezalaConfigModal.jsx';

/* Bezala config-admin — leverantör → Bezala-konto + moms-override.
 * Bug-bakgrund: Bezalas default-moms för konto 67113 (Parkering) är 14% (id 864),
 * men finsk parkering är 25.5%. Här kan användaren skapa explicita mappningar.
 * Själva applicerandet i upload-flödet kommer i ett senare PR. */
export default function BezalaConfigSection() {
  const { t } = useI18n();
  const toast = useToast();
  const [mappings, setMappings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [modalState, setModalState] = useState(null); // null | { mode, mapping? }
  const [removingId, setRemovingId] = useState(null);

  const strings = t.settings.bezalaConfig;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.bezalaConfigList();
      setMappings((data && data.mappings) || []);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${strings.toast.loadFailed}: ${detail}`,
      });
    } finally {
      setLoading(false);
    }
  }, [strings.toast.loadFailed, toast]);

  useEffect(() => {
    reload();
  }, [reload]);

  const onCreate = async (payload) => {
    try {
      await api.bezalaConfigCreate(payload);
      toast.show({ kind: 'ok', message: strings.toast.created });
      setModalState(null);
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${strings.toast.createFailed}: ${detail}`,
      });
      throw err;
    }
  };

  const onUpdate = async (id, payload) => {
    try {
      await api.bezalaConfigUpdate(id, payload);
      toast.show({ kind: 'ok', message: strings.toast.updated });
      setModalState(null);
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${strings.toast.updateFailed}: ${detail}`,
      });
      throw err;
    }
  };

  const onRemove = async (mapping) => {
    const confirmMsg = strings.deleteConfirm.replace(
      '{pattern}', mapping.vendor_pattern,
    );
    if (typeof window !== 'undefined' && !window.confirm(confirmMsg)) return;
    setRemovingId(mapping.id);
    try {
      await api.bezalaConfigDelete(mapping.id);
      toast.show({ kind: 'ok', message: strings.toast.removed });
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${strings.toast.removeFailed}: ${detail}`,
      });
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <section
      className="settings-section"
      data-testid="bezala-config-section"
    >
      <header className="settings-section__head">
        <h2 className="settings-section__title">{strings.title}</h2>
        <p className="settings-section__lead muted">{strings.description}</p>
      </header>

      {loading ? (
        <p className="muted">{t.common.loading}</p>
      ) : (
        <>
          {mappings.length === 0 ? (
            <p
              className="muted"
              data-testid="bezala-config-empty"
            >
              {strings.empty}
            </p>
          ) : (
            <table
              className="bezala-config-table"
              data-testid="bezala-config-list"
            >
              <thead>
                <tr>
                  <th>{strings.columns.vendor}</th>
                  <th>{strings.columns.account}</th>
                  <th>{strings.columns.vat}</th>
                  <th>{strings.columns.description}</th>
                  <th>{strings.columns.actions}</th>
                </tr>
              </thead>
              <tbody>
                {mappings.map((m) => (
                  <tr
                    key={m.id}
                    data-testid={`bezala-config-row-${m.id}`}
                  >
                    <td className="mono">{m.vendor_pattern}</td>
                    <td className="mono">{m.bezala_account_id}</td>
                    <td className="mono">{m.vat_rate}</td>
                    <td>{m.description_override || '—'}</td>
                    <td>
                      <button
                        type="button"
                        className="btn ghost btn--sm"
                        onClick={() =>
                          setModalState({ mode: 'edit', mapping: m })
                        }
                        data-testid={`bezala-config-edit-${m.id}`}
                      >
                        {strings.edit}
                      </button>
                      <button
                        type="button"
                        className="btn ghost btn--sm"
                        onClick={() => onRemove(m)}
                        disabled={removingId === m.id}
                        data-testid={`bezala-config-remove-${m.id}`}
                      >
                        {strings.remove}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <button
            type="button"
            className="btn"
            onClick={() => setModalState({ mode: 'create' })}
            data-testid="bezala-config-add"
          >
            {strings.addButton}
          </button>
        </>
      )}

      {modalState ? (
        <BezalaConfigModal
          mode={modalState.mode}
          mapping={modalState.mapping}
          onCancel={() => setModalState(null)}
          onSubmit={(payload) =>
            modalState.mode === 'edit'
              ? onUpdate(modalState.mapping.id, payload)
              : onCreate(payload)
          }
        />
      ) : null}
    </section>
  );
}
