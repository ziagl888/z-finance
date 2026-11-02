import { api } from "../../api/client";

export type TaxKind = "est" | "svs";

/**
 * Body of PUT /api/settings/tax. Amounts are cents per due date (null = not entered); due dates
 * are "MM-DD" (day 31 = last day of the month). There is no VAT field (small business).
 */
export interface TaxInput {
  business_account_id: number | null;
  est_vz_quarter_cents: number | null;
  svs_quarter_cents: number | null;
  est_due_dates: string[];
  svs_due_dates: string[];
}

/** One current plan item: a due date with its recurring item and the next planned date. */
export interface TaxTerm {
  kind: TaxKind;
  due: string;
  recurring_id: number;
  amount_cents: number;
  next_due_date: string | null;
  planned_count: number;
}

export interface TaxSettings extends TaxInput {
  /** No amount or no account entered yet: the dashboard hint "Taxes & levies not set". */
  plan_missing: boolean;
  terms: TaxTerm[];
}

export const getTaxSettings = () => api.get<TaxSettings>("/api/settings/tax");
export const putTaxSettings = (input: TaxInput) =>
  api.put<TaxSettings>("/api/settings/tax", input);
