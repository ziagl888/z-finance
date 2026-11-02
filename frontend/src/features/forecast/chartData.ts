/**
 * API forecast → props of the shared `Chart` (pure, tested without the DOM). Labels are passed in
 * already translated; dates follow the UI locale.
 */
import {
  monthShort,
  seriesColor,
  type ChartCategory,
  type ChartFlags,
  type ChartLine,
  type ChartMark,
} from "../../components/charts";
import { formatDay } from "../levy/draft";
import type { Forecast, ForecastDay } from "./forecastApi";

/** Reference line (the reserve) in the text colour: it is a threshold, not a series. */
const REFERENCE_COLOR = "var(--ink)";

/** Least days between the first day and the next 1st of a month for the first day to get a tick. */
const MIN_TICK_GAP_DAYS = 15;

function daysToNextMonth(iso: string): number {
  const [year = 1970, month = 1, day = 1] = iso.split("-").map(Number);
  const next = Date.UTC(year, month, 1);
  return Math.round((next - Date.UTC(year, month - 1, day)) / 86_400_000);
}

/**
 * One category per day: the label is the full date (tooltip and table), the axis shows a tick on
 * each 1st of a month (and on the first day when the next 1st is far enough away), the year under
 * the first tick and every January.
 */
export function dayCategories(
  days: ForecastDay[],
  locale: string,
): ChartCategory[] {
  let firstTick = true;
  return days.map((day, i) => {
    const monthStart = day.date.endsWith("-01");
    const tick =
      monthStart || (i === 0 && daysToNextMonth(day.date) >= MIN_TICK_GAP_DAYS);
    const withYear = tick && (firstTick || day.date.endsWith("-01-01"));
    if (tick) firstTick = false;
    return {
      key: day.date,
      label: formatDay(day.date, locale),
      tick: tick ? monthShort(day.date.slice(0, 7), locale) : "",
      tickSub: withYear ? day.date.slice(0, 4) : undefined,
    };
  });
}

export interface ForecastLabels {
  balance: string;
  available: string;
  reserve: string;
  below: string;
  /** "Lowest point" plus its amount, built by the caller (money is formatted there). */
  lowest: string;
}

/**
 * Curve lines, reserve line, the flag for every day under the reserve and the mark on the lowest
 * point. The check refers to the *available* line (UC-ZF-006 step 3, AK7), never to the balance.
 * A minimum date outside the days (should not happen) leaves the mark out instead of guessing.
 */
export function forecastSeries(
  forecast: Forecast,
  labels: ForecastLabels,
): { lines: ChartLine[]; flags: ChartFlags | undefined; marks: ChartMark[] } {
  const { days, reserve_cents: reserve } = forecast;
  const lines: ChartLine[] = [
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
  if (!forecast.reserve_missing) {
    lines.push({
      key: "reserve",
      label: labels.reserve,
      color: REFERENCE_COLOR,
      dashed: true,
      values: days.map(() => reserve),
    });
  }
  const flags = forecast.reserve_missing
    ? undefined
    : {
        label: labels.below,
        values: days.map((day) => day.available_cents < reserve),
      };
  const index = days.findIndex((day) => day.date === forecast.minimum_date);
  const marks: ChartMark[] =
    index === -1 ? [] : [{ index, lineKey: "available", label: labels.lowest }];
  return { lines, flags, marks };
}
