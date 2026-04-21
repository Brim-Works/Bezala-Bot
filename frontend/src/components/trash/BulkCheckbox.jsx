import { useRef } from 'react';

/* Enkel checkbox till radval. Stoppar click-propagation så rad-klick inte
 * triggar drawer när user väljer.
 *
 * Vid shift+klick anropas onRangeSelect (om angiven) istället för onToggle
 * — Gmail/Finder-stil range-ADD. shiftKey finns bara på MouseEvent (click),
 * inte på change-eventet, så vi lagrar det i en ref på onClick och läser
 * det i onChange. Native toggle får ske som vanligt — det är Playwrights
 * .check() beroende av. */
export default function BulkCheckbox({
  checked,
  onToggle,
  onRangeSelect,
  ariaLabel,
}) {
  const shiftRef = useRef(false);

  return (
    <label
      className="bulk-checkbox"
      onClick={(e) => e.stopPropagation()}
    >
      <input
        type="checkbox"
        checked={checked}
        onClick={(e) => {
          e.stopPropagation();
          shiftRef.current = e.shiftKey === true;
        }}
        onChange={(e) => {
          e.stopPropagation();
          const shift = shiftRef.current;
          shiftRef.current = false;
          if (shift && typeof onRangeSelect === 'function') {
            onRangeSelect();
          } else {
            onToggle?.();
          }
        }}
        aria-label={ariaLabel}
        data-testid="bulk-checkbox"
      />
    </label>
  );
}
