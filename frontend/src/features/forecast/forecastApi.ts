import { api } from "../../api/client";
import type { Area } from "../accounts/accountsApi";

/** Horizons the API accepts, in months. */
export const HORIZONS = [3, 6, 12] as const;
export type Horizon = (typeof HORIZONS)[number];

/** What the forecast looks at: one account, else one area, else all payment accounts. */
export interface ForecastView {
  accountId: number | null;
  area: Area | null;
}

/** One day of the curve: the balance and the line after levies. */
export interface ForecastDay {
  date: string;
  balance_cents: number;
  available_cents: number;
}

/** GET /api/forecast: the curve plus UC step 3; minimum and breach refer to `available_cents`. */
export interface Forecast {
  scope: "account" | "area" | "total";
  account_id: number | null;
  area: Area | null;
  horizon_months: number;
  start: string;
  end: string;
  days: ForecastDay[];
  minimum_date: string;
  minimum_cents: number;
  reserve_cents: number;
  reserve_missing: boolean;
  first_breach_date: string | null;
  shortfall_cents: number;
  planned_income_cents: number;
  planned_expense_cents: number;
  overdue_count: number;
  levy_applies: boolean;
  levy_gap_cents: number;
}

/** A hypothetical item: signed cents, a day from today on, an account. Never stored. */
export interface WhatIfItem {
  date: string;
  amount_cents: number;
  account_id: number;
  text: string;
}

export type ItemKind = "planned" | "booked" | "what_if";

/** A row acting on one day of the curve; overdue rows act on today. */
export interface ForecastItem {
  transaction_id: number | null;
  kind: ItemKind;
  effective_date: string;
  date: string;
  account_id: number;
  amount_cents: number;
  text: string;
  transfer_group_id: string | null;
  overdue: boolean;
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

/** What the pots of one account would give, in emergency order. */
export interface EmergencyProposal {
  account_id: number;
  amount_cents: number;
  lines: EmergencyLine[];
  covered_cents: number;
  shortfall_cents: number;
}

/** GET /api/forecast/emergency; the API answers `null` while the reserve holds. */
export interface Emergency {
  shortfall_cents: number;
  proposals: EmergencyProposal[];
  covered_cents: number;
  uncovered_cents: number;
}

/** Query parts of a view: the account wins over the area, neither means all payment accounts. */
function viewQuery(view: ForecastView): string[] {
  if (view.accountId !== null) return [`account_id=${view.accountId}`];
  return view.area !== null ? [`area=${view.area}`] : [];
}

const query = (parts: string[]) =>
  parts.length === 0 ? "" : `?${parts.join("&")}`;

export const getForecast = (view: ForecastView, horizon: number) =>
  api.get<Forecast>(
    `/api/forecast${query([...viewQuery(view), `horizon=${horizon}`])}`,
  );

/** The same curve with hypothetical items; the server stores nothing. */
export const postWhatIf = (
  view: ForecastView,
  horizon: number,
  items: WhatIfItem[],
) =>
  api.post<Forecast>("/api/forecast/what-if", {
    account_id: view.accountId,
    area: view.area,
    horizon,
    items,
  });

export const getItems = (day: string, view: ForecastView) =>
  api.get<ForecastItem[]>(
    `/api/forecast/items${query([`date=${day}`, ...viewQuery(view)])}`,
  );

export const getEmergency = (view: ForecastView, horizon: number) =>
  api.get<Emergency | null>(
    `/api/forecast/emergency${query([...viewQuery(view), `horizon=${horizon}`])}`,
  );

/** Takes the confirmed proposal out of the pots (POST /api/pots/emergency). */
export const takeEmergency = (proposal: EmergencyProposal) =>
  api.post<unknown>("/api/pots/emergency", {
    account_id: proposal.account_id,
    lines: proposal.lines.map((line) => ({
      pot_id: line.pot_id,
      amount_cents: line.amount_cents,
    })),
  });
