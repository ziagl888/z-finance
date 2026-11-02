/**
 * Reusable charts (ARCHITECTURE.md "UI system"): `Chart` (stacked bars and lines on one value
 * axis, tooltip, keyboard, table twin) and `SplitBar`. Series colours: `seriesColor(n)`.
 */
export {
  Chart,
  type ChartBar,
  type ChartCategory,
  type ChartFlags,
  type ChartLine,
  type ChartMark,
  type ChartProps,
} from "./Chart";
export { SplitBar, type SplitSegment } from "./SplitBar";
export { SERIES_SLOTS, otherColor, seriesColor } from "./palette";
export { monthLong, monthShort, monthYear, niceAxis } from "./scale";
