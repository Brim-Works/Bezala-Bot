/* Match Health — analysvy som klassificerar varje korttrans utan kvitto
 * efter sannolik orsak. Skog/Ljust-tema fungerar via CSS-variabler (inga
 * hardcodade hex). State persistas till localStorage:
 *   mh_filter_verdict, mh_filter_period, mh_filter_vendor
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api, ApiError } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { IconRefresh, IconCopy, IconChevronRight } from '../icons/index.jsx';

const LS_VERDICT_KEY = 'mh_filter_verdict';
const LS_PERIOD_KEY = 'mh_filter_period';
const LS_VENDOR_KEY = 'mh_filter_vendor';

const VERDICT_ORDER = [
  'matched_correctly',
  'multiple_candidates_above_threshold',
  'best_below_threshold',
  'processed_but_no_candidate',
  'gmail_found_not_processed',
  'gmail_filtered_or_excluded',
  'gmail_miss',
  'ai_extraction_wrong',
  'match_algorithm_failed',
  'no_receipt_exists',
  'already_matched',
  'gmail_error',
];

// Mappar verdict-namn → i18n-nyckel + CSS-row-modifier.
const VERDICT_TO_KEY = {
  matched_correctly: 'matchedCorrectly',
  multiple_candidates_above_threshold: 'multipleAboveThreshold',
  best_below_threshold: 'bestBelowThreshold',
  processed_but_no_candidate: 'processedButNoCandidate',
  gmail_found_not_processed: 'gmailFoundNotProcessed',
  gmail_filtered_or_excluded: 'gmailFilteredOrExcluded',
  gmail_miss: 'gmailMiss',
  ai_extraction_wrong: 'aiExtractionWrong',
  match_algorithm_failed: 'matchAlgorithmFailed',
  no_receipt_exists: 'noReceiptExists',
  already_matched: 'alreadyMatched',
  gmail_error: 'gmailError',
};

// Max-värden för score-staplar (matchar receipt_matcher.py-konstanter).
const SCORE_MAX = {
  amount: 50,
  date: 30,
  vendor: 30,
  total: 110,
};

function scoreColorClass(value, max) {
  if (max <= 0) return 'mh-bar--err';
  const pct = (value / max) * 100;
  if (pct >= 80) return 'mh-bar--ok';
  if (pct >= 40) return 'mh-bar--warn';
  return 'mh-bar--err';
}

const PERIODS = ['7d', '30d', '90d', 'all'];

function loadFromLs(key, fallback) {
  try {
    const v = window.localStorage.getItem(key);
    return v == null ? fallback : v;
  } catch {
    return fallback;
  }
}
function saveToLs(key, value) {
  try {
    window.localStorage.setItem(key, String(value || ''));
  } catch {
    // localStorage kan vara full — tappa tyst
  }
}

function inPeriod(dateStr, period) {
  if (period === 'all') return true;
  if (!dateStr) return false;
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return false;
  const days = { '7d': 7, '30d': 30, '90d': 90 }[period] || 30;
  const ageMs = Date.now() - d.getTime();
  return ageMs <= days * 24 * 60 * 60 * 1000;
}

function buildGmailUrl(query) {
  if (!query) return null;
  // Gmail webbsökning — användaren kan ge sin egen logged-in browser.
  return `https://mail.google.com/mail/u/0/#search/${encodeURIComponent(query)}`;
}

function fmtAmount(amount, currency) {
  if (amount == null) return '—';
  const n = typeof amount === 'number' ? amount : Number(amount);
  if (!Number.isFinite(n)) return String(amount);
  const formatted = n.toLocaleString('sv-SE', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  });
  return currency ? `${formatted} ${currency}` : formatted;
}

function fmtDate(dateStr) {
  if (!dateStr) return '—';
  return String(dateStr).slice(0, 10);
}

function rowToMarkdown(row, t) {
  const verdictKey = VERDICT_TO_KEY[row.verdict.category] || 'matchedCorrectly';
  const header = `### ${t.matchHealth.verdicts[verdictKey]}: `
    + `${row.bill_line.vendor_normalized || '<okänd>'} `
    + `${fmtAmount(row.bill_line.amount, row.bill_line.currency)} `
    + `(${fmtDate(row.bill_line.date)})`;

  const top = row.top_3_suggestions || [];
  const bestLine = row.best_match
    ? `**Bästa match:** ${row.best_match.vendor || '—'} `
      + `${fmtAmount(row.best_match.amount, row.best_match.currency)} `
      + `(score ${row.best_match.score})`
    : '**Bästa match:** inga förslag över tröskeln';

  const top3Block = top.length
    ? top.map((s, i) => {
        const b = s.score_breakdown || {};
        return `${i + 1}. ${s.vendor || '—'} `
          + `${fmtAmount(s.amount, s.currency)} `
          + `— score ${s.score} `
          + `(belopp:${b.amount ?? 0}, datum:${b.date ?? 0}, vendor:${b.vendor ?? 0})`;
      }).join('\n')
    : '_inga förslag_';

  const fc = row.fuzzy_candidates || {};
  const fuzzyLine = `**Fuzzy candidates:** `
    + `${fc.by_amount_window_10pct || 0} i ±10% belopp, `
    + `${fc.by_date_window_7d || 0} i ±7d datum, `
    + `${fc.by_vendor_fuzzy || 0} vendor-fuzzy`;

  const g = row.gmail_status || {};
  const gmailLine = `**Gmail-status:** ${g.category || '—'} — ${g.details || ''}`;
  const queryLine = g.search_query_used
    ? `**Gmail-query:** \`${g.search_query_used}\``
    : '';

  const actionLine = `**Föreslagen åtgärd:** ${row.verdict.suggested_action || '—'}`;

  // Match Health 2.0 — diagnostic summary + processed_receipts + gmail_messages
  const ds = row.diagnostic_summary || {};
  const summaryLine = (
    `**Diagnos:** ${ds.gmail_count ?? 0} Gmail-mail · `
    + `${ds.candidate_count ?? 0} kandidater · `
    + `${ds.above_threshold_count ?? 0} över tröskel ${ds.threshold ?? 80}`
  );

  const processed = row.processed_receipts || [];
  const processedBlock = processed.length
    ? '**Kandidater:**\n' + processed.slice(0, 10).map((r) => {
        const bd = r.match_score_breakdown || {};
        return `- ${r.vendor || r.file_name || '—'} `
          + `${fmtAmount(r.amount, r.currency)} `
          + `(${fmtDate(r.receipt_date)}) — score ${r.match_score_total} `
          + `(belopp:${bd.amount ?? 0}, diff ${bd.amount_diff ?? '?'}; `
          + `datum:${bd.date ?? 0}, ${bd.date_diff_days ?? '?'}d; `
          + `vendor:${bd.vendor ?? 0}, ${bd.vendor_match_method || '—'} `
          + `${bd.vendor_similarity_pct ?? 0}%)`
          + (r.above_threshold ? ' ✓' : '')
          + (r.drive_link ? ` · Drive: ${r.drive_link}` : '');
      }).join('\n')
    : '';

  const messages = row.gmail_messages || [];
  const messagesBlock = messages.length
    ? '**Gmail-mail:**\n' + messages.slice(0, 10).map((m) => {
        return `- ${m.subject || m.message_id} `
          + `(${m.sender || '—'}) — `
          + (m.has_attachment ? 'med' : 'utan') + ' attachment, '
          + (m.is_processed ? 'processad' : 'EJ processad')
          + (m.via_html_only_pipeline ? ', html-only' : '');
      }).join('\n')
    : '';

  return [
    header,
    summaryLine,
    bestLine,
    '**Top 3:**',
    top3Block,
    '',
    processedBlock,
    messagesBlock,
    fuzzyLine,
    gmailLine,
    queryLine,
    actionLine,
    '',
    '---',
  ].filter(Boolean).join('\n');
}

function buildMarkdown({ rows, stats, period, verdictFilter, t }) {
  const ts = new Date().toISOString();
  const filterLabel = verdictFilter === 'all'
    ? 'Alla'
    : (t.matchHealth.filters[VERDICT_TO_KEY[verdictFilter]] || verdictFilter);
  const lines = [
    '# Match Health Report',
    `*Genererad: ${ts} · Period: ${period} · Filter: ${filterLabel}*`,
    '',
    '## Översikt',
    `- Total korttrans: ${stats.total ?? 0}`,
    `- Matchade rätt: ${stats.matched_correctly ?? 0}`,
    `- Saknas i Gmail: ${stats.gmail_miss ?? 0}`,
    `- AI-extraktion fel: ${stats.ai_extraction_wrong ?? 0}`,
    `- Algoritm fel: ${stats.match_algorithm_failed ?? 0}`,
    `- Fysiska kvitton (sannolikt): ${stats.no_receipt_exists ?? 0}`,
    '',
    '## Detaljer per korttrans',
    '',
    ...rows.map((r) => rowToMarkdown(r, t)),
    '',
    '## Råa data (för diagnostik)',
    '',
    '```json',
    JSON.stringify({ stats, rows }, null, 2),
    '```',
  ];
  return lines.join('\n');
}

function singleRowMarkdown(row, t) {
  return [
    '## Match Health — enstaka rad',
    '',
    rowToMarkdown(row, t),
  ].join('\n');
}

async function copyToClipboard(text, onFallback) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // pågå ned till fallback
  }
  onFallback(text);
  return false;
}

export default function MatchHealth() {
  const { t } = useI18n();
  const toast = useToast();

  const [report, setReport] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const [verdictFilter, setVerdictFilter] = useState(
    () => loadFromLs(LS_VERDICT_KEY, 'all'),
  );
  const [period, setPeriod] = useState(
    () => loadFromLs(LS_PERIOD_KEY, '30d'),
  );
  const [vendorFilter, setVendorFilter] = useState(
    () => loadFromLs(LS_VENDOR_KEY, ''),
  );
  const [expandedId, setExpandedId] = useState(null);
  // Fallback-textarea innehåll när navigator.clipboard saknas eller failar.
  const [fallbackText, setFallbackText] = useState(null);

  useEffect(() => saveToLs(LS_VERDICT_KEY, verdictFilter), [verdictFilter]);
  useEffect(() => saveToLs(LS_PERIOD_KEY, period), [period]);
  useEffect(() => saveToLs(LS_VENDOR_KEY, vendorFilter), [vendorFilter]);

  const fetchReport = useCallback(async ({ refresh = false } = {}) => {
    if (refresh) setRefreshing(true); else setIsLoading(true);
    setError(null);
    try {
      const data = await api.matchHealth({ refresh });
      setReport(data);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      setError(msg);
    } finally {
      setIsLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { fetchReport(); }, [fetchReport]);

  const rows = report?.rows || [];
  const stats = report?.stats || {};

  const filteredRows = useMemo(() => {
    const vendorQ = vendorFilter.trim().toLowerCase();
    return rows.filter((r) => {
      if (verdictFilter !== 'all'
          && r.verdict?.category !== verdictFilter) return false;
      if (!inPeriod(r.bill_line?.date, period)) return false;
      if (vendorQ) {
        const v = (
          (r.bill_line?.vendor_normalized || '')
          + ' ' + (r.bill_line?.merchant || '')
        ).toLowerCase();
        if (!v.includes(vendorQ)) return false;
      }
      return true;
    });
  }, [rows, verdictFilter, period, vendorFilter]);

  const handleCopyAll = useCallback(async () => {
    const md = buildMarkdown({
      rows: filteredRows, stats, period, verdictFilter, t,
    });
    const ok = await copyToClipboard(md, setFallbackText);
    if (ok) toast.show({ kind: 'ok', message: t.matchHealth.copiedToast });
  }, [filteredRows, stats, period, verdictFilter, t, toast]);

  const handleCopyRow = useCallback(async (row) => {
    const md = singleRowMarkdown(row, t);
    const ok = await copyToClipboard(md, setFallbackText);
    if (ok) toast.show({ kind: 'ok', message: t.matchHealth.copiedToast });
  }, [t, toast]);

  const statsLine = (t.matchHealth.stats || '')
    .replace('{total}', String(stats.total ?? 0))
    .replace('{matched}', String(stats.matched_correctly ?? 0))
    .replace('{gmailMiss}', String(stats.gmail_miss ?? 0))
    .replace('{algoFail}', String(stats.match_algorithm_failed ?? 0))
    .replace('{noReceipt}', String(stats.no_receipt_exists ?? 0));

  return (
    <div className="page" data-testid="match-health">
      <header className="page-header">
        <div>
          <h1>{t.matchHealth.title}</h1>
          <p className="muted">{t.matchHealth.subtitle}</p>
          {report ? (
            <p className="muted small" data-testid="mh-stats">{statsLine}</p>
          ) : null}
        </div>
        <div className="actions" style={{ display: 'flex', gap: '0.5rem' }}>
          <button
            type="button"
            onClick={() => fetchReport({ refresh: true })}
            disabled={refreshing || isLoading}
            data-testid="mh-refresh"
            className="btn"
          >
            <IconRefresh className="icon sm" />
            <span>{t.matchHealth.refresh}</span>
          </button>
          <button
            type="button"
            onClick={handleCopyAll}
            disabled={isLoading || filteredRows.length === 0}
            data-testid="mh-copy-all"
            className="btn primary"
          >
            <IconCopy className="icon sm" />
            <span>{t.matchHealth.copyForClaude}</span>
          </button>
        </div>
      </header>

      <section className="toolbar" data-testid="mh-toolbar"
               style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap',
                        margin: '1rem 0' }}>
        <label>
          <span className="muted small">{t.matchHealth.filters.verdict}</span>
          <select
            value={verdictFilter}
            onChange={(e) => setVerdictFilter(e.target.value)}
            data-testid="mh-filter-verdict"
          >
            <option value="all">{t.matchHealth.filters.all}</option>
            {VERDICT_ORDER.map((v) => (
              <option key={v} value={v}>
                {t.matchHealth.filters[VERDICT_TO_KEY[v]] || v}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span className="muted small">{t.matchHealth.filters.period}</span>
          <select
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
            data-testid="mh-filter-period"
          >
            {PERIODS.map((p) => (
              <option key={p} value={p}>
                {t.matchHealth.filters[p === 'all'
                  ? 'periodAll'
                  : `period${p[0].toUpperCase()}${p.slice(1)}`]
                  || p}
              </option>
            ))}
          </select>
        </label>
        <input
          type="search"
          placeholder={t.matchHealth.filters.searchVendor}
          value={vendorFilter}
          onChange={(e) => setVendorFilter(e.target.value)}
          data-testid="mh-filter-vendor"
        />
      </section>

      {error ? (
        <div className="card err-card" data-testid="mh-error">
          <p>{(t.matchHealth.error || 'Error: {error}').replace(
            '{error}', error,
          )}</p>
          <button
            type="button"
            onClick={() => fetchReport({ refresh: true })}
            data-testid="mh-retry"
            className="btn"
          >
            {t.matchHealth.retry}
          </button>
        </div>
      ) : null}

      {isLoading && !report ? (
        <div className="muted" data-testid="mh-loading">
          {t.matchHealth.loading}
        </div>
      ) : null}

      {!isLoading && !error && filteredRows.length === 0 && rows.length === 0 ? (
        <div className="card" data-testid="mh-empty">
          <h2>{t.matchHealth.emptyState}</h2>
          <p className="muted">{t.matchHealth.emptyStateBody}</p>
        </div>
      ) : null}

      {filteredRows.length > 0 ? (
        <table
          className="data-table"
          data-testid="mh-table"
          style={{ width: '100%' }}
        >
          <thead>
            <tr>
              <th>{t.matchHealth.columns.payment}</th>
              <th>{t.matchHealth.columns.bestMatch}</th>
              <th>{t.matchHealth.columns.verdict}</th>
              <th>{t.matchHealth.columns.action}</th>
              <th aria-label={t.matchHealth.columns.expand} />
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row) => (
              <MatchHealthRow
                key={row.bill_line.id}
                row={row}
                expanded={expandedId === row.bill_line.id}
                onToggle={() => setExpandedId(
                  expandedId === row.bill_line.id ? null : row.bill_line.id,
                )}
                onCopyRow={handleCopyRow}
                t={t}
              />
            ))}
          </tbody>
        </table>
      ) : null}

      {fallbackText != null ? (
        <div className="card" data-testid="mh-fallback-textarea-wrap"
             style={{ marginTop: '1rem' }}>
          <p className="muted">{t.matchHealth.copyManualHint}</p>
          <textarea
            readOnly
            data-testid="mh-fallback-textarea"
            value={fallbackText}
            style={{ width: '100%', height: '240px', fontFamily: 'monospace' }}
          />
          <button
            type="button"
            onClick={() => setFallbackText(null)}
            data-testid="mh-fallback-close"
            className="btn"
          >
            {t.matchHealth.closeCopyHint}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function MatchHealthRow({ row, expanded, onToggle, onCopyRow, t }) {
  const verdictKey = VERDICT_TO_KEY[row.verdict?.category] || 'matchedCorrectly';
  const verdictLabel = t.matchHealth.verdicts[verdictKey] || row.verdict?.category;
  const score = row.best_match?.score;
  const billLineId = row.bill_line.id;
  const ds = row.diagnostic_summary || {};

  return (
    <>
      <tr
        data-testid={`mh-row-${billLineId}`}
        data-verdict={row.verdict?.category}
        className={`mh-row mh-row--${row.verdict?.category}`}
        onClick={onToggle}
        style={{ cursor: 'pointer' }}
      >
        <td>
          <div>
            <strong>{row.bill_line.vendor_normalized || '—'}</strong>
          </div>
          <div className="muted small">
            {fmtAmount(row.bill_line.amount, row.bill_line.currency)} ·
            {' '}{fmtDate(row.bill_line.date)}
          </div>
        </td>
        <td>
          {row.best_match ? (
            <>
              <div>{row.best_match.file_name || row.best_match.vendor || '—'}</div>
              <div className="muted small" data-testid={`mh-score-${billLineId}`}>
                {(t.matchHealth.bestMatchScore || 'Score {score}')
                  .replace('{score}', String(score ?? 0))}
              </div>
            </>
          ) : (
            <span className="muted">{t.matchHealth.bestMatchNone}</span>
          )}
          {/* Match Health 2.0 — counts ikoner */}
          <div className="muted small mh-counts"
               data-testid={`mh-counts-${billLineId}`}>
            <span title={t.matchHealth.counts.gmail}>
              📧 {ds.gmail_count ?? 0}
            </span>
            {' · '}
            <span title={t.matchHealth.counts.processed}>
              📄 {ds.candidate_count ?? 0}
            </span>
            {ds.above_threshold_count > 0 ? (
              <>
                {' · '}
                <span title={t.matchHealth.counts.aboveThreshold}>
                  🎯 {ds.above_threshold_count}
                </span>
              </>
            ) : null}
          </div>
        </td>
        <td>
          <span data-testid={`mh-verdict-${billLineId}`}>{verdictLabel}</span>
        </td>
        <td className="muted small">{row.verdict?.suggested_action || '—'}</td>
        <td>
          <IconChevronRight
            className="icon sm"
            title={t.matchHealth.columns.expand}
          />
        </td>
      </tr>
      {expanded ? (
        <tr data-testid={`mh-expanded-${billLineId}`}>
          <td colSpan={5}>
            <MatchHealthDetails row={row} onCopyRow={onCopyRow} t={t} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function ScoreBar({ label, value, max, t }) {
  const safeMax = Math.max(1, max);
  const widthPct = Math.min(100, (value / safeMax) * 100);
  const colorCls = scoreColorClass(value, safeMax);
  return (
    <div className="mh-score-row" data-testid={`mh-score-row-${label}`}>
      <span className="mh-score-label muted small">{label}</span>
      <div className="mh-bar-track">
        <div className={`mh-bar-fill ${colorCls}`}
             style={{ width: `${widthPct}%` }} />
      </div>
      <span className="mh-score-value mono small">{value}/{max}</span>
    </div>
  );
}

function FlowStep({ icon, title, status, ok }) {
  return (
    <div className={`mh-flow-step ${ok ? 'mh-flow-step--ok' : 'mh-flow-step--info'}`}>
      <span className="mh-flow-icon" aria-hidden="true">{icon}</span>
      <span className="mh-flow-title">{title}</span>
      <span className="mh-flow-status muted small">{status}</span>
    </div>
  );
}

function SummaryView({ row, t }) {
  const ds = row.diagnostic_summary || {};
  const verdictKey = VERDICT_TO_KEY[row.verdict?.category] || 'matchedCorrectly';
  const aboveCount = ds.above_threshold_count ?? 0;
  return (
    <div className="mh-flow" data-testid={`mh-flow-${row.bill_line.id}`}>
      <FlowStep
        icon="📧"
        title={t.matchHealth.flow.gmailStep}
        status={(t.matchHealth.status.foundMails || '{count} mail hittade')
          .replace('{count}', String(ds.gmail_count ?? 0))}
        ok={(ds.gmail_count ?? 0) > 0}
      />
      <div className="mh-flow-arrow" aria-hidden="true">↓</div>
      <FlowStep
        icon="📄"
        title={t.matchHealth.flow.processedStep}
        status={(t.matchHealth.status.processedOk || '{count} processade')
          .replace('{count}', String(ds.candidate_count ?? 0))}
        ok={(ds.candidate_count ?? 0) > 0}
      />
      <div className="mh-flow-arrow" aria-hidden="true">↓</div>
      <FlowStep
        icon="🎯"
        title={t.matchHealth.flow.matchingStep}
        status={
          row.best_match
            ? (t.matchHealth.status.bestScore || 'Bästa: {score}')
                .replace('{score}', String(ds.best_score ?? 0))
              + ' / ' + String(ds.threshold ?? 80)
              + ' '
              + (aboveCount > 0
                ? (t.matchHealth.status.aboveThreshold || '✓ över')
                : (t.matchHealth.status.belowThreshold || '✗ under'))
            : t.matchHealth.bestMatchNone
        }
        ok={aboveCount > 0}
      />
      <div className="mh-flow-arrow" aria-hidden="true">↓</div>
      <FlowStep
        icon={aboveCount > 0 ? '✓' : 'ℹ'}
        title={t.matchHealth.flow.resultStep}
        status={
          t.matchHealth.verdicts[verdictKey]
            || row.verdict?.category || '—'
        }
        ok={aboveCount > 0}
      />
      <p className="muted" style={{ marginTop: '0.75rem' }}>
        {ds.next_action || row.verdict?.suggested_action || ''}
      </p>
    </div>
  );
}

function TechnicalView({ row, t }) {
  const top3 = row.top_3_suggestions || [];
  const processed = row.processed_receipts || [];
  const messages = row.gmail_messages || [];
  const fc = row.fuzzy_candidates || {};
  const g = row.gmail_status || {};
  const gmailUrl = buildGmailUrl(g.search_query_used);

  return (
    <div className="mh-tech">
      <h4>{t.matchHealth.details.top3}</h4>
      {top3.length === 0 ? (
        <p className="muted">{t.matchHealth.details.top3Empty}</p>
      ) : (
        <ol style={{ paddingLeft: '1.25rem' }}>
          {top3.map((s, i) => {
            const b = s.score_breakdown || {};
            return (
              <li key={`${row.bill_line.id}-top-${i}`}
                  data-testid={`mh-top-${row.bill_line.id}-${i}`}>
                <strong>{s.vendor || s.file_name || '—'}</strong>
                {' '}({fmtAmount(s.amount, s.currency)}) — score {s.score}
                <div className="mh-bars">
                  <ScoreBar
                    label={t.matchHealth.details.amountScore}
                    value={b.amount ?? 0} max={SCORE_MAX.amount} t={t}
                  />
                  <ScoreBar
                    label={t.matchHealth.details.dateScore}
                    value={b.date ?? 0} max={SCORE_MAX.date} t={t}
                  />
                  <ScoreBar
                    label={t.matchHealth.details.vendorScore}
                    value={b.vendor ?? 0} max={SCORE_MAX.vendor} t={t}
                  />
                  <ScoreBar
                    label={t.matchHealth.fields.total}
                    value={s.score ?? 0} max={SCORE_MAX.total} t={t}
                  />
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {/* Match Health 2.0 — utökad kandidatlista */}
      {processed.length > 0 ? (
        <>
          <h4>{t.matchHealth.processedReceipts.title} ({processed.length})</h4>
          <ul style={{ paddingLeft: '1.25rem' }}
              data-testid={`mh-processed-${row.bill_line.id}`}>
            {processed.slice(0, 10).map((r) => (
              <li key={`${row.bill_line.id}-proc-${r.id}`}
                  data-testid={`mh-processed-row-${r.id}`}>
                <strong>{r.vendor || r.file_name || '—'}</strong>
                {' '}({fmtAmount(r.amount, r.currency)}) ·{' '}
                {fmtDate(r.receipt_date || r.received_at)}
                {' · '}
                <span className="muted small">
                  score {r.match_score_total}
                </span>
                {r.above_threshold ? (
                  <span className="mh-pill mh-pill--ok">
                    {t.matchHealth.processedReceipts.aboveThreshold}
                  </span>
                ) : null}
                {r.drive_link ? (
                  <>
                    {' · '}
                    <a href={r.drive_link} target="_blank" rel="noreferrer"
                       data-testid={`mh-drive-${r.id}`}>
                      {t.matchHealth.fields.driveLink}
                    </a>
                  </>
                ) : (
                  <span className="muted small">
                    {' '}· {t.matchHealth.processedReceipts.noDrive}
                  </span>
                )}
                {r.ai_confidence != null ? (
                  <span className="muted small">
                    {' '}· AI {r.ai_confidence}%
                  </span>
                ) : null}
                {r.ai_summary ? (
                  <div className="muted small">
                    {r.ai_summary}
                  </div>
                ) : null}
              </li>
            ))}
            {processed.length > 10 ? (
              <li className="muted">
                +{processed.length - 10} fler…
              </li>
            ) : null}
          </ul>
        </>
      ) : null}

      {/* Match Health 2.0 — Gmail-meddelanden */}
      {messages.length > 0 ? (
        <>
          <h4>{t.matchHealth.gmailMessages.title} ({messages.length})</h4>
          <ul style={{ paddingLeft: '1.25rem' }}
              data-testid={`mh-gmail-${row.bill_line.id}`}>
            {messages.slice(0, 10).map((m) => (
              <li key={`${row.bill_line.id}-gm-${m.message_id}`}>
                <strong>{m.subject || m.message_id}</strong>
                {' · '}
                <span className="muted small">{m.sender || '—'}</span>
                {' · '}
                <span className="muted small">
                  {m.has_attachment
                    ? t.matchHealth.gmailMessages.withAttachment
                    : t.matchHealth.gmailMessages.noAttachment}
                </span>
                {m.is_processed ? (
                  <span className="mh-pill mh-pill--ok">
                    {t.matchHealth.gmailMessages.processed}
                  </span>
                ) : (
                  <span className="mh-pill mh-pill--warn">
                    {t.matchHealth.gmailMessages.notProcessed}
                  </span>
                )}
                {m.via_html_only_pipeline ? (
                  <span className="mh-pill mh-pill--info">
                    {t.matchHealth.gmailMessages.viaHtmlOnly}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <h4>{t.matchHealth.details.fuzzyTitle}</h4>
      <ul style={{ paddingLeft: '1.25rem' }}>
        <li>{(t.matchHealth.details.fuzzyAmount || '')
          .replace('{count}', String(fc.by_amount_window_10pct || 0))}</li>
        <li>{(t.matchHealth.details.fuzzyDate || '')
          .replace('{count}', String(fc.by_date_window_7d || 0))}</li>
        <li>{(t.matchHealth.details.fuzzyVendor || '')
          .replace('{count}', String(fc.by_vendor_fuzzy || 0))}</li>
      </ul>

      <h4>{t.matchHealth.details.gmailTitle}</h4>
      <p className="muted">{g.details || '—'}</p>
      {g.search_query_used ? (
        <p>
          <span className="muted small">
            {t.matchHealth.details.gmailQuery}:
          </span>{' '}
          <code>{g.search_query_used}</code>
          {gmailUrl ? (
            <>
              {' · '}
              <a href={gmailUrl} target="_blank" rel="noreferrer"
                 data-testid={`mh-open-gmail-${row.bill_line.id}`}>
                {t.matchHealth.details.openInGmail}
              </a>
            </>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

function MatchHealthDetails({ row, onCopyRow, t }) {
  const [mode, setMode] = useState('summary');

  return (
    <div className="mh-details" style={{ padding: '0.75rem 0.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between',
                    alignItems: 'flex-start', gap: '1rem',
                    marginBottom: '0.5rem' }}>
        <div className="mh-mode-toggle" role="tablist"
             data-testid={`mh-mode-${row.bill_line.id}`}>
          <button
            type="button"
            className={`btn ${mode === 'summary' ? 'primary' : 'ghost'} btn--sm`}
            onClick={(e) => { e.stopPropagation(); setMode('summary'); }}
            data-testid={`mh-mode-summary-${row.bill_line.id}`}
            role="tab"
            aria-selected={mode === 'summary'}
          >
            {t.matchHealth.summaryView}
          </button>
          <button
            type="button"
            className={`btn ${mode === 'details' ? 'primary' : 'ghost'} btn--sm`}
            onClick={(e) => { e.stopPropagation(); setMode('details'); }}
            data-testid={`mh-mode-details-${row.bill_line.id}`}
            role="tab"
            aria-selected={mode === 'details'}
          >
            {t.matchHealth.detailedView}
          </button>
        </div>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onCopyRow(row); }}
          data-testid={`mh-copy-row-${row.bill_line.id}`}
          className="btn"
        >
          <IconCopy className="icon sm" />
          <span>{t.matchHealth.copyRow}</span>
        </button>
      </div>

      {mode === 'summary' ? (
        <SummaryView row={row} t={t} />
      ) : (
        <TechnicalView row={row} t={t} />
      )}
    </div>
  );
}
