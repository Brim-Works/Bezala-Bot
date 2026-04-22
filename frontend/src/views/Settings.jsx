import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api } from '../api/client.js';
import { useApiData } from '../hooks/useApiData.js';

import AutomationSection from '../components/settings/AutomationSection.jsx';
import FilterSection from '../components/settings/FilterSection.jsx';
import ScanRulesSection from '../components/settings/ScanRulesSection.jsx';
import LinkFetchSection from '../components/settings/LinkFetchSection.jsx';
import TrashSection from '../components/settings/TrashSection.jsx';
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
  'trash_auto_purge_days',
  'ai_min_confidence_to_save',
  'link_fetch_senders',
  'html_to_pdf_enabled',
];

function pickPayload(form) {
  if (!form) return null;
  const out = {};
  for (const key of FIELDS_IN_PAYLOAD) {
    out[key] = form[key];
  }
  return out;
}

/* Dirty jämförs mellan form och en egen baseline (inte server-state från
 * useApiData). Baseline flyttas fram synkront när PUT lyckats — så
 * SaveBar-läget nollställs direkt, oberoende av om refetch resolvar, cachar
 * eller returnerar något annat. */
export default function Settings() {
  const { t } = useI18n();
  const toast = useToast();
  const loader = useCallback(() => api.settings(), []);
  const { data: server, isLoading, refetch } = useApiData(loader, []);

  const [form, setForm] = useState(null);
  const [baseline, setBaseline] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (server && !baseline) {
      setBaseline(server);
      setForm(server);
    }
  }, [server, baseline]);

  const update = useCallback((patch) => {
    setForm((f) => (f ? { ...f, ...patch } : f));
  }, []);

  const dirty = useMemo(() => {
    if (!form || !baseline) return false;
    return (
      JSON.stringify(pickPayload(form)) !==
      JSON.stringify(pickPayload(baseline))
    );
  }, [form, baseline]);

  const onReset = useCallback(() => {
    if (baseline) setForm(baseline);
  }, [baseline]);

  const onSave = useCallback(async () => {
    if (!form) return;
    setSaving(true);
    try {
      const updated = await api.updateSettings(pickPayload(form));
      // Nollställ dirty synkront genom att flytta fram både form och baseline
      // innan refetch hinner lura jämförelsen.
      setForm(updated);
      setBaseline(updated);
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
        <ScanRulesSection
          form={form}
          update={update}
          builtinSenders={form.builtin_senders || baseline?.builtin_senders || []}
        />
        <LinkFetchSection form={form} update={update} />
        <TrashSection form={form} update={update} />
      </div>
      <SaveBar dirty={dirty} saving={saving} onSave={onSave} onReset={onReset} />
    </>
  );
}
