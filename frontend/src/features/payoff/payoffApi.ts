import { api } from "../../api/client";

export type Strategy = "snowball" | "avalanche" | "manual";
export const STRATEGIES: Strategy[] = ["snowball", "avalanche", "manual"];

/** Body of PUT /api/payoff. `budget_cents` is the amount per month on top of the instalments. */
export interface PlanInput {
  strategy: Strategy;
  budget_cents: number;
  manual_order: number[];
  /** Monthly income to plan with instead of the 3-month average; null = the average. */
  income_override_cents: number | null;
}

/** The available amount per month and its terms (all per month). */
export interface Availability {
  income_cents: number;
  income_months: string[];
  income_overridden: boolean;
  fixed_costs_cents: number;
  installments_cents: number;
  pot_rates_cents: number;
  levy_cents: number;
  levy_rate_bp: number;
  available_cents: number;
}

/** An open debt in plan order; `end_month` (YYYY-MM) = month of its last payment. */
export interface DebtLine {
  id: number;
  creditor: string;
  remaining_cents: number;
  installment_cents: number | null;
  interval: string | null;
  interest_rate_pct: string | null;
  end_month: string | null;
}

/** One plan month; `extra_to` lists [debt id, special payment] pairs. */
export interface MonthLine {
  month: string;
  installments_cents: number;
  extra_cents: number;
  extra_to: [number, number][];
  remaining_cents: number;
}

/** Planned against booked repayment of a month of the saved plan. */
export interface Progress {
  month: string;
  planned_cents: number;
  actual_cents: number;
  difference_cents: number;
  completed: boolean;
}

export interface PayoffStatus {
  saved: boolean;
  strategy: Strategy;
  manual_order: number[];
  budget_cents: number;
  total_monthly_cents: number;
  income_override_cents: number | null;
  availability: Availability;
  remaining_total_cents: number;
  has_plan: boolean;
  shortfall_cents: number;
  debts: DebtLine[];
  months: MonthLine[];
  debt_free_month: string | null;
  baseline_end_month: string | null;
  shift_months: number | null;
  progress: Progress[];
  behind: boolean;
  catch_up_cents: number;
  over_budget: boolean;
  suggested_budget_cents: number | null;
  free_now_cents: number;
  missing_pay_account: number[];
}

export interface WhatIf {
  budget_cents: number;
  total_monthly_cents: number;
  has_plan: boolean;
  debt_free_month: string | null;
  ends: Record<string, string | null>;
}

export const getPayoff = () => api.get<PayoffStatus>("/api/payoff");
export const putPayoff = (input: PlanInput) =>
  api.put<PayoffStatus>("/api/payoff", input);
export const getWhatIf = (deltaCents: number) =>
  api.get<WhatIf>(`/api/payoff/what-if?delta_cents=${deltaCents}`);

/** "YYYY-MM" as a month name and year in the UI locale, e.g. "July 2027". */
export function formatMonth(month: string, locale: string): string {
  const year = Number(month.slice(0, 4));
  const index = Number(month.slice(5, 7));
  return new Intl.DateTimeFormat(locale, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, index - 1, 1)));
}

/** Move the item at `from` to position `to` (drag and drop, up/down buttons). */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from === to || to < 0 || to >= items.length) return items;
  const next = [...items];
  const [item] = next.splice(from, 1);
  if (item === undefined) return items;
  next.splice(to, 0, item);
  return next;
}
