import { api } from "../../api/client";
import type { Area } from "../accounts/accountsApi";
import type { Status } from "../entry/entryApi";

export type DebtInterval = "monthly" | "quarterly" | "yearly";
export const DEBT_INTERVALS: DebtInterval[] = [
  "monthly",
  "quarterly",
  "yearly",
];

/**
 * Body of PUT /api/debts/{id}. `installment_cents`, `interval` and `first_due` are all set or
 * all null (no fixed instalment); with them `pay_account_id` is required.
 */
export interface DebtInput {
  creditor: string;
  area: Area;
  total_amount_cents: number;
  installment_cents: number | null;
  interval: DebtInterval | null;
  first_due: string | null;
  pay_account_id: number | null;
  interest_rate_pct: string | null;
  contract_end: string | null;
  target_end: string | null;
}

/** Body of POST /api/debts: the fields plus the amount already repaid. */
export interface DebtCreateInput extends DebtInput {
  repaid_cents: number;
}

/** Open instalments: how many, next and last due date, amount of the last one. */
export interface PayoffPlan {
  count: number;
  next_due: string;
  end: string;
  last_installment_cents: number;
}

export interface Debt extends DebtInput {
  id: number;
  account_id: number;
  recurring_id: number | null;
  closed: boolean;
  closed_at: string | null;
  remaining_cents: number;
  paid_cents: number;
  /** Set for an open debt with instalment. */
  plan: PayoffPlan | null;
  /** Set for an open debt without instalment that has a target end. */
  required_monthly_cents: number | null;
}

export interface Overview {
  debts: Debt[];
  remaining_by_area: Record<Area, number>;
  remaining_total_cents: number;
}

/** One booking on the liability account: + = paid off, − = debt raised. */
export interface HistoryEntry {
  id: number;
  date: string;
  amount_cents: number;
  status: Status;
  text: string;
  transfer_account_id: number | null;
}

export interface PaymentResult {
  debt: Debt;
  booked_cents: number;
  /** The amount was above the remaining amount and was lowered to it. */
  capped: boolean;
}

export interface DebtSettings {
  payoff_budget_cents: number;
}

export const listDebts = () => api.get<Overview>("/api/debts");
export const createDebt = (input: DebtCreateInput) =>
  api.post<Debt>("/api/debts", input);
export const updateDebt = (id: number, input: DebtInput) =>
  api.put<Debt>(`/api/debts/${id}`, input);
export const deleteDebt = (id: number) => api.delete<void>(`/api/debts/${id}`);
export const getHistory = (id: number) =>
  api.get<HistoryEntry[]>(`/api/debts/${id}/history`);
export const payExtra = (
  id: number,
  amountCents: number,
  date: string,
  fromAccountId: number | null,
) =>
  api.post<PaymentResult>(`/api/debts/${id}/payments`, {
    amount_cents: amountCents,
    date,
    from_account_id: fromAccountId,
  });
export const getDebtSettings = () =>
  api.get<DebtSettings>("/api/debts/settings");
export const putDebtSettings = (settings: DebtSettings) =>
  api.put<DebtSettings>("/api/debts/settings", settings);
