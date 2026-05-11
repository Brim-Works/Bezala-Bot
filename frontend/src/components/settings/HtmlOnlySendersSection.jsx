import { useCallback, useEffect, useState } from 'react';
import { useI18n } from '../../i18n/useI18n.jsx';
import { api, ApiError } from '../../api/client.js';
import { useToast } from '../../lib/toast.jsx';

/* HTML-only avsändare: senders som skickar kvittot som HTML i mail-bodyn
 * istället för PDF-bilaga (Skånetrafiken, Moovy notifieringar, Cursor,
 * Airport LRS). För dem skippar Gmail-queryn has:attachment-filtret och
 * html_to_pdf-konverteraren producerar PDF:en av bodyn.
 *
 * Per rad: toggle aktiv/inaktiv + radera. Användaren kan lägga till
 * fler via inline form. Spegelmönster av ExcludedVendorsSection. */

export default function HtmlOnlySendersSection() {
  const { t } = useI18n();
  const toast = useToast();
  const [senders, setSenders] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAddForm, setShowAddForm] = useState(false);
  const [pattern, setPattern] = useState('');
  const [description, setDescription] = useState('');
  const [saving, setSaving] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const tt = t.settings.htmlOnlySenders;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.htmlOnlySendersList();
      setSenders((data && data.senders) || []);
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `${tt.toast.loadFailed}: ${detail}` });
    } finally {
      setLoading(false);
    }
  }, [tt, toast]);

  useEffect(() => { reload(); }, [reload]);

  const onAdd = async (event) => {
    event.preventDefault();
    if (!pattern.trim()) return;
    setSaving(true);
    try {
      await api.htmlOnlySendersAdd({
        sender_pattern: pattern.trim(),
        description: description.trim(),
      });
      toast.show({ kind: 'ok', message: tt.toast.added });
      setShowAddForm(false);
      setPattern('');
      setDescription('');
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `${tt.toast.addFailed}: ${detail}` });
    } finally {
      setSaving(false);
    }
  };

  const onRemove = async (row) => {
    if (!window.confirm(
      (tt.deleteConfirm || 'Remove {pattern}?').replace(
        '{pattern}', row.sender_pattern,
      ),
    )) return;
    setBusyId(row.id);
    try {
      await api.htmlOnlySendersRemove(row.id);
      toast.show({ kind: 'ok', message: tt.toast.removed });
      await reload();
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `${tt.toast.removeFailed}: ${detail}` });
    } finally {
      setBusyId(null);
    }
  };

  const onToggle = async (row) => {
    setBusyId(row.id);
    try {
      await api.htmlOnlySendersToggle(row.id, !row.is_active);
      // Uppdatera lokalt utan att rerunda hela listan — snabbare UI.
      setSenders((cur) => cur.map(
        (r) => r.id === row.id ? { ...r, is_active: !r.is_active } : r,
      ));
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({ kind: 'err', message: `${tt.toast.toggleFailed}: ${detail}` });
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="settings-section"
             data-testid="html-only-senders-section">
      <header className="settings-section__head">
        <h2 className="settings-section__title">{tt.title}</h2>
        <p className="settings-section__lead muted">{tt.helpText}</p>
      </header>

      {loading ? (
        <p className="muted">{t.common.loading}</p>
      ) : (
        <>
          {senders.length === 0 ? (
            <p className="muted" data-testid="html-only-senders-empty">
              {tt.empty}
            </p>
          ) : (
            <ul className="excluded-vendors__list"
                data-testid="html-only-senders-list">
              {senders.map((s) => (
                <li
                  key={s.id}
                  className={
                    'excluded-vendors__chip ' +
                    (s.is_active
                      ? 'excluded-vendors__chip--user'
                      : 'excluded-vendors__chip--system')
                  }
                  data-testid={`html-only-sender-${s.id}`}
                  data-active={s.is_active ? 'true' : 'false'}
                >
                  <span className="mono">{s.sender_pattern}</span>
                  {s.description ? (
                    <span className="muted"> · {s.description}</span>
                  ) : null}
                  <label className="muted small"
                         style={{ marginLeft: '0.5rem' }}>
                    <input
                      type="checkbox"
                      checked={!!s.is_active}
                      onChange={() => onToggle(s)}
                      disabled={busyId === s.id}
                      data-testid={`html-only-sender-toggle-${s.id}`}
                    />
                    {' '}{tt.active}
                  </label>
                  <button
                    type="button"
                    className="btn ghost btn--sm"
                    onClick={() => onRemove(s)}
                    disabled={busyId === s.id}
                    data-testid={`html-only-sender-remove-${s.id}`}
                    aria-label={t.common.remove}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}

          {showAddForm ? (
            <form
              className="card card-pad"
              onSubmit={onAdd}
              data-testid="html-only-senders-add-form"
              style={{ marginTop: '0.75rem' }}
            >
              <label className="form-row">
                <span>{tt.senderPattern}</span>
                <input
                  type="text"
                  value={pattern}
                  onChange={(e) => setPattern(e.target.value)}
                  placeholder={tt.senderPatternPlaceholder}
                  autoFocus
                  data-testid="html-only-senders-pattern-input"
                />
              </label>
              <label className="form-row">
                <span>{tt.description}</span>
                <input
                  type="text"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  data-testid="html-only-senders-description-input"
                />
              </label>
              <div className="modal-card__actions">
                <button
                  type="button"
                  className="btn ghost"
                  onClick={() => {
                    setShowAddForm(false);
                    setPattern('');
                    setDescription('');
                  }}
                  disabled={saving}
                  data-testid="html-only-senders-cancel"
                >
                  {tt.cancel}
                </button>
                <button
                  type="submit"
                  className="btn primary"
                  disabled={saving || !pattern.trim()}
                  data-testid="html-only-senders-confirm"
                >
                  {saving ? t.common.loading : tt.save}
                </button>
              </div>
            </form>
          ) : (
            <button
              type="button"
              className="btn"
              onClick={() => setShowAddForm(true)}
              data-testid="html-only-senders-add"
              style={{ marginTop: '0.75rem' }}
            >
              {tt.addButton}
            </button>
          )}
        </>
      )}
    </section>
  );
}
