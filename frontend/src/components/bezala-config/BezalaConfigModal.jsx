import { useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';

/* Add/edit-modal för vendor-mappningar. Skickar samma payload-form vid
 * båda — kallaren bestämmer om det blir POST eller PATCH. */
export default function BezalaConfigModal({
  mode, mapping, onCancel, onSubmit,
}) {
  const { t } = useI18n();
  const strings = t.settings.bezalaConfig;
  const isEdit = mode === 'edit';

  const [vendorPattern, setVendorPattern] = useState(
    mapping?.vendor_pattern || '',
  );
  const [accountId, setAccountId] = useState(
    mapping?.bezala_account_id != null
      ? String(mapping.bezala_account_id) : '',
  );
  const [vatRate, setVatRate] = useState(
    mapping?.vat_rate != null ? String(mapping.vat_rate) : '',
  );
  const [description, setDescription] = useState(
    mapping?.description_override || '',
  );
  const [saving, setSaving] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    const trimmedPattern = vendorPattern.trim();
    if (!trimmedPattern) return;
    const accountIdNum = Number(accountId);
    const vatRateNum = Number(vatRate);
    if (!Number.isFinite(accountIdNum) || accountIdNum <= 0) return;
    if (!Number.isFinite(vatRateNum) || vatRateNum < 0 || vatRateNum > 100) {
      return;
    }
    const payload = {
      vendor_pattern: trimmedPattern,
      bezala_account_id: Math.trunc(accountIdNum),
      vat_rate: vatRateNum,
      description_override: description.trim() || null,
    };
    setSaving(true);
    try {
      await onSubmit(payload);
    } catch (_err) {
      // kallaren visar toast.
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-label={isEdit ? strings.modal.editTitle : strings.modal.addTitle}
      data-testid="bezala-config-modal"
    >
      <form className="modal-card card-pad" onSubmit={submit}>
        <h3 className="modal-card__title">
          {isEdit ? strings.modal.editTitle : strings.modal.addTitle}
        </h3>
        <label className="form-row">
          <span>{strings.modal.vendor}</span>
          <input
            type="text"
            value={vendorPattern}
            onChange={(e) => setVendorPattern(e.target.value)}
            placeholder={strings.modal.vendorPlaceholder}
            autoFocus
            data-testid="bezala-config-vendor-input"
          />
        </label>
        <label className="form-row">
          <span>{strings.modal.account}</span>
          <input
            type="number"
            min="1"
            step="1"
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            placeholder={strings.modal.accountPlaceholder}
            data-testid="bezala-config-account-input"
          />
        </label>
        <label className="form-row">
          <span>{strings.modal.vat}</span>
          <input
            type="number"
            min="0"
            max="100"
            step="0.01"
            value={vatRate}
            onChange={(e) => setVatRate(e.target.value)}
            placeholder={strings.modal.vatPlaceholder}
            data-testid="bezala-config-vat-input"
          />
        </label>
        <label className="form-row">
          <span>{strings.modal.description}</span>
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={strings.modal.descriptionPlaceholder}
            data-testid="bezala-config-description-input"
          />
        </label>
        <footer className="modal-card__actions">
          <button
            type="button"
            className="btn ghost"
            onClick={onCancel}
            disabled={saving}
            data-testid="bezala-config-cancel"
          >
            {strings.modal.cancel}
          </button>
          <button
            type="submit"
            className="btn primary"
            disabled={saving || !vendorPattern.trim() || !accountId || !vatRate}
            data-testid="bezala-config-submit"
          >
            {saving ? t.common.loading : strings.modal.save}
          </button>
        </footer>
      </form>
    </div>
  );
}
