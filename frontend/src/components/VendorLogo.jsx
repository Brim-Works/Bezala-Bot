import { hueFromName, initials } from '../lib/vendorHash.js';

/* Färgad kvadrat med 2-bokstavs-initialer. Hue + initialer härleds från
 * leverantörsnamnet — backend skickar inget logo-data. */
export default function VendorLogo({ name, size = 22 }) {
  const hue = hueFromName(name || '');
  const text = initials(name || '');
  const fontSize = size < 22 ? 9 : 10;
  return (
    <span
      className="vlogo"
      style={{
        width: size,
        height: size,
        background: `oklch(55% 0.12 ${hue})`,
        fontSize,
      }}
      aria-hidden="true"
    >
      {text}
    </span>
  );
}
