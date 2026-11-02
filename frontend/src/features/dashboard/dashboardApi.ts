/**
 * `GET /api/dashboard` — everything the start page shows, in one response (UC NF5, SPEC AK8).
 * Amounts are integer cents; every figure belongs to the feature the matching card links to.
 */
import { api } from "../../api/client";

/** The one figure and its terms; `levy_gap_cents` is the sub line "reserved for levies". */
export interface FreeAmount {
  /** End of the current month, `YYYY-MM-DD`. */
  as_of: string;
  accounts_cents: number;
  planned_cents: number;
  reserve_cents: number;
  levy_gap_cents: number;
  free_cents: number;
}

/** Remaining debt now and at the end of the previous month; `trend_cents` < 0 = shrinking. */
export interface DebtFigure {
  total_cents: number;
  previous_month_cents: number;
  trend_cents: number;
}

export interface Balances {
  private_cents: number;
  business_cents: number;
  total_cents: number;
}

/** A planned item of the next 14 days; `days_overdue` > 0 means it is late. */
export interface DueItem {
  id: number;
  date: string;
  amount_cents: number;
  text: string;
  account_id: number;
  category_id: number | null;
  days_overdue: number;
  long_overdue: boolean;
}

export interface MonthLine {
  flow: "income" | "expense";
  category_id: number | null;
  category_name: string | null;
  amount_cents: number;
}

export interface MonthOverview {
  /** `YYYY-MM` */
  month: string;
  income_cents: number;
  expense_cents: number;
  balance_cents: number;
  uncategorized_count: number;
  top_lines: MonthLine[];
}

/** One day of the mini curve: balance and the line after levies. */
export interface CurveDay {
  date: string;
  balance_cents: number;
  available_cents: number;
}

/** An open allocation proposal; `overdue` is the 7-day highlight (UC-ZF-014 4b). */
export interface Proposal {
  id: number;
  date: string;
  text: string;
  amount_cents: number;
  bookable_cents: number;
  rule_name: string | null;
  days_open: number;
  overdue: boolean;
}

export interface Hints {
  levies_not_covered: boolean;
  tax_plan_missing: boolean;
}

export interface Dashboard {
  today: string;
  month_end: string;
  free: FreeAmount;
  debt: DebtFigure;
  overdue_count: number;
  due_items: DueItem[];
  balances: Balances;
  month: MonthOverview;
  mini_curve: CurveDay[];
  proposals: Proposal[];
  receipts_review_count: number;
  hints: Hints;
}

export const getDashboard = () => api.get<Dashboard>("/api/dashboard");
