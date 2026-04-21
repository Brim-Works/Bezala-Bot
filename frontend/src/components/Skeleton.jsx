/* Generisk skeleton-placeholder. Tema-medveten via CSS-variabler.
 * Anropare väljer storlek via width/height eller egen className. */
export default function Skeleton({
  width = '100%',
  height = 12,
  radius,
  className = '',
  style = {},
  testId,
}) {
  return (
    <span
      className={`skeleton ${className}`}
      style={{ width, height, borderRadius: radius, ...style }}
      data-testid={testId}
      aria-hidden="true"
    />
  );
}

/* Rad i en tabell-skeleton — matchar --row-h. */
export function SkeletonRow({ cols = 5, testId }) {
  return (
    <tr className="skeleton-row" data-testid={testId}>
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i}>
          <Skeleton height={14} />
        </td>
      ))}
    </tr>
  );
}
