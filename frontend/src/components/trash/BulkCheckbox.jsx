/* Enkel checkbox till radval. Stoppar click-propagation så rad-klick inte
 * triggar drawer när user väljer. */
export default function BulkCheckbox({ checked, onToggle, ariaLabel }) {
  return (
    <label
      className="bulk-checkbox"
      onClick={(e) => e.stopPropagation()}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => {
          e.stopPropagation();
          onToggle?.();
        }}
        aria-label={ariaLabel}
        data-testid="bulk-checkbox"
      />
    </label>
  );
}
