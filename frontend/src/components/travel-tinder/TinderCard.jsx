import { useI18n } from '../../i18n/useI18n.jsx';
import { fmtAmount } from '../../lib/format.js';
import VendorLogo from '../VendorLogo.jsx';

/* Stora Tinder-kortet i mitten av högra panelen. Visar AI-förslag med
 * score-badge + inline-validering (belopp/datum/vendor). Match-knappen
 * blir orange (varning) när någon validering har avvikelse, men funkar
 * — manuell override.
 *
 * PDF-preview default: ikon + filnamn. Klick öppnar lightbox via
 * onShowPdfPreview-prop. */

function parseDate(s) {
  if (!s) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatShortDate(s, lang) {
  const d = parseDate(s);
  if (!d) return '—';
  try {
    return d.toLocaleDateString(lang === 'sv' ? 'sv-SE' : 'en-GB', {
      day: 'numeric',
      month: 'short',
    });
  } catch {
    return s;
  }
}

function amountClose(a, b) {
  if (a == null || b == null) return false;
  const tol = Math.max(0.01, Math.abs(a) * 0.05);
  return Math.abs(a - b) <= tol;
}

function vendorClose(a, b) {
  if (!a || !b) return false;
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  if (x === y) return true;
  return x.includes(y) || y.includes(x);
}

export default function TinderCard({
  suggestion,
  payment,
  onSkip,
  onMatch,
  onMoreInfo,
  onShowPdfPreview,
  matching,
}) {
  const { t, lang } = useI18n();
  const m = suggestion.message;
  const breakdown = suggestion.score_breakdown || {};
  const matchedField = breakdown.date_matched_field || null;
  const daysOff = breakdown.date_days_off;

  // Bygg datum-validering från backend's dual-date-resultat. matched_field
  // berättar om receipt_date (resedatum) eller received_at (bokningsdatum)
  // gav den bästa matchen — vi visar rätt kontext-text för användaren.
  let dateOk = false;
  let dateLabel = '';
  if (matchedField === 'receipt_date') {
    const dateStr = formatShortDate(m.receipt_date, lang);
    dateOk = daysOff === 0;
    dateLabel = (daysOff === 0
      ? t.travelTinder.valid.dateMatchReceiptDate
      : t.travelTinder.valid.dateNearReceiptDate
    )
      .replace('{date}', dateStr)
      .replace('{days}', String(daysOff ?? '—'));
  } else if (matchedField === 'received_at') {
    const dateStr = formatShortDate(m.received_at, lang);
    dateOk = daysOff === 0;
    dateLabel = (daysOff === 0
      ? t.travelTinder.valid.dateMatchReceivedAt
      : t.travelTinder.valid.dateNearReceivedAt
    )
      .replace('{date}', dateStr)
      .replace('{days}', String(daysOff ?? '—'));
  } else {
    // matched_field=null: backend matchade inte (>7 dagar isär eller båda
    // datumen saknas). Visa varning utan att gissa "samma dag".
    dateLabel = t.travelTinder.valid.dateDiffNoMatch;
  }

  const validations = [
    {
      key: 'amount',
      ok: amountClose(m.amount, payment?.amount),
      okLabel: t.travelTinder.valid.amountMatch.replace(
        '{value}',
        m.amount != null ? fmtAmount(m.amount, m.currency, lang) : '—',
      ),
      diffLabel: t.travelTinder.valid.amountDiff
        .replace(
          '{receipt}',
          m.amount != null ? fmtAmount(m.amount, m.currency, lang) : '—',
        )
        .replace(
          '{payment}',
          payment?.amount != null
            ? fmtAmount(payment.amount, payment.currency, lang)
            : '—',
        ),
    },
    {
      key: 'date',
      ok: dateOk,
      okLabel: dateLabel,
      diffLabel: dateLabel,
    },
    {
      key: 'vendor',
      ok: vendorClose(m.vendor, payment?.description),
      okLabel: t.travelTinder.valid.vendorMatch.replace(
        '{vendor}',
        m.vendor || payment?.description || '',
      ),
      diffLabel: t.travelTinder.valid.vendorDiff,
    },
  ];

  const anyDiff = validations.some((v) => !v.ok);

  const score = suggestion.score;
  // Cap visning vid 100 — score-summan kan överstiga maxvärdet
  // (amount-bonus 50 + date 30 + vendor 30 + currency-bonus etc.) men
  // "110%" ser buggigt ut för användaren. Backend-score lämnas
  // oförändrad så intern ranking fortfarande har full upplösning.
  const displayScore = Math.min(100, Math.max(0, Math.round(score ?? 0)));
  const tooltip = breakdown
    ? t.travelTinder.scoreTooltip
        .replace('{amount}', String(breakdown.amount ?? 0))
        .replace('{date}', String(breakdown.date ?? 0))
        .replace('{vendor}', String(breakdown.vendor ?? 0))
    : '';

  return (
    <article className="tt-card" data-testid={`tt-card-${m.id}`}>
      <div className="tt-card__head">
        <span
          className="tt-card__star"
          title={tooltip}
          data-testid="tt-card-score"
        >
          ⭐ {t.travelTinder.aiSuggestion}{' '}
          <span className="mono">{displayScore}%</span>
        </span>
      </div>

      <div className="tt-card__vendor">
        <VendorLogo name={m.vendor} size={32} />
        <div>
          <div className="tt-card__vendor-name">
            {m.vendor || <span className="muted">—</span>}
          </div>
          <div className="tt-card__vendor-meta mono muted">
            {m.receipt_date || '—'}
            {m.amount != null
              ? ' · ' + fmtAmount(m.amount, m.currency, lang)
              : ''}
          </div>
        </div>
      </div>

      <button
        type="button"
        className="tt-card__pdf"
        onClick={onShowPdfPreview}
        disabled={!m.drive_file_id}
        data-testid="tt-card-pdf"
      >
        <span className="tt-card__pdf-icon" aria-hidden="true">📄</span>
        <span className="tt-card__pdf-name mono">
          {m.file_name || (m.drive_file_id ? 'PDF' : t.travelTinder.pdfMissing)}
        </span>
      </button>

      <ul className="tt-card__validations" data-testid="tt-validations">
        {validations.map((v) => (
          <li
            key={v.key}
            className={`tt-valid ${v.ok ? 'tt-valid--ok' : 'tt-valid--warn'}`}
            data-testid={`tt-valid-${v.key}`}
          >
            <span className="tt-valid__icon" aria-hidden="true">
              {v.ok ? '✓' : '⚠'}
            </span>
            <span>{v.ok ? v.okLabel : v.diffLabel}</span>
          </li>
        ))}
      </ul>

      <div className="tt-card__actions">
        <button
          type="button"
          className="btn ghost tt-card__skip"
          onClick={onSkip}
          disabled={matching}
          data-testid="tt-card-skip"
          aria-label={t.travelTinder.actionSkip}
        >
          ✗
        </button>
        <button
          type="button"
          className="btn ghost"
          onClick={onMoreInfo}
          disabled={matching}
          data-testid="tt-card-info"
          aria-label={t.travelTinder.actionInfo}
        >
          ⓘ
        </button>
        <button
          type="button"
          className={`btn ${anyDiff ? 'tt-btn-warn' : 'primary'}`}
          onClick={onMatch}
          disabled={matching}
          data-testid="tt-card-match"
        >
          ✓ {t.travelTinder.actionMatch}
        </button>
      </div>
    </article>
  );
}
