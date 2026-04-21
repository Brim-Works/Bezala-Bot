/* Generisk badge-bas. Färg styrs av kind, dot är en liten färgad cirkel
 * för tillgänglighet (färg ensam räcker inte). */
export default function Pill({ kind = 'muted', children, dot = true }) {
  return (
    <span className={`pill pill--${kind}`}>
      {dot ? <span className="pill__dot" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}
