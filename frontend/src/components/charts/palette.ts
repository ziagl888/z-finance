/**
 * Series colours by slot. The values live in `charts.css` (`--chart-1` … `--chart-8`, light and
 * dark); slots are handed out in this fixed order, never cycled — a ninth series folds into
 * "Other" (`otherColor`). Colour follows the entity, so a caller keeps its slot map across
 * filters instead of re-ranking.
 */

/** Number of distinct hues; the eighth (red) is left out of automatic assignment (status-like). */
export const SERIES_SLOTS = 7;

/** CSS colour of slot `slot` (0-based). */
export function seriesColor(slot: number): string {
  if (!Number.isInteger(slot) || slot < 0 || slot >= SERIES_SLOTS) {
    throw new RangeError(`series slot ${slot} outside 0..${SERIES_SLOTS - 1}`);
  }
  return `var(--chart-${slot + 1})`;
}

/** Neutral colour of the folded "Other" series. */
export function otherColor(): string {
  return "var(--chart-other)";
}
