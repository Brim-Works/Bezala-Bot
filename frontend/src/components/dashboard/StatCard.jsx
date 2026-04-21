/* Ett stat-kort. Värdet renderas alltid mono så siffror linjerar mellan
 * kort, även när de blandas med text. accent=true ger accent-kant + linjär
 * gradient i bakgrunden. onClick gör hela kortet klickbart. */
export default function StatCard({
  label,
  value,
  sub,
  accent = false,
  onClick,
  emphasizeError = false,
}) {
  const isClickable = typeof onClick === 'function';
  const Tag = isClickable ? 'button' : 'div';
  const display = value === null || value === undefined ? '—' : value;
  return (
    <Tag
      type={isClickable ? 'button' : undefined}
      className={`stat ${accent ? 'stat--accent' : ''} ${
        isClickable ? 'stat--clickable' : ''
      }`}
      onClick={isClickable ? onClick : undefined}
    >
      <div className="stat__label">{label}</div>
      <div
        className={`stat__value mono ${
          emphasizeError && Number(value) > 0 ? 'stat__value--err' : ''
        }`}
      >
        {display}
      </div>
      {sub ? <div className="stat__sub">{sub}</div> : null}
    </Tag>
  );
}
