import { api } from "../../api/client";

export type LevyBase = "income" | "profit";
export type ChartArea = "all" | "private" | "business";

/** Body of PUT /api/levy/settings (full replacement). `levy_rate_bp`: 3500 = 35 %, at most 9000. */
export interface LevySettingsInput {
  levy_rate_bp: number;
  levy_base: LevyBase;
  /** Due date of the back-payment in the following year, `MM-DD`. */
  due_month_day: string;
  levy_pot_id: number | null;
  account_id: number | null;
}

/** The stored settings; `settings_missing` while nothing was saved (rate 0). */
export interface LevySettings extends LevySettingsInput {
  settings_missing: boolean;
}

export type BackpaymentState = "open" | "settled" | "archived";

/** One open year: base, target, prepayments, the pot's share, actual, gap and the back-payment. */
export interface LevyYear {
  year: number;
  base_cents: number;
  target_cents: number;
  prepayments_cents: number;
  pot_share_cents: number;
  actual_cents: number;
  gap_cents: number;
  backpayment_cents: number;
  backpayment_date: string;
  backpayment_transaction_id: number | null;
  backpayment_state: BackpaymentState;
}

export interface LevyStatus {
  settings: LevySettings;
  levy_pot_id: number | null;
  levy_pot_missing: boolean;
  levy_pot_ambiguous: boolean;
  pot_balance_cents: number;
  years: LevyYear[];
  levy_gap_cents: number;
  free_balance_cents: number;
  levy_uncovered: boolean;
  backpayment_account_missing: boolean;
}

/** `free_cents` = accounts + planned − reserve − levy gap (planned is signed, usually negative). */
export interface FreeAmount {
  as_of: string;
  accounts_cents: number;
  planned_cents: number;
  reserve_cents: number;
  levy_gap_cents: number;
  free_cents: number;
}

/** Revenue needed per month; the block figures sum to `required_cents`. */
export interface RequiredRevenue {
  required_cents: number;
  fixed_costs_cents: number;
  installments_cents: number;
  payoff_budget_cents: number;
  pot_rates_cents: number;
  reserve_build_cents: number;
  levy_cents: number;
  levy_rate_bp: number;
}

export interface IncomeMonth {
  /** `YYYY-MM` */
  month: string;
  actual_cents: number;
  planned_cents: number;
  required_cents: number;
  below_required: boolean;
}

export interface ExpenseCategory {
  category_id: number | null;
  category_name: string | null;
  amount_cents: number;
}

export interface ExpenseMonth {
  month: string;
  /** After the current month: planned rows instead of bookings. */
  planned: boolean;
  categories: ExpenseCategory[];
  liabilities_cents: number;
  remaining_debt_cents: number;
}

/** The part of a pot the settings form needs. */
export interface PotOption {
  id: number;
  name: string;
  closed: boolean;
  is_levy_pot: boolean;
}

export const getStatus = () => api.get<LevyStatus>("/api/levy/status");
export const putSettings = (input: LevySettingsInput) =>
  api.put<LevySettings>("/api/levy/settings", input);
export const getFreeAmount = () => api.get<FreeAmount>("/api/levy/free-amount");
export const getRequiredRevenue = () =>
  api.get<RequiredRevenue>("/api/levy/required-revenue");
export const getIncomeChart = () =>
  api.get<IncomeMonth[]>("/api/levy/charts/income");
export const getExpenseChart = (area: ChartArea) =>
  api.get<ExpenseMonth[]>(`/api/levy/charts/expenses?area=${area}`);
export const archiveBackpayment = (year: number) =>
  api.post<void>(`/api/levy/backpayments/${year}/archive`);
export const listPots = () =>
  api.get<{ pots: PotOption[] }>("/api/pots").then((overview) => overview.pots);
