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

function daysApart(a, b) {
  if (!a || !b) return null;
  return Math.round(Math.abs(a.getTime() - b.getTime()) / (1000 * 60 * 60 * 24));
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

  const receiptDate = parseDate(m.receipt_date);
  const paymentDate = parseDate(payment?.date);
  const dDays = daysApart(receiptDate, paymentDate);

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
      ok: dDays != null && dDays === 0,
      okLabel: t.travelTinder.valid.dateMatch,
      diffLabel:
        dDays != null
          ? t.travelTinder.valid.dateDiff.replace('{days}', String(dDays))
          : t.travelTinder.valid.dateDiff.replace('{days}', '—'),
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
  const scoreBreakdown = suggestion.score_breakdown;
  const tooltip = scoreBreakdown
    ? t.travelTinder.scoreTooltip
        .replace('{amount}', String(scoreBreakdown.amount ?? 0))
        .replace('{date}', String(scoreBreakdown.date ?? 0))
        .replace('{vendor}', String(scoreBreakdown.vendor ?? 0))
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
          <span className="mono">{score}%</span>
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
