/**
 * What a row of the due list sends, as plain functions (tested without a DOM). The amount stays
 * text until the user confirms; the server repeats every check.
 */
import { centsToInput, parseEuroInput } from "../accounts/accountsApi";
import type { ConfirmBody, DueItem } from "./dueApi";

/** The inline fields of a row, prefilled with what a plain confirm would book. */
export interface RowDraft {
  amount: string;
  date: string;
}

/** Which field stops the confirm; the row shows the matching message. */
export type Invalid = "amount" | "date";

export function rowDraft(item: DueItem, locale: string): RowDraft {
  return {
    amount: centsToInput(Math.abs(item.amount_cents), locale),
    date: item.confirm_date,
  };
}

/**
 * The confirm body for the typed fields, or the field that stops it. Only what differs from the
 * plan is sent: an untouched row confirms with an empty body, so the plan stays authoritative.
 * The amount is positive cents; the item's sign is kept by the server.
 */
export function confirmBody(
  item: DueItem,
  draft: RowDraft,
): { body: ConfirmBody } | { invalid: Invalid } {
  const cents = parseEuroInput(draft.amount);
  if (cents === null || cents <= 0) return { invalid: "amount" };
  if (draft.date === "") return { invalid: "date" };
  const body: ConfirmBody = {};
  if (cents !== Math.abs(item.amount_cents)) body.amount_cents = cents;
  if (draft.date !== item.confirm_date) body.date = draft.date;
  return { body };
}

/** Tomorrow (local calendar day) as YYYY-MM-DD — the default of a postponed item. */
export function tomorrow(now: Date = new Date()): string {
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}
