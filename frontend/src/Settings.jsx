import { useEffect, useState } from 'react';
import { api } from './api.js';

const INTERVAL_CHOICES = [
  { value: 15, label: '15 min' },
  { value: 30, label: '30 min' },
  { value: 60, label: '1 timme' },
  { value: 240, label: '4 timmar' },
];

function ListEditor({ label, placeholder, values, onChange }) {
  const [draft, setDraft] = useState('');

  const add = () => {
    const v = draft.trim();
    if (!v) return;
    if (values.includes(v)) {
      setDraft('');
      return;
    }
    onChange([...values, v]);
    setDraft('');
  };

  const remove = (v) => onChange(values.filter((x) => x !== v));

  return (
    <div className="list-editor">
      <label>{label}</label>
      <div className="list-editor-row">
        <input
          type="text"
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" onClick={add}>Lägg till</button>
      </div>
      <div className="chip-list">
        {values.length === 0 && <span className="muted">Inget tillagt ännu.</span>}
        {values.map((v) => (
          <span key={v} className="chip">
            {v}
            <button type="button" onClick={() => remove(v)} aria-label={`Ta bort ${v}`}>
              ×
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}

export default function Settings({ navigate }) {
  const [form, setForm] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  useEffect(() => {
    let cancelled = false;
    api.getSettings()
      .then((data) => {
        if (!cancelled) {
          setForm(data);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e.message);
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  const update = (patch) => setForm((f) => ({ ...f, ...patch }));

  const save = async (e) => {
    e.preventDefault();
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const { updated_at, ...payload } = form;
      const updated = await api.updateSettings(payload);
      setForm(updated);
      setMessage('Sparat.');
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !form) {
    return (
      <>
        <header>
          <h1>Inställningar</h1>
          <button onClick={() => navigate('/')} className="logout-btn">Tillbaka</button>
        </header>
        <div className="container">
          <p className="muted">Laddar…</p>
        </div>
      </>
    );
  }

  return (
    <>
      <header>
        <h1>Inställningar</h1>
        <button onClick={() => navigate('/')} className="logout-btn">Tillbaka</button>
      </header>
      <form className="container settings-form" onSubmit={save}>
        {error && (
          <div className="stat-card" style={{ borderColor: 'var(--err)', marginBottom: '1rem' }}>
            <div className="label">Fel</div>
            <div style={{ color: 'var(--err)' }}>{error}</div>
          </div>
        )}
        {message && (
          <div className="stat-card" style={{ borderColor: 'var(--ok)', marginBottom: '1rem' }}>
            <div className="label">Status</div>
            <div style={{ color: 'var(--ok)' }}>{message}</div>
          </div>
        )}

        <section>
          <h2>Scanning-regler</h2>

          <ListEditor
            label="Inkludera från (avsändare eller domän)"
            placeholder="t.ex. finnair.com eller kvitto@sl.se"
            values={form.include_senders}
            onChange={(v) => update({ include_senders: v })}
          />

          <ListEditor
            label="Exkludera från (avsändare eller domän)"
            placeholder="t.ex. newsletter@example.com"
            values={form.exclude_senders}
            onChange={(v) => update({ exclude_senders: v })}
          />

          <ListEditor
            label="Exkludera ämnen (matchar substring)"
            placeholder="t.ex. Accepted, Declined, Kickoff"
            values={form.exclude_subjects}
            onChange={(v) => update({ exclude_subjects: v })}
          />

          <div className="checkbox-grid">
            <label>
              <input
                type="checkbox"
                checked={form.require_attachments}
                onChange={(e) => update({ require_attachments: e.target.checked })}
              />
              Endast mail med bilagor
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.exclude_promotions}
                onChange={(e) => update({ exclude_promotions: e.target.checked })}
              />
              Exkludera Promotions-kategorin
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.exclude_social}
                onChange={(e) => update({ exclude_social: e.target.checked })}
              />
              Exkludera Social-kategorin
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.exclude_calendar}
                onChange={(e) => update({ exclude_calendar: e.target.checked })}
              />
              Exkludera kalenderinbjudningar
            </label>
          </div>
        </section>

        <section>
          <h2>Konto-konfiguration</h2>

          <label className="field">
            <span>Scanningsintervall</span>
            <select
              value={form.scan_interval_minutes}
              onChange={(e) => update({ scan_interval_minutes: parseInt(e.target.value, 10) })}
            >
              {INTERVAL_CHOICES.map((c) => (
                <option key={c.value} value={c.value}>{c.label}</option>
              ))}
            </select>
          </label>

          <div className="checkbox-grid">
            <label>
              <input
                type="checkbox"
                checked={form.ai_naming_enabled}
                onChange={(e) => update({ ai_naming_enabled: e.target.checked })}
              />
              AI-namngivning
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.auto_upload_enabled}
                onChange={(e) => update({ auto_upload_enabled: e.target.checked })}
              />
              Auto-upload till Bezala
            </label>
          </div>

          <label className="field">
            <span>Confidence-tröskel för auto-upload: {form.confidence_threshold}%</span>
            <input
              type="range"
              min="0"
              max="100"
              step="1"
              value={form.confidence_threshold}
              onChange={(e) => update({ confidence_threshold: parseInt(e.target.value, 10) })}
            />
          </label>
        </section>

        <div className="form-actions">
          <button type="submit" disabled={saving}>
            {saving ? 'Sparar…' : 'Spara'}
          </button>
        </div>
      </form>
    </>
  );
}
