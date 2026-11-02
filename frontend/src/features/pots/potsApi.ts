import { api } from "../../api/client";
import type { Area } from "../accounts/accountsApi";
import type { Transaction } from "../entry/entryApi";

export type PotStatus = "on_track" | "behind" | "reached";
export type AllocationKind =
  "deposit" | "withdraw" | "transfer_in" | "transfer_out" | "emergency";

/**
 * Body of POST /api/pots and PUT /api/pots/{id} (full replacement). The account cannot change on
 * update. `plan_expense` needs the target amount and the target date.
 */
export interface PotInput {
  name: string;
  account_id: number;
  area: Area;
  target_amount_cents: number | null;
  target_date: string | null;
  plan_expense: boolean;
  locked: boolean;
  is_levy_pot: boolean;
}

/**
 * A pot with its figures today. `status`, `monthly_rate_cents` and `expected_reach` are null
 * without the data they need (UC 1a, 1b); `emergency_rank` is null for a locked or closed pot.
 */
export interface Pot extends PotInput {
  id: number;
  emergency_rank: number | null;
  plan_start: string;
  planned_transaction_id: number | null;
  closed: boolean;
  closed_at: string | null;
  balance_cents: number;
  progress_pct: number | null;
  monthly_rate_cents: number | null;
  status: PotStatus | null;
  expected_balance_cents: number | null;
  savings_plan_id: number | null;
  savings_plan_amount_cents: number | null;
  expected_reach: string | null;
}

/** An account's balance split into pots and free balance (UC step 4). */
export interface AccountSplit {
  account_id: number;
  balance_cents: number;
  pots_cents: number;
  free_cents: number;
  /** Sum of the open, unlocked pots: what the emergency order can give. */
  emergency_available_cents: number;
}

/** Pots (open first, per account in emergency order) and the accounts they are on. */
export interface Overview {
  pots: Pot[];
  accounts: AccountSplit[];
}

/** One movement of a pot: + in, − out. */
export interface Allocation {
  id: number;
  date: string;
  amount_cents: number;
  kind: AllocationKind;
  transaction_id: number | null;
  transfer_group_id: string | null;
  memo: string | null;
}

export interface ExpenseResult {
  pot: Pot;
  covered_cents: number;
  from_free_cents: number;
}

export interface EmergencyLine {
  pot_id: number;
  name: string;
  rank: number;
  amount_cents: number;
  new_balance_cents: number;
  target_date: string | null;
  shifted_target_date: string | null;
}

export interface EmergencyProposal {
  account_id: number;
  amount_cents: number;
  lines: EmergencyLine[];
  covered_cents: number;
  shortfall_cents: number;
}

export const listPots = () => api.get<Overview>("/api/pots");
export const createPot = (input: PotInput) => api.post<Pot>("/api/pots", input);
export const updatePot = (id: number, input: PotInput) =>
  api.put<Pot>(`/api/pots/${id}`, input);
export const deletePot = (id: number) => api.delete<void>(`/api/pots/${id}`);
export const closePot = (id: number) => api.post<Pot>(`/api/pots/${id}/close`);
export const getAllocations = (id: number) =>
  api.get<Allocation[]>(`/api/pots/${id}/allocations`);

export const deposit = (
  id: number,
  amountCents: number,
  fromAccountId: number | null,
) =>
  api.post<Pot>(`/api/pots/${id}/deposits`, {
    amount_cents: amountCents,
    from_account_id: fromAccountId,
  });
export const withdraw = (
  id: number,
  amountCents: number,
  confirmLocked: boolean,
) =>
  api.post<Pot>(`/api/pots/${id}/withdrawals`, {
    amount_cents: amountCents,
    confirm_locked: confirmLocked,
  });
export const assignExpense = (
  id: number,
  transactionId: number,
  confirmLocked: boolean,
) =>
  api.post<ExpenseResult>(`/api/pots/${id}/expenses`, {
    transaction_id: transactionId,
    confirm_locked: confirmLocked,
  });
export const movePots = (
  fromPotId: number,
  toPotId: number,
  amountCents: number,
  confirmLocked: boolean,
) =>
  api.post<Pot[]>("/api/pots/transfers", {
    from_pot_id: fromPotId,
    to_pot_id: toPotId,
    amount_cents: amountCents,
    confirm_locked: confirmLocked,
  });

export const putRanks = (accountId: number, potIds: number[]) =>
  api.put<Overview>("/api/pots/ranks", {
    account_id: accountId,
    pot_ids: potIds,
  });

export const getProposal = (accountId: number, amountCents: number) =>
  api.get<EmergencyProposal>(
    `/api/pots/emergency?account_id=${accountId}&amount_cents=${amountCents}`,
  );
export const confirmEmergency = (
  accountId: number,
  lines: { pot_id: number; amount_cents: number }[],
) => api.post<Pot[]>("/api/pots/emergency", { account_id: accountId, lines });

export const putSavingsPlan = (
  id: number,
  fromAccountId: number,
  amountCents: number,
) =>
  api.put<Pot>(`/api/pots/${id}/savings-plan`, {
    from_account_id: fromAccountId,
    amount_cents: amountCents,
  });
export const deleteSavingsPlan = (id: number) =>
  api.delete<Pot>(`/api/pots/${id}/savings-plan`);

/** The newest bookings of an account (up to 100), for putting an expense on a pot. */
export const listAccountBookings = (accountId: number) =>
  api.get<Transaction[]>(`/api/transactions?account_id=${accountId}&limit=100`);
