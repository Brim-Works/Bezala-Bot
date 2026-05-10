import { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { api, ApiError } from '../../api/client.js';
import { useToast } from '../../lib/toast.jsx';

/* FAS 11.1.1 — SaaS / återkommande prenumerationer som aldrig ska räknas
 * som resekvitton. System-listan är icke-redigerbar; användaren kan lägga
 * till och ta bort egna patterns. */

export default function ExcludedVendorsSection() {
  const { t } = useI18n();
  const toast = useToast();
  const [vendors, setVendors] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAllSystem, setShowAllSystem] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [pattern, setPattern] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [removingId, setRemovingId] = useState(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.excludedVendorsList();
      setVendors((data && data.vendors) || []);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.settings.excludedVendors.toast.loadFailed}: ${detail}`,
      });
    } finally {
      setLoading(false);
    }
  }, [t, toast]);

  useEffect(() => {
    reload();
  }, [reload]);

  const onAdd = async (event) => {
    event.preventDefault();
    if (!pattern.trim()) return;
    setSaving(true);
    try {
      await api.excludedVendorsAdd({
        pattern: pattern.trim(),
        description: description.trim(),
      });
      toast.show({
        kind: 'ok',
        message: t.settings.excludedVendors.toast.added,
      });
      setShowAddModal(false);
      setPattern('');
      setDescription('');
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.settings.excludedVendors.toast.addFailed}: ${detail}`,
      });
    } finally {
      setSaving(false);
    }
  };

  const onRemove = async (vendor) => {
    setRemovingId(vendor.id);
    try {
      await api.excludedVendorsRemove(vendor.id);
      toast.show({
        kind: 'ok',
        message: t.settings.excludedVendors.toast.removed,
      });
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.settings.excludedVendors.toast.removeFailed}: ${detail}`,
      });
    } finally {
      setRemovingId(null);
    }
  };

  const systemVendors = vendors.filter((v) => v.added_by === 'system');
  const userVendors = vendors.filter((v) => v.added_by === 'user');

  return (
    <section
      className="settings-section"
      data-testid="excluded-vendors-section"
    >
      <header className="settings-section__head">
        <h2 className="settings-section__title">
          {t.settings.excludedVendors.title}
        </h2>
        <p className="settings-section__lead muted">
          {t.settings.excludedVendors.description}
        </p>
      </header>

      {loading ? (
        <p className="muted">{t.common.loading}</p>
      ) : (
        <>
          <div className="excluded-vendors__group">
            <h3 className="excluded-vendors__group-title muted">
              {t.settings.excludedVendors.systemList} ({systemVendors.length})
            </h3>
            {systemVendors.length === 0 ? (
              <p className="muted">—</p>
            ) : (
              <>
                <ul
                  className="excluded-vendors__list"
                  data-testid="excluded-vendors-system-list"
                >
                  {(showAllSystem
                    ? systemVendors
                    : systemVendors.slice(0, 6)
                  ).map((v) => (
                    <li
                      key={v.id}
                      className="excluded-vendors__chip excluded-vendors__chip--system"
                      data-testid={`excluded-vendor-system-${v.id}`}
                    >
                      <span className="mono">{v.pattern}</span>
                      {v.description ? (
                        <span className="muted"> · {v.description}</span>
                      ) : null}
                    </li>
                  ))}
                </ul>
                {systemVendors.length > 6 ? (
                  <button
                    type="button"
                    className="btn ghost btn--sm"
                    onClick={() => setShowAllSystem((v) => !v)}
                    data-testid="excluded-vendors-toggle-all"
                  >
                    {showAllSystem
                      ? t.settings.excludedVendors.hideAll
                      : t.settings.excludedVendors.showAll}
                  </button>
                ) : null}
              </>
            )}
          </div>

          <div className="excluded-vendors__group">
            <h3 className="excluded-vendors__group-title muted">
              {t.settings.excludedVendors.myAdditions} ({userVendors.length})
            </h3>
            {userVendors.length === 0 ? (
              <p
                className="muted"
                data-testid="excluded-vendors-user-empty"
              >
                {t.settings.excludedVendors.userEmpty}
              </p>
            ) : (
              <ul
                className="excluded-vendors__list"
                data-testid="excluded-vendors-user-list"
              >
                {userVendors.map((v) => (
                  <li
                    key={v.id}
                    className="excluded-vendors__chip excluded-vendors__chip--user"
                    data-testid={`excluded-vendor-user-${v.id}`}
                  >
                    <span className="mono">{v.pattern}</span>
                    {v.description ? (
                      <span className="muted"> · {v.description}</span>
                    ) : null}
                    <button
                      type="button"
                      className="btn ghost btn--sm"
                      onClick={() => onRemove(v)}
                      disabled={removingId === v.id}
                      data-testid={`excluded-vendor-remove-${v.id}`}
                      aria-label={t.common.remove}
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <button
              type="button"
              className="btn"
              onClick={() => setShowAddModal(true)}
              data-testid="excluded-vendors-add"
            >
              {t.settings.excludedVendors.addVendor}
            </button>
          </div>
        </>
      )}

      {showAddModal ? (
        <div
          className="modal-overlay"
          role="dialog"
          aria-label={t.settings.excludedVendors.addModal.title}
          data-testid="excluded-vendors-add-modal"
        >
          <form className="modal-card card-pad" onSubmit={onAdd}>
            <h3 className="modal-card__title">
              {t.settings.excludedVendors.addModal.title}
            </h3>
            <label className="form-row">
              <span>{t.settings.excludedVendors.addModal.pattern}</span>
              <input
                type="text"
                value={pattern}
                onChange={(e) => setPattern(e.target.value)}
                autoFocus
                data-testid="excluded-vendors-pattern-input"
              />
            </label>
            <label className="form-row">
              <span>{t.settings.excludedVendors.addModal.description}</span>
              <input
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                data-testid="excluded-vendors-description-input"
              />
            </label>
            <footer className="modal-card__actions">
              <button
                type="button"
                className="btn ghost"
                onClick={() => setShowAddModal(false)}
                disabled={saving}
                data-testid="excluded-vendors-cancel"
              >
                {t.settings.excludedVendors.addModal.cancel}
              </button>
              <button
                type="submit"
                className="btn primary"
                disabled={saving || !pattern.trim()}
                data-testid="excluded-vendors-confirm"
              >
                {saving
                  ? t.common.loading
                  : t.settings.excludedVendors.addModal.add}
              </button>
            </footer>
          </form>
        </div>
      ) : null}
    </section>
  );
}
