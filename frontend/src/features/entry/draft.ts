/**
 * Form state of the quick entry and the rules around it, as plain functions (tested without a DOM).
 * Amounts stay text until saving, so "12," can be typed on the way to "12,50".
 */
import { centsToInput, parseEuroInput } from "../accounts/accountsApi";
import type { Category } from "../accounts/accountsApi";
import type { Transaction } from "./entryApi";

export type Kind = "expense" | "income" | "transfer";

export interface Draft {
  kind: Kind;
  date: string;
  /** Account of an income/expense, source account of a transfer ("" = none). */
  accountId: string;
  /** Target account of a transfer. */
  toAccountId: string;
  amount: string;
  text: string;
  categoryId: string;
  counterparty: string;
  memo: string;
}

/** Local date as YYYY-MM-DD (the backend decides planned vs. booked the same way). */
export function todayIso(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A fresh form: only date and account are filled (UC-ZF-002 AK2). */
export function emptyDraft(date: string, accountId: string): Draft {
  return {
    kind: "expense",
    date,
    accountId,
    toAccountId: "",
    amount: "",
    text: "",
    categoryId: "",
    counterparty: "",
    memo: "",
  };
}

/** The form after a successful save: date and account stay, everything else is empty. */
export function afterSave(draft: Draft): Draft {
  return emptyDraft(draft.date, draft.accountId);
}

/**
 * Amount typed by the user as positive cents, or null when it is empty, 0 or not an amount
 * (UC-ZF-002 3b). A leading minus is allowed and ignored here; see `effectiveKind`.
 */
export function magnitudeCents(amount: string): number | null {
  const cents = parseEuroInput(amount);
  if (cents === null || cents === 0) return null;
  return Math.abs(cents);
}

/** A typed minus makes an income/expense an expense ("negativ oder mit Umschalter"). */
export function effectiveKind(draft: Draft): Kind {
  if (draft.kind !== "transfer" && draft.amount.trim().startsWith("-")) {
    return "expense";
  }
  return draft.kind;
}

/** Signed cents of an income/expense: expense negative, income positive. */
export function signedCents(draft: Draft): number | null {
  const cents = magnitudeCents(draft.amount);
  if (cents === null) return null;
  return effectiveKind(draft) === "expense" ? -cents : cents;
}

/** A date after today is saved as a planned item (UC-ZF-002 3c). */
export function isFuture(date: string, today: string): boolean {
  return date > today;
}

/**
 * Category kind contradicts the amount (income category on an expense or the other way round):
 * UC-ZF-001 3b warns but allows it (refunds, reversals).
 */
export function kindMismatch(
  draft: Draft,
  category: Category | undefined,
): boolean {
  if (!category || draft.kind === "transfer") return false;
  const kind = effectiveKind(draft);
  return (
    (category.kind === "income" && kind === "expense") ||
    (category.kind === "expense" && kind === "income")
  );
}

/** Case-insensitive text comparison used to apply a suggestion. */
export function sameText(a: string, b: string): boolean {
  return a.trim().toLocaleLowerCase() === b.trim().toLocaleLowerCase();
}

/** The form filled from a stored booking (for a transfer leg: the whole transfer). */
export function draftFrom(item: Transaction, locale: string): Draft {
  const amount = centsToInput(Math.abs(item.amount_cents), locale);
  const base = {
    date: item.date,
    amount,
    text: item.text,
    categoryId: item.category_id === null ? "" : String(item.category_id),
    memo: item.memo ?? "",
  };
  if (item.transfer_group_id !== null && item.transfer_account_id !== null) {
    const outgoing = item.amount_cents < 0;
    const from = outgoing ? item.account_id : item.transfer_account_id;
    const to = outgoing ? item.transfer_account_id : item.account_id;
    return {
      ...base,
      kind: "transfer",
      accountId: String(from),
      toAccountId: String(to),
      counterparty: "",
    };
  }
  return {
    ...base,
    kind: item.amount_cents < 0 ? "expense" : "income",
    accountId: String(item.account_id),
    toAccountId: "",
    counterparty: item.counterparty ?? "",
  };
}
