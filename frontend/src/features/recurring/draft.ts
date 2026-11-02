/**
 * Form state of a recurring item and the rules around it, as plain functions (tested without a
 * DOM). Amounts stay text until saving; the server repeats every check.
 */
import { centsToInput, parseEuroInput } from "../accounts/accountsApi";
import type { Interval, Recurring, RecurringInput } from "./recurringApi";

export type Kind = "expense" | "income" | "transfer";

export interface RecurringDraft {
  kind: Kind;
  description: string;
  amount: string;
  accountId: string;
  toAccountId: string;
  categoryId: string;
  interval: Interval;
  dayOfMonth: string;
  /** "0" = Monday … "6" = Sunday. */
  weekday: string;
  startDate: string;
  endDate: string;
}

/** Which field stops the save; the form shows the matching message. */
export type Invalid =
  "description" | "amount" | "account" | "category" | "day" | "start" | "end";

/** A new item starting today, due monthly on today's day of the month. */
export function emptyDraft(today: string, accountId: string): RecurringDraft {
  return {
    kind: "expense",
    description: "",
    amount: "",
    accountId,
    toAccountId: "",
    categoryId: "",
    interval: "monthly",
    dayOfMonth: String(Number(today.slice(8, 10))),
    weekday: String((new Date(`${today}T00:00:00`).getDay() + 6) % 7),
    startDate: today,
    endDate: "",
  };
}

/** The form filled from a stored item. */
export function draftFrom(item: Recurring, locale: string): RecurringDraft {
  const kind: Kind =
    item.transfer_target_account_id !== null
      ? "transfer"
      : item.amount_cents < 0
        ? "expense"
        : "income";
  return {
    kind,
    description: item.description,
    amount: centsToInput(Math.abs(item.amount_cents), locale),
    accountId: String(item.account_id),
    toAccountId:
      item.transfer_target_account_id === null
        ? ""
        : String(item.transfer_target_account_id),
    categoryId: item.category_id === null ? "" : String(item.category_id),
    interval: item.interval,
    dayOfMonth: item.day_of_month === null ? "1" : String(item.day_of_month),
    weekday: item.weekday === null ? "0" : String(item.weekday),
    startDate: item.start_date,
    endDate: item.end_date ?? "",
  };
}

/** Positive cents of the typed amount, or null when empty, 0 or not an amount. */
export function magnitudeCents(amount: string): number | null {
  const cents = parseEuroInput(amount);
  if (cents === null || cents === 0) return null;
  return Math.abs(cents);
}

/**
 * The API body for a draft, or the first field that stops the save. Expenses are negative,
 * income and transfers positive; a category is required except for transfers.
 */
export function toInput(
  draft: RecurringDraft,
): { input: RecurringInput } | { invalid: Invalid } {
  const cents = magnitudeCents(draft.amount);
  const day = Number(draft.dayOfMonth);
  const transfer = draft.kind === "transfer";
  if (draft.description.trim() === "") return { invalid: "description" };
  if (cents === null) return { invalid: "amount" };
  if (
    draft.accountId === "" ||
    (transfer &&
      (draft.toAccountId === "" || draft.toAccountId === draft.accountId))
  ) {
    return { invalid: "account" };
  }
  if (!transfer && draft.categoryId === "") return { invalid: "category" };
  if (
    draft.interval !== "weekly" &&
    !(Number.isInteger(day) && day >= 1 && day <= 31)
  ) {
    return { invalid: "day" };
  }
  if (draft.startDate === "") return { invalid: "start" };
  if (draft.endDate !== "" && draft.endDate < draft.startDate) {
    return { invalid: "end" };
  }
  return {
    input: {
      description: draft.description.trim(),
      amount_cents: draft.kind === "expense" ? -cents : cents,
      account_id: Number(draft.accountId),
      category_id: draft.categoryId === "" ? null : Number(draft.categoryId),
      transfer_target_account_id: transfer ? Number(draft.toAccountId) : null,
      interval: draft.interval,
      day_of_month: draft.interval === "weekly" ? null : day,
      weekday: draft.interval === "weekly" ? Number(draft.weekday) : null,
      start_date: draft.startDate,
      end_date: draft.endDate === "" ? null : draft.endDate,
    },
  };
}

/** Weekday names Monday … Sunday in the UI language (2024-01-01 was a Monday). */
export function weekdayNames(locale: string): string[] {
  const format = new Intl.DateTimeFormat(locale, { weekday: "long" });
  return Array.from({ length: 7 }, (_, index) =>
    format.format(new Date(2024, 0, 1 + index)),
  );
}

/** A YYYY-MM-DD date in the UI language. */
export function formatDate(iso: string, locale: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(locale);
}
