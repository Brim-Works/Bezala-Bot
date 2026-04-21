/* Horisontell stapel + procent. Mono-font på siffran (CLAUDE.md-regel). */
export default function Confidence({ value }) {
  if (value === null || value === undefined) {
    return <span className="muted">—</span>;
  }
  const v = Math.max(0, Math.min(100, Number(value) || 0));
  const lvl = v >= 85 ? 'high' : v >= 70 ? 'mid' : 'low';
  return (
    <span className={`conf conf--${lvl}`} aria-label={`${v}%`}>
      <span className="conf__track">
        <span className="conf__fill" style={{ width: `${v}%` }} />
      </span>
      <span className="conf__val mono">{v}%</span>
    </span>
  );
}
