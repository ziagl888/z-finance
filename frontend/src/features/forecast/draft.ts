/**
 * Form state and view rules of the forecast screen as plain functions (tested without a DOM).
 * Amounts stay text until adding, so "12," can be typed on the way to "12,50".
 */
import {
  parseEuroInput,
  type Account,
  type Area,
} from "../accounts/accountsApi";
import {
  HORIZONS,
  type ForecastItem,
  type ForecastView,
  type Horizon,
  type WhatIfItem,
} from "./forecastApi";

/** The API takes at most this many what-if items per request. */
export const MAX_WHAT_IF_ITEMS = 50;

/** Value of the view selector: "total", "area:private", "area:business" or "account:<id>". */
export type ViewValue = string;

export const TOTAL_VIEW: ViewValue = "total";

export function viewOf(value: ViewValue): ForecastView {
  if (value.startsWith("account:")) {
    return { accountId: Number(value.slice("account:".length)), area: null };
  }
  if (value === "area:private" || value === "area:business") {
    return { accountId: null, area: value.slice("area:".length) as Area };
  }
  return { accountId: null, area: null };
}

/** Accounts a forecast can be about: liabilities are not part of any liquidity view (UC 2c). */
export function paymentAccounts(accounts: Account[]): Account[] {
  return accounts.filter(
    (account) => account.type !== "liability" && account.archived_at === null,
  );
}

/** Whether an account's items count in a view (the API ignores what-if items outside it). */
export function inView(
  account: Account | undefined,
  view: ForecastView,
): boolean {
  if (!account || account.type === "liability") return false;
  if (view.accountId !== null) return account.id === view.accountId;
  return view.area === null || account.area === view.area;
}

export function isHorizon(value: number): value is Horizon {
  return (HORIZONS as readonly number[]).includes(value);
}

/** The day after `iso` (a calendar date, UTC arithmetic so a DST change never shifts it). */
export function nextDay(iso: string): string {
  const [year = 1970, month = 1, day = 1] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + 1))
    .toISOString()
    .slice(0, 10);
}

export type Direction = "expense" | "income";

/** The what-if form: direction and a positive amount, a day, an account and an optional text. */
export interface WhatIfDraft {
  direction: Direction;
  amount: string;
  date: string;
  accountId: string;
  text: string;
}

export function emptyWhatIf(date: string, accountId: string): WhatIfDraft {
  return { direction: "expense", amount: "", date, accountId, text: "" };
}

export type Invalid = "amount" | "date" | "account" | "limit";

/**
 * The form as an item, or the first thing wrong with it. The amount is typed positive and signed
 * by the direction (a typed minus means an expense); the date must be `start` (today) or later,
 * like the API demands.
 */
export function whatIfItem(
  draft: WhatIfDraft,
  start: string,
  count: number,
): { item: WhatIfItem } | { invalid: Invalid } {
  const cents = parseEuroInput(draft.amount);
  if (cents === null || cents === 0) return { invalid: "amount" };
  if (draft.date === "" || draft.date < start) return { invalid: "date" };
  const accountId = Number(draft.accountId);
  if (draft.accountId === "" || !Number.isInteger(accountId)) {
    return { invalid: "account" };
  }
  if (count >= MAX_WHAT_IF_ITEMS) return { invalid: "limit" };
  const expense = draft.direction === "expense" || cents < 0;
  return {
    item: {
      date: draft.date,
      amount_cents: expense ? -Math.abs(cents) : Math.abs(cents),
      account_id: accountId,
      text: draft.text.trim(),
    },
  };
}

/** An expected item must lie after today (`POST /api/recurring/expected`). */
export function canAdopt(item: WhatIfItem, start: string): boolean {
  return item.date > start;
}

/** The what-if items that act on `day` in the view, as rows of the day list. */
export function whatIfRows(
  items: WhatIfItem[],
  day: string,
  accounts: Account[],
  view: ForecastView,
): ForecastItem[] {
  return items
    .filter(
      (item) =>
        item.date === day &&
        inView(
          accounts.find((account) => account.id === item.account_id),
          view,
        ),
    )
    .map((item) => ({
      transaction_id: null,
      kind: "what_if" as const,
      effective_date: item.date,
      date: item.date,
      account_id: item.account_id,
      amount_cents: item.amount_cents,
      text: item.text,
      transfer_group_id: null,
      overdue: false,
    }));
}

/** Sum of the rows of a day: what the day changes on the curve. */
export function dayChange(rows: ForecastItem[]): number {
  return rows.reduce((sum, row) => sum + row.amount_cents, 0);
}
