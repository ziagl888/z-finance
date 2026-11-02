/**
 * Pure helpers of the chart component: a "nice" value axis and month labels. Amounts are integer
 * cents everywhere; nothing here formats or rounds money for storage.
 */

/** Largest bar thickness in px (dataviz mark spec: bars are capped, the band's rest is air). */
export const MAX_BAR_PX = 24;

/** Smallest of 1, 2 or 5 times a power of ten that is at least `raw` (raw > 0). */
export function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw));
  const fraction = raw / power;
  const factor = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return factor * power;
}

export interface Axis {
  /** Round tick values, ascending, including 0 (the baseline is always shown). */
  ticks: number[];
  min: number;
  max: number;
}

/**
 * Value axis for data in `[low, high]`. The baseline 0 is always inside the domain — bars grow
 * from it and the axis never starts at another value — and the domain ends on a tick.
 */
export function niceAxis(low: number, high: number, tickCount = 5): Axis {
  const min = Math.min(0, low);
  const max = Math.max(0, high);
  // A chart of only zeros still gets a readable axis (0 to 10 EUR) instead of a degenerate one.
  if (max === min) return niceAxis(0, 1000, tickCount);
  const span = max - min;
  const step = niceStep(span / Math.max(1, tickCount - 1));
  const first = Math.floor(min / step) * step;
  const last = Math.ceil(max / step) * step;
  const ticks: number[] = [];
  for (let tick = first; tick <= last + step / 2; tick += step)
    ticks.push(tick);
  return { ticks, min: first, max: last === first ? first + step : last };
}

/** "YYYY-MM" → the first day of that month in UTC (the API's month key; never local time). */
function monthStart(month: string): Date {
  const [year = 1970, number = 1] = month.split("-").map(Number);
  return new Date(Date.UTC(year, number - 1, 1));
}

/** Short month name for an axis tick ("Sep"), in the UI locale. */
export function monthShort(month: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: "short",
    timeZone: "UTC",
  }).format(monthStart(month));
}

/** Month and year for tooltips and tables ("September 2026"), in the UI locale. */
export function monthLong(month: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(monthStart(month));
}

/** Year of a "YYYY-MM" key as text (shown under the first tick and every January). */
export function monthYear(month: string): string {
  return month.slice(0, 4);
}

/** Axis tick label: whole euros in the UI locale, `12k`-style compaction not used (exact values). */
export function formatAxisCents(cents: number, locale: string): string {
  const wholeEuros = cents % 100 === 0;
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: wholeEuros ? 0 : 2,
    maximumFractionDigits: wholeEuros ? 0 : 2,
  }).format(cents / 100);
}
