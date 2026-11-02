/** Rules of the levy form and the chart data, as plain functions (tested without the DOM). */
import { SERIES_SLOTS, otherColor, seriesColor } from "../../components/charts";
import type { ExpenseMonth } from "./levyApi";

/** Largest rate the API takes, in basis points (90 %). */
export const MAX_RATE_BP = 9000;

/** A change of more than this many basis points shows the UC 6a hint (10 percentage points). */
export const RATE_JUMP_BP = 1000;

const RATE_INPUT = /^(\d{1,2})(?:[.,](\d{1,2}))?$/;

/**
 * "35", "35,5", "35.25" → basis points (3500, 3550, 3525); `null` for anything else, for a rate
 * above 90 % and for negative numbers. Integer arithmetic only — no float ever reaches the API.
 */
export function parseRatePercent(text: string): number | null {
  const match = RATE_INPUT.exec(text.replace(/\s/g, ""));
  if (!match) return null;
  const [, whole = "0", fraction = ""] = match;
  const bp = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  return bp <= MAX_RATE_BP ? bp : null;
}

/** Basis points as an editable percentage ("35", "35,5" in German) — trailing zeros dropped. */
export function formatRatePercent(bp: number, locale: string): string {
  const whole = Math.floor(bp / 100);
  const fraction = String(bp % 100)
    .padStart(2, "0")
    .replace(/0+$/, "");
  if (!fraction) return String(whole);
  return `${whole}${locale.startsWith("de") ? "," : "."}${fraction}`;
}

/** True when the rate moved by more than 10 percentage points (UC 6a). */
export function isRateJump(oldBp: number, newBp: number): boolean {
  return Math.abs(newBp - oldBp) > RATE_JUMP_BP;
}

/** `MM-DD` from a month (1–12) and a day (1–31); `null` when either is not a whole number in range. */
export function toMonthDay(month: number, day: number): string | null {
  if (!Number.isInteger(month) || month < 1 || month > 12) return null;
  if (!Number.isInteger(day) || day < 1 || day > 31) return null;
  return `${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/** Month and day of an `MM-DD` string; falls back to 1 July (the UC default) for anything else. */
export function fromMonthDay(value: string): { month: number; day: number } {
  const match = /^(\d{2})-(\d{2})$/.exec(value);
  return {
    month: match ? Number(match[1]) : 7,
    day: match ? Number(match[2]) : 1,
  };
}

/** Name of month 1–12 in the UI locale ("July"). */
export function monthName(month: number, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: "long",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(2000, month - 1, 1)));
}

/** A business date `YYYY-MM-DD` as a short date in the UI locale (parsed as UTC, never shifted). */
export function formatDay(iso: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(new Date(iso));
}

/** Share of `part` in `total` as a whole percent (display only; 0 for an empty total). */
export function sharePercent(part: number, total: number): number {
  return total > 0 ? Math.round((part * 100) / total) : 0;
}

/** One category's slot: 0–6 = a hue, `null` = folded into "Other". */
export type SlotMap = Map<string, number | null>;

/** Stable key of a category; uncategorised expenses have no id. */
export const categoryKey = (id: number | null) =>
  id === null ? "none" : String(id);

/**
 * Give every category of `months` a colour slot, keeping the slots already handed out: a filter
 * that removes categories must not repaint the survivors. New categories are ranked by their
 * total over the 24 months (largest first) and take the next free slot; past seven they share
 * "Other". Returns the same map when nothing is new.
 */
export function extendSlots(current: SlotMap, months: ExpenseMonth[]): SlotMap {
  const totals = new Map<string, number>();
  for (const month of months) {
    for (const category of month.categories) {
      const key = categoryKey(category.category_id);
      totals.set(key, (totals.get(key) ?? 0) + category.amount_cents);
    }
  }
  const fresh = [...totals.entries()]
    .filter(([key]) => !current.has(key))
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  if (fresh.length === 0) return current;
  const next: SlotMap = new Map(current);
  let used = [...next.values()].filter((slot) => slot !== null).length;
  for (const [key] of fresh) {
    next.set(key, used < SERIES_SLOTS ? used++ : null);
  }
  return next;
}

/** CSS colour of a category (its slot, or the neutral "Other"). */
export function colorOfCategory(slots: SlotMap, id: number | null): string {
  const slot = slots.get(categoryKey(id));
  return slot === undefined || slot === null ? otherColor() : seriesColor(slot);
}
