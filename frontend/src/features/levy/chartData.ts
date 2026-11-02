/**
 * API chart data → props of the shared `Chart` (pure, tested without the DOM). Labels are passed
 * in already translated; month names follow the UI locale.
 */
import {
  monthLong,
  monthShort,
  monthYear,
  otherColor,
  seriesColor,
  type ChartBar,
  type ChartCategory,
  type ChartFlags,
  type ChartLine,
} from "../../components/charts";
import { categoryKey, colorOfCategory, type SlotMap } from "./draft";
import type { ExpenseMonth, IncomeMonth } from "./levyApi";

/** Reference lines (need, liabilities, debt) are drawn in the text colour: they are not a category. */
const LINE_COLOR = "var(--ink)";

/** Local "YYYY-MM" of today; the API's chart window is anchored on the server's local date. */
export function currentMonthKey(today: Date = new Date()): string {
  return `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
}

/** X positions for 24 months: month name on the axis, the year under the first tick and every January. */
export function monthCategories(
  months: string[],
  locale: string,
  plannedNote: string,
  current: string,
): ChartCategory[] {
  return months.map((month, i) => ({
    key: month,
    label: monthLong(month, locale),
    tick: monthShort(month, locale),
    tickSub: i === 0 || month.endsWith("-01") ? monthYear(month) : undefined,
    note: month > current ? plannedNote : undefined,
  }));
}

/** Index of the first month after `current`, for the "planned" divider (`null` when none). */
export function firstPlannedIndex(
  months: string[],
  current: string,
): number | null {
  const index = months.findIndex((month) => month > current);
  return index === -1 ? null : index;
}

export interface IncomeLabels {
  booked: string;
  planned: string;
  required: string;
  below: string;
}

/** Income chart: booked and planned income as stacked bars, the need as a line, months below it flagged. */
export function incomeSeries(
  months: IncomeMonth[],
  labels: IncomeLabels,
): { bars: ChartBar[]; lines: ChartLine[]; flags: ChartFlags } {
  return {
    bars: [
      {
        key: "booked",
        label: labels.booked,
        color: seriesColor(0),
        values: months.map((m) => m.actual_cents),
      },
      {
        key: "planned",
        label: labels.planned,
        color: seriesColor(0),
        opacity: 0.45,
        values: months.map((m) => m.planned_cents),
      },
    ],
    lines: [
      {
        key: "required",
        label: labels.required,
        color: LINE_COLOR,
        values: months.map((m) => m.required_cents),
      },
    ],
    flags: { label: labels.below, values: months.map((m) => m.below_required) },
  };
}

export interface ExpenseLabels {
  liabilities: string;
  other: string;
  uncategorised: string;
}

/**
 * Expense chart, top panel: one stacked bar series per category (slot order, so a colour never
 * moves), the categories without a slot folded into "Other", and the liabilities line on the same
 * axis (both are money per month). The remaining debt is a stock and gets a panel of its own.
 */
export function expenseSeries(
  months: ExpenseMonth[],
  slots: SlotMap,
  labels: ExpenseLabels,
): { bars: ChartBar[]; lines: ChartLine[] } {
  const names = new Map<string, string>();
  const perCategory = new Map<string, number[]>();
  const other: number[] = months.map(() => 0);
  months.forEach((month, i) => {
    for (const category of month.categories) {
      const key = categoryKey(category.category_id);
      names.set(key, category.category_name ?? labels.uncategorised);
      const slot = slots.get(key);
      if (slot === undefined || slot === null) {
        other[i] = (other[i] ?? 0) + category.amount_cents;
      } else {
        const values = perCategory.get(key) ?? months.map(() => 0);
        values[i] = (values[i] ?? 0) + category.amount_cents;
        perCategory.set(key, values);
      }
    }
  });
  const bars: ChartBar[] = [...perCategory.entries()]
    .sort((a, b) => (slots.get(a[0]) ?? 0) - (slots.get(b[0]) ?? 0))
    .map(([key, values]) => ({
      key,
      label: names.get(key) ?? labels.uncategorised,
      color: colorOfCategory(slots, key === "none" ? null : Number(key)),
      values,
    }));
  if (other.some((cents) => cents > 0)) {
    bars.push({
      key: "other",
      label: labels.other,
      color: otherColor(),
      values: other,
    });
  }
  return {
    bars,
    lines: [
      {
        key: "liabilities",
        label: labels.liabilities,
        color: LINE_COLOR,
        values: months.map((m) => m.liabilities_cents),
      },
    ],
  };
}

/** Expense chart, lower panel: the remaining debt at the end of each month. */
export function debtSeries(months: ExpenseMonth[], label: string): ChartLine[] {
  return [
    {
      key: "remaining-debt",
      label,
      color: LINE_COLOR,
      values: months.map((m) => m.remaining_debt_cents),
      area: true,
    },
  ];
}
