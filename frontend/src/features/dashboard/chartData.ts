/**
 * API mini curve → props of the shared `Chart` (pure, tested without the DOM). The day categories
 * are the forecast screen's (`features/forecast/chartData`): the dashboard draws the same curve,
 * shorter, and never a second chart implementation.
 */
import { seriesColor, type ChartLine } from "../../components/charts";
import type { CurveDay } from "./dashboardApi";

export interface CurveLabels {
  balance: string;
  available: string;
}

/** Balance and "available after levies" of the next 90 days, in the order the chart draws them. */
export function miniCurveLines(
  days: CurveDay[],
  labels: CurveLabels,
): ChartLine[] {
  return [
    {
      key: "balance",
      label: labels.balance,
      color: seriesColor(1),
      values: days.map((day) => day.balance_cents),
    },
    {
      key: "available",
      label: labels.available,
      color: seriesColor(0),
      values: days.map((day) => day.available_cents),
    },
  ];
}
