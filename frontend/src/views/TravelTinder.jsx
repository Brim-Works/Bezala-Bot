/* FAS 8.5a — Travel Tinder. Tinder-stil-koppling av Bezala-korttrans
 * mot kvitton. Vänster panel = saknade korttransaktioner, höger panel
 * = AI-förslag som stort kort + lista med alla kvitton.
 *
 * Är en PARALLELL vy bredvid Översikt + Kortmatchning. När den är
 * verifierad rivs gamla vyerna i FAS 8.5b.
 *
 * State:
 *  - selectedPayment: vald korttrans (objektet, inte bara id)
 *  - all_messages: alla saved-rader (inkl. kopplade) från backend
 *  - missing_receipts: saknade Bezala-kortrader + AI-suggestions
 *  - search/filter/sort persistas i localStorage (tt_*)
 *
 * Auto-refresh var 10 min. Network-fel renderas som toast utan att
 * tappa cachen.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useI18n } from '../i18n/useI18n.jsx';
import { api, ApiError } from '../api/client.js';
import { useToast } from '../lib/toast.jsx';
import { useDrawer } from '../drawer/DrawerProvider.jsx';
import MissingPaymentsList from '../components/travel-tinder/MissingPaymentsList.jsx';
import OtherReceiptsList from '../components/travel-tinder/OtherReceiptsList.jsx';
import TinderCard from '../components/travel-tinder/TinderCard.jsx';
import MatchConfirmModal from '../components/travel-tinder/MatchConfirmModal.jsx';
import UploadCard from '../components/travel-tinder/UploadCard.jsx';
import PdfPreviewLightbox from '../components/travel-tinder/PdfPreviewLightbox.jsx';
import MatchedPairsList from '../components/travel-tinder/MatchedPairsList.jsx';
import { IconRefresh } from '../icons/index.jsx';

const REFRESH_INTERVAL_MS = 10 * 60 * 1000;
const LS_KEY = 'tt_state_v1';
const LS_WIDTH_KEY = 'tt_panel_width';
const LS_MODE_KEY = 'tt_mode';
const LS_MATCHED_PERIOD_KEY = 'tt_matched_period';
const LS_MATCHED_SEARCH_KEY = 'tt_matched_search';
const PANEL_WIDTH_DEFAULT = 300;
const PANEL_WIDTH_MIN = 200;
const PANEL_WIDTH_MAX = 500;
const MOBILE_BREAKPOINT = 800;

function clampWidth(n) {
  if (typeof n !== 'number' || Number.isNaN(n)) return PANEL_WIDTH_DEFAULT;
  return Math.min(PANEL_WIDTH_MAX, Math.max(PANEL_WIDTH_MIN, Math.round(n)));
}

function loadPanelWidth() {
  if (typeof window === 'undefined') return PANEL_WIDTH_DEFAULT;
  try {
    const raw = window.localStorage.getItem(LS_WIDTH_KEY);
    if (!raw) return PANEL_WIDTH_DEFAULT;
    const n = Number.parseInt(raw, 10);
    return Number.isFinite(n) ? clampWidth(n) : PANEL_WIDTH_DEFAULT;
  } catch {
    return PANEL_WIDTH_DEFAULT;
  }
}

function persistPanelWidth(px) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(LS_WIDTH_KEY, String(clampWidth(px)));
  } catch {
    // tappa tyst
  }
}

function loadPersisted() {
  if (typeof window === 'undefined') return {};
  try {
    const raw = window.localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) || {} : {};
  } catch {
    return {};
  }
}

function persist(patch) {
  if (typeof window === 'undefined') return;
  try {
    const cur = loadPersisted();
    window.localStorage.setItem(LS_KEY, JSON.stringify({ ...cur, ...patch }));
  } catch {
    // localStorage kan vara full eller blockerad — tappa tyst.
  }
}

function formatRelative(ts, t) {
  if (!ts) return t.travelTinder.justNow;
  const diff = Math.max(0, Math.floor((Date.now() - ts) / 60000));
  if (diff < 1) return t.travelTinder.justNow;
  return t.travelTinder.minutesAgo.replace('{n}', String(diff));
}

export default function TravelTinder() {
  const { t } = useI18n();
  const toast = useToast();
  const { openDrawer } = useDrawer();
  const persisted = useRef(loadPersisted()).current;

  const [data, setData] = useState({ missing_receipts: [], all_messages: [] });
  const [isLoading, setIsLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  const [selectedPaymentId, setSelectedPaymentId] = useState(null);
  const [skippedSuggestionIds, setSkippedSuggestionIds] = useState([]);

  const [searchQuery, setSearchQuery] = useState(persisted.search || '');
  const [statusFilter, setStatusFilter] = useState(
    persisted.statusFilter || 'uncoupled',
  );
  const [dateFilter, setDateFilter] = useState(persisted.dateFilter || '30d');
  const [currencyFilter, setCurrencyFilter] = useState(
    persisted.currencyFilter || 'all',
  );
  const [sortBy, setSortBy] = useState(persisted.sortBy || 'processed_at');
  const [sortDir, setSortDir] = useState(persisted.sortDir || 'desc');

  const [pendingMatch, setPendingMatch] = useState(null);
  const [matching, setMatching] = useState(false);
  const [pdfPreviewMessage, setPdfPreviewMessage] = useState(null);

  // FAS 8.5 — Matchade-vy state. mode persistas separat så användaren
  // hamnar tillbaka i rätt läge nästa session.
  const [mode, setMode] = useState(() => {
    try {
      const raw = window.localStorage.getItem(LS_MODE_KEY);
      return raw === 'matched' ? 'matched' : 'unmatched';
    } catch {
      return 'unmatched';
    }
  });
  const [matchedPeriod, setMatchedPeriod] = useState(() => {
    try {
      const raw = window.localStorage.getItem(LS_MATCHED_PERIOD_KEY);
      return ['7d', '30d', '90d', 'all'].includes(raw) ? raw : '30d';
    } catch {
      return '30d';
    }
  });
  const [matchedSearch, setMatchedSearch] = useState(() => {
    try {
      return window.localStorage.getItem(LS_MATCHED_SEARCH_KEY) || '';
    } catch {
      return '';
    }
  });
  const [matchedData, setMatchedData] = useState({
    pairs: [],
    total: 0,
    stats: { total_all_time: 0, this_week: 0, estimated_minutes_saved: 0 },
  });
  const [matchedLoading, setMatchedLoading] = useState(false);

  // Resizable splitter — bredd persistas separat (egen LS-nyckel) så
  // den överlever även om huvud-state-objektet byter shape framöver.
  const [panelWidth, setPanelWidth] = useState(() => loadPanelWidth());
  const isDraggingRef = useRef(false);
  const dragStartRef = useRef({ x: 0, w: PANEL_WIDTH_DEFAULT });

  const onSplitterMouseDown = useCallback(
    (e) => {
      e.preventDefault();
      isDraggingRef.current = true;
      dragStartRef.current = { x: e.clientX, w: panelWidth };
      document.body.style.cursor = 'col-resize';
      // Förhindra textmarkering under drag.
      document.body.style.userSelect = 'none';
    },
    [panelWidth],
  );

  useEffect(() => {
    function onMove(e) {
      if (!isDraggingRef.current) return;
      const delta = e.clientX - dragStartRef.current.x;
      setPanelWidth(clampWidth(dragStartRef.current.w + delta));
    }
    function onUp() {
      if (!isDraggingRef.current) return;
      isDraggingRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      // Persistera bara vid släpp — undvik att hamra localStorage under drag.
      setPanelWidth((w) => {
        persistPanelWidth(w);
        return w;
      });
    }
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
  }, []);

  const onSplitterKeyDown = useCallback((e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const step = e.shiftKey ? 32 : 8;
    setPanelWidth((w) => {
      const next = clampWidth(w + (e.key === 'ArrowRight' ? step : -step));
      persistPanelWidth(next);
      return next;
    });
  }, []);

  // Inline grid-template — desktop använder bredden, mobil ignoreras
  // helt via CSS @media (max-width:800px) som kör vertikal stacking.
  const gridStyle = { gridTemplateColumns: `${panelWidth}px 6px 1fr` };

  // Persist UI-state vid varje förändring
  useEffect(() => {
    persist({
      search: searchQuery,
      statusFilter,
      dateFilter,
      currencyFilter,
      sortBy,
      sortDir,
    });
  }, [searchQuery, statusFilter, dateFilter, currencyFilter, sortBy, sortDir]);

  // FAS 8.5 — Persist Matchade-vy-state separat
  useEffect(() => {
    try {
      window.localStorage.setItem(LS_MODE_KEY, mode);
    } catch {
      /* ignore */
    }
  }, [mode]);
  useEffect(() => {
    try {
      window.localStorage.setItem(LS_MATCHED_PERIOD_KEY, matchedPeriod);
    } catch {
      /* ignore */
    }
  }, [matchedPeriod]);
  useEffect(() => {
    try {
      window.localStorage.setItem(LS_MATCHED_SEARCH_KEY, matchedSearch);
    } catch {
      /* ignore */
    }
  }, [matchedSearch]);

  const refreshMatched = useCallback(async () => {
    setMatchedLoading(true);
    try {
      const body = await api.matchedPairs({
        period: matchedPeriod,
        search: matchedSearch,
      });
      setMatchedData({
        pairs: body?.pairs || [],
        total: body?.total || 0,
        stats: body?.stats || {
          total_all_time: 0, this_week: 0, estimated_minutes_saved: 0,
        },
      });
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.travelTinder.refreshFailed}: ${detail}`,
      });
    } finally {
      setMatchedLoading(false);
    }
  }, [matchedPeriod, matchedSearch, t.travelTinder.refreshFailed, toast]);

  // Hämta matchade-data när vi byter till matched-läge eller filter ändras.
  // Eftersom search/period är localStorage-persistenta är det rimligt att
  // ladda när användaren öppnar vyn även om den inte aktivt är vald.
  useEffect(() => {
    if (mode !== 'matched') return;
    refreshMatched();
  }, [mode, refreshMatched]);

  const refresh = useCallback(
    async ({ silent = false } = {}) => {
      if (!silent) setRefreshing(true);
      try {
        const body = await api.bezalaMatchSuggestionsAll();
        setData({
          missing_receipts: body?.missing_receipts || [],
          all_messages: body?.all_messages || [],
        });
        setLastRefresh(Date.now());
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : String(err);
        toast.show({
          kind: 'err',
          message: `${t.travelTinder.refreshFailed}: ${msg}`,
        });
      } finally {
        setIsLoading(false);
        setRefreshing(false);
      }
    },
    [t.travelTinder.refreshFailed, toast],
  );

  useEffect(() => {
    refresh({ silent: true });
  }, [refresh]);

  useEffect(() => {
    const id = setInterval(() => refresh({ silent: true }), REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const missingRows = data.missing_receipts;

  // Auto-välj första saknade kortbetalning om inget är valt
  useEffect(() => {
    if (selectedPaymentId == null && missingRows.length > 0) {
      setSelectedPaymentId(missingRows[0].missing_receipt.id);
    }
  }, [missingRows, selectedPaymentId]);

  const selected = useMemo(
    () =>
      missingRows.find((r) => r.missing_receipt.id === selectedPaymentId) ||
      null,
    [missingRows, selectedPaymentId],
  );

  const matchedCount = useMemo(
    () => data.all_messages.filter((m) => m.coupled).length,
    [data.all_messages],
  );
  const totalCount = data.all_messages.length;

  const activeSuggestion = useMemo(() => {
    if (!selected) return null;
    const list = (selected.suggestions || []).filter(
      (s) => !skippedSuggestionIds.includes(s.message.id),
    );
    return list[0] || null;
  }, [selected, skippedSuggestionIds]);

  const onSelectPayment = useCallback((id) => {
    setSelectedPaymentId(id);
    setSkippedSuggestionIds([]);
  }, []);

  // FAS 8.5c — fire-and-forget feedback. Får aldrig blockera kärnflödet:
  // alla fel sväljs i en .catch så match/skip-knapparna fortfarande
  // beter sig snabbt och deterministiskt även om backend är seg.
  const sendMatchFeedback = useCallback(
    ({ messageId, billLineId, result, aiScore, scoreBreakdown }) => {
      if (!messageId) return;
      api
        .feedbackMatchResult({
          messageId,
          billLineId,
          result,
          aiScore,
          scoreBreakdown,
        })
        .catch((err) => {
          // Tyst — feedback är best-effort. Console.warn för felsökning.
          // eslint-disable-next-line no-console
          console.warn('match-feedback failed:', err);
        });
    },
    [],
  );

  const onSkipSuggestion = useCallback(() => {
    if (!activeSuggestion || !selected) return;
    sendMatchFeedback({
      messageId: activeSuggestion.message.message_id,
      billLineId: selected.missing_receipt.id,
      result: 'skipped',
      aiScore: activeSuggestion.score ?? null,
      scoreBreakdown: activeSuggestion.score_breakdown || null,
    });
    setSkippedSuggestionIds((prev) => [...prev, activeSuggestion.message.id]);
  }, [activeSuggestion, selected, sendMatchFeedback]);

  const requestMatch = useCallback(
    (messageRow, missingReceiptId, aiContext = null) => {
      if (matching) return;
      // aiContext: { score, score_breakdown } när Match-klicket kommer
      // från Tinder-kortet. null när det är manuell koppling via en rad
      // i "Andra kvitton" — då finns ingen AI-score.
      setPendingMatch({
        message: messageRow,
        missingReceiptId,
        aiContext,
      });
    },
    [matching],
  );

  const cancelMatch = useCallback(() => {
    setPendingMatch(null);
  }, []);

  const confirmMatch = useCallback(async () => {
    if (!pendingMatch) return;
    setMatching(true);
    try {
      await api.matchToBezala(
        pendingMatch.message.id,
        pendingMatch.missingReceiptId,
      );
      // FAS 8.5c — registrera positiv feedback så match-algoritmen kan
      // lära sig. Manuell koppling skickar aiScore=null.
      sendMatchFeedback({
        messageId: pendingMatch.message.message_id,
        billLineId: pendingMatch.missingReceiptId,
        result: 'matched',
        aiScore: pendingMatch.aiContext?.score ?? null,
        scoreBreakdown: pendingMatch.aiContext?.score_breakdown || null,
      });
      toast.show({
        kind: 'ok',
        message: t.travelTinder.matched.replace(
          '{vendor}',
          pendingMatch.message.vendor || pendingMatch.message.file_name || '',
        ),
      });
      setPendingMatch(null);
      setSkippedSuggestionIds([]);
      // Markera nästa korttrans automatiskt
      const idx = missingRows.findIndex(
        (r) => r.missing_receipt.id === selectedPaymentId,
      );
      const nextRow = missingRows[idx + 1] || null;
      setSelectedPaymentId(
        nextRow ? nextRow.missing_receipt.id : null,
      );
      refresh({ silent: true });
    } catch (err) {
      const detail = err instanceof ApiError ? err.message : String(err);
      toast.show({
        kind: 'err',
        message: `${t.travelTinder.matchFailed}: ${detail}`,
      });
    } finally {
      setMatching(false);
    }
  }, [
    pendingMatch,
    missingRows,
    selectedPaymentId,
    refresh,
    sendMatchFeedback,
    t.travelTinder.matched,
    t.travelTinder.matchFailed,
    toast,
  ]);

  const onClickReceipt = useCallback(
    (msg) => {
      // Med vald korttrans → öppna bekräftelsemodal
      if (selected && !msg.coupled) {
        requestMatch(msg, selected.missing_receipt.id);
        return;
      }
      // Annars (eller redan kopplad) → öppna drawer
      openDrawer(msg, 'gmail');
    },
    [openDrawer, requestMatch, selected],
  );

  const onShowPdfPreview = useCallback((msg) => {
    setPdfPreviewMessage(msg);
  }, []);

  return (
    <div className="travel-tinder" data-testid="travel-tinder">
      <header className="travel-tinder__head">
        <h1 className="travel-tinder__title">{t.travelTinder.title}</h1>
        <div className="travel-tinder__head-meta">
          <span className="muted mono" data-testid="last-refresh">
            {t.travelTinder.lastRefresh.replace(
              '{when}',
              formatRelative(lastRefresh, t),
            )}
          </span>
          <button
            type="button"
            className="btn ghost"
            onClick={() => refresh()}
            disabled={refreshing}
            data-testid="tt-refresh"
            aria-label={t.travelTinder.refresh}
          >
            <IconRefresh className="icon sm" />
          </button>
        </div>
      </header>

      <div className="travel-tinder__grid" style={gridStyle}>
        <MissingPaymentsList
          rows={missingRows}
          selectedId={selectedPaymentId}
          onSelect={onSelectPayment}
          matchedCount={matchedCount}
          totalCount={totalCount}
          isLoading={isLoading}
          mode={mode}
          onModeChange={setMode}
          unmatchedCount={missingRows.length}
          matchedTotalCount={matchedData.stats?.total_all_time ?? 0}
        />

        <div
          className="tt-splitter"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize panels"
          aria-valuenow={panelWidth}
          aria-valuemin={PANEL_WIDTH_MIN}
          aria-valuemax={PANEL_WIDTH_MAX}
          tabIndex={0}
          onMouseDown={onSplitterMouseDown}
          onKeyDown={onSplitterKeyDown}
          data-testid="tt-splitter"
        >
          <span className="tt-splitter__grip" aria-hidden="true" />
        </div>

        <div className="travel-tinder__right">
          {mode === 'matched' ? (
            <MatchedPairsList
              data={matchedData}
              isLoading={matchedLoading}
              search={matchedSearch}
              setSearch={setMatchedSearch}
              period={matchedPeriod}
              setPeriod={setMatchedPeriod}
              onOpenDrawer={(pair) => openDrawer(
                {
                  // Drawer förväntar sig samma shape som ProcessedMessage —
                  // vi har bara delar i pair.receipt så vi rebygger ett
                  // minimalt objekt. Drawer-tabbarna laddar resten via API.
                  id: pair.id,
                  message_id: pair.message_id,
                  vendor: pair.receipt?.vendor,
                  file_name: pair.receipt?.file_name,
                  amount: pair.receipt?.amount,
                  currency: pair.receipt?.currency,
                  receipt_date: pair.receipt?.receipt_date,
                  drive_file_id: pair.receipt?.drive_file_id,
                  drive_link: pair.receipt?.drive_link,
                  subject: pair.receipt?.subject,
                  sender: pair.receipt?.sender,
                  bezala_transaction_id: pair.bezala_transaction_id,
                  bezala_upload_status: 'success',
                  status: 'saved',
                },
                'gmail',
              )}
              onChanged={() => {
                // Refresha både listorna efter unmatch
                refreshMatched();
                refresh({ silent: true });
              }}
            />
          ) : null}
          {mode !== 'matched' ? (
          <OtherReceiptsList
            allMessages={data.all_messages}
            selected={selected}
            activeSuggestion={activeSuggestion}
            onClickReceipt={onClickReceipt}
            onShowPdfPreview={onShowPdfPreview}
            search={searchQuery}
            setSearch={setSearchQuery}
            statusFilter={statusFilter}
            setStatusFilter={setStatusFilter}
            dateFilter={dateFilter}
            setDateFilter={setDateFilter}
            currencyFilter={currencyFilter}
            setCurrencyFilter={setCurrencyFilter}
            sortBy={sortBy}
            setSortBy={setSortBy}
            sortDir={sortDir}
            setSortDir={setSortDir}
            tinderCard={
              selected ? (
                activeSuggestion ? (
                  <TinderCard
                    suggestion={activeSuggestion}
                    payment={selected.missing_receipt}
                    onSkip={onSkipSuggestion}
                    onMatch={() =>
                      requestMatch(
                        activeSuggestion.message,
                        selected.missing_receipt.id,
                        {
                          score: activeSuggestion.score,
                          score_breakdown: activeSuggestion.score_breakdown,
                        },
                      )
                    }
                    onMoreInfo={() =>
                      openDrawer(activeSuggestion.message, 'gmail')
                    }
                    onShowPdfPreview={() =>
                      onShowPdfPreview(activeSuggestion.message)
                    }
                    matching={matching}
                  />
                ) : (
                  <div className="tt-empty-card" data-testid="tt-no-suggestion">
                    <h3>{t.travelTinder.empty.noSuggestion}</h3>
                    <p className="muted">
                      {t.travelTinder.empty.noSuggestionBody}
                    </p>
                  </div>
                )
              ) : null
            }
            uploadCard={<UploadCard payment={selected?.missing_receipt} />}
            isLoading={isLoading}
          />
          ) : null}
        </div>
      </div>

      {pendingMatch ? (
        <MatchConfirmModal
          payment={selected?.missing_receipt}
          message={pendingMatch.message}
          onCancel={cancelMatch}
          onConfirm={confirmMatch}
          loading={matching}
        />
      ) : null}

      {pdfPreviewMessage ? (
        <PdfPreviewLightbox
          message={pdfPreviewMessage}
          onClose={() => setPdfPreviewMessage(null)}
        />
      ) : null}
    </div>
  );
}
