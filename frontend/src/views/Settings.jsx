import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { useApiData } from '../hooks/useApiData.js';

import AutomationSection from '../components/settings/AutomationSection.jsx';
import FilterSection from '../components/settings/FilterSection.jsx';
import ScanRulesSection from '../components/settings/ScanRulesSection.jsx';
import SaveBar from '../components/settings/SaveBar.jsx';
import { useToast } from '../lib/toast.jsx';

const FIELDS_IN_PAYLOAD = [
  'scan_interval_minutes',
  'ai_naming_enabled',
  'auto_upload_enabled',
  'confidence_threshold',
  'require_attachments',
  'exclude_promotions',
  'exclude_social',
  'exclude_calendar',
  'include_senders',
  'exclude_senders',
  'exclude_subjects',
];

function pickPayload(form) {
  const out = {};
  for (const key of FIELDS_IN_PAYLOAD) {
    out[key] = form[key];
  }
  return out;
}

function deepEqualSettings(a, b) {
  if (!a || !b) return false;
  for (const key of FIELDS_IN_PAYLOAD) {
    const av = a[key];
    const bv = b[key];
    if (Array.isArray(av) && Array.isArray(bv)) {
      if (av.length !== bv.length) return false;
      for (let i = 0; i < av.length; i += 1) {
        if (av[i] !== bv[i]) return false;
      }
    } else if (av !== bv) {
      return false;
    }
  }
  return true;
}

export default function Settings() {
  const { t } = useI18n();
  const toast = useToast();
  const loader = useCallback(() => api.settings(), []);
  const { data: server, isLoading, refetch } = useApiData(loader, []);

  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (server && !form) setForm(server);
  }, [server, form]);

  const update = useCallback((patch) => {
    setForm((f) => (f ? { ...f, ...patch } : f));
  }, []);

  const dirty = useMemo(
    () => (form && server ? !deepEqualSettings(form, server) : false),
    [form, server],
  );

  const onReset = useCallback(() => {
    if (server) setForm(server);
  }, [server]);

  const onSave = useCallback(async () => {
    if (!form) return;
    setSaving(true);
    try {
      const updated = await api.updateSettings(pickPayload(form));
      setForm(updated);
      toast.show({ kind: 'ok', message: t.settings.toast.saved });
      refetch().catch(() => {});
    } catch (err) {
      toast.show({
        kind: 'err',
        message: `${t.settings.toast.saveFailed}: ${err.message || err}`,
      });
    } finally {
      setSaving(false);
    }
  }, [form, refetch, t.settings.toast.saveFailed, t.settings.toast.saved, toast]);

  if (isLoading || !form) {
    return (
      <div className="settings-loading muted" data-testid="settings-loading">
        {t.common.loading}
      </div>
    );
  }

  return (
    <>
      <div className="settings-wrap" data-testid="settings-view">
        <AutomationSection form={form} update={update} />
        <FilterSection form={form} update={update} />
        <ScanRulesSection form={form} update={update} />
      </div>
      <SaveBar dirty={dirty} saving={saving} onSave={onSave} onReset={onReset} />
    </>
  );
}
