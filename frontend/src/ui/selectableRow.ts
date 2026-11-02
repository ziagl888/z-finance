import type { HTMLAttributes, KeyboardEvent } from "react";

/**
 * Props that make a table row selectable by mouse and keyboard: focusable, Enter or Space selects
 * it like a click. Spread onto the `<tr>` together with the `ui-row-selectable` class.
 */
export function selectableRow(
  onSelect: () => void,
): HTMLAttributes<HTMLTableRowElement> {
  return {
    tabIndex: 0,
    onClick: onSelect,
    onKeyDown: (event: KeyboardEvent<HTMLTableRowElement>) => {
      // Keys pressed on a control inside the row (a button, a field) are not a row selection.
      if (event.target !== event.currentTarget) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onSelect();
      }
    },
  };
}
