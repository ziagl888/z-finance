/**
 * Form state of a pot and the pure helpers of the screen (task -018, UC-ZF-011): the conversion of
 * the form to the API body, the emergency order and the split of the pots per account. Amounts are
 * typed without a sign and parsed to integer cents without floats.
 */
import {
  centsToInput,
  parseEuroInput,
  type Account,
  type Area,
} from "../accounts/accountsApi";
import type { Pot, PotInput } from "./potsApi";

/** Value of the position select for a locked pot; other values are the rank as a number. */
export const LOCKED = "locked";

export interface PotDraft {
  name: string;
  accountId: string;
  area: Area;
  target: string;
  targetDate: string;
  planExpense: boolean;
  /** `LOCKED` or the wished rank ("1" = first in an emergency). */
  position: string;
  isLevyPot: boolean;
}

export type Invalid = "name" | "account" | "target" | "planExpense";

export function emptyDraft(account: Account | undefined): PotDraft {
  return {
    name: "",
    accountId: account ? String(account.id) : "",
    area: account?.area ?? "private",
    target: "",
    targetDate: "",
    planExpense: false,
    position: "",
    isLevyPot: false,
  };
}

/** The form filled with a stored pot (for editing). */
export function draftFrom(pot: Pot, locale: string): PotDraft {
  return {
    name: pot.name,
    accountId: String(pot.account_id),
    area: pot.area,
    target:
      pot.target_amount_cents === null
        ? ""
        : centsToInput(pot.target_amount_cents, locale),
    targetDate: pot.target_date ?? "",
    planExpense: pot.plan_expense,
    position: pot.locked ? LOCKED : String(pot.emergency_rank ?? ""),
    isLevyPot: pot.is_levy_pot,
  };
}

/**
 * The API body, or the first invalid field. A target amount is optional (UC 1b) but must be above
 * 0 when given; "plan the expense" needs the amount and the date (the backend refuses it otherwise).
 */
export function toInput(
  draft: PotDraft,
): { input: PotInput } | { invalid: Invalid } {
  const name = draft.name.trim();
  if (!name) return { invalid: "name" };
  if (draft.accountId === "") return { invalid: "account" };
  let target: number | null = null;
  if (draft.target.trim() !== "") {
    target = parseEuroInput(draft.target);
    if (target === null || target <= 0) return { invalid: "target" };
  }
  const targetDate = draft.targetDate === "" ? null : draft.targetDate;
  if (draft.planExpense && (target === null || targetDate === null)) {
    return { invalid: "planExpense" };
  }
  return {
    input: {
      name,
      account_id: Number(draft.accountId),
      area: draft.area,
      target_amount_cents: target,
      target_date: targetDate,
      plan_expense: draft.planExpense,
      locked: draft.position === LOCKED,
      is_levy_pot: draft.isLevyPot,
    },
  };
}

/** Open pots of an account that take part in an emergency, in rank order. */
export function orderedPots(pots: Pot[], accountId: number): Pot[] {
  return pots
    .filter((p) => p.account_id === accountId && !p.closed && !p.locked)
    .sort(
      (a, b) =>
        (a.emergency_rank ?? Infinity) - (b.emergency_rank ?? Infinity) ||
        a.id - b.id,
    );
}

/**
 * The order of pot ids after moving `potId` into the place of the pot `before` (a drop: the
 * dragged pot takes that pot's place) or to an `index` (the Up/Down buttons and the form's
 * position). Returns null when nothing changes or an id is not in the list.
 */
export function reorder(
  ids: number[],
  potId: number,
  target: { before: number } | { index: number },
): number[] | null {
  const from = ids.indexOf(potId);
  if (from < 0) return null;
  const rest = ids.filter((id) => id !== potId);
  let to: number;
  if ("before" in target) {
    to = rest.indexOf(target.before);
    if (to < 0) return null;
    // Dropping onto a pot below the dragged one puts it after that pot, like an insertion line.
    if (from < ids.indexOf(target.before)) to += 1;
  } else {
    to = Math.max(0, Math.min(rest.length, target.index));
  }
  const next = [...rest.slice(0, to), potId, ...rest.slice(to)];
  return next.every((id, index) => id === ids[index]) ? null : next;
}

/** Sum of the amounts typed on the emergency lines, or null while one is not an amount. */
export function sumLines(texts: string[]): number | null {
  let sum = 0;
  for (const text of texts) {
    if (text.trim() === "") continue;
    const cents = parseEuroInput(text);
    if (cents === null || cents < 0) return null;
    sum += cents;
  }
  return sum;
}
