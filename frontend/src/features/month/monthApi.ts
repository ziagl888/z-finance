import { api } from "../../api/client";

/** Report scope: one area or both ("all", the UC's "gesamt"). */
export type ReportArea = "private" | "business" | "all";
export const REPORT_AREAS: ReportArea[] = ["private", "business", "all"];

export type Flow = "income" | "expense";
export type MatchKind = "contains" | "regex";
export type InvalidReason = "syntax" | "timeout";

/** An amount with the same figure one month earlier and the six-month average before the month. */
export interface Comparison {
  amount_cents: number;
  previous_cents: number;
  average_6m_cents: number;
}

/** Income or expense of one category; `category_id` null = uncategorised. */
export interface Line {
  flow: Flow;
  category_id: number | null;
  category_name: string | null;
  parent_name: string | null;
  booking_count: number;
  comparison: Comparison;
}

/** GET /api/reports/month. Amounts are signed cents (expenses negative). */
export interface MonthReport {
  month: string;
  area: ReportArea;
  income: Comparison;
  expense: Comparison;
  balance: Comparison;
  movement_in_cents: number;
  movement_out_cents: number;
  uncategorized_count: number;
  lines: Line[];
}

/** A booking behind a line or in the uncategorised list. */
export interface MonthBooking {
  id: number;
  date: string;
  amount_cents: number;
  account_id: number;
  account_name: string;
  category_id: number | null;
  text: string;
  counterparty: string | null;
  memo: string | null;
}

/** Body of POST /api/rules; also the shape of a draft. */
export interface RuleInput {
  match_kind: MatchKind;
  pattern: string;
  category_id: number;
  counterparty: string | null;
  priority: number;
}

export interface Rule extends RuleInput {
  id: number;
  invalid_reason: InvalidReason | null;
  created_at: string;
}

export interface CreatedRule {
  rule: Rule;
  applied_count: number;
}

export interface RulePreview {
  count: number;
  samples: { id: number; date: string; text: string; amount_cents: number }[];
}

export interface ApplyResult {
  categorised: number;
  by_rule: Record<string, number>;
  timed_out: number[];
}

const query = (params: Record<string, string | number | undefined>) =>
  Object.entries(params)
    .filter(([, value]) => value !== undefined)
    .map(([key, value]) => `${key}=${encodeURIComponent(String(value))}`)
    .join("&");

/** Current month as YYYY-MM in local time (the report's default). */
export function currentMonth(now: Date = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** The month `delta` months from `month` (YYYY-MM). */
export function shiftMonth(month: string, delta: number): string {
  const [year = 0, number = 1] = month.split("-").map(Number);
  const index = year * 12 + (number - 1) + delta;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

export const getMonthReport = (month: string, area: ReportArea) =>
  api.get<MonthReport>(`/api/reports/month?${query({ month, area })}`);

/** Bookings behind a line; `categoryId` undefined selects the uncategorised bookings. */
export const getMonthBookings = (
  month: string,
  area: ReportArea,
  categoryId?: number,
  flow?: Flow,
) =>
  api.get<MonthBooking[]>(
    `/api/reports/month/bookings?${query({ month, area, category_id: categoryId, flow })}`,
  );

/** URL of the CSV download of a month and area (machine-readable: "." decimals, UTF-8). */
export const exportUrl = (month: string, area: ReportArea) =>
  `/api/reports/month/export.csv?${query({ month, area })}`;

export const listRules = () => api.get<Rule[]>("/api/rules");
export const createRule = (input: RuleInput) =>
  api.post<CreatedRule>("/api/rules", input);
export const deleteRule = (id: number) => api.delete<void>(`/api/rules/${id}`);
export const applyRules = () => api.post<ApplyResult>("/api/rules/apply");
export const previewRule = (matchKind: MatchKind, pattern: string) =>
  api.post<RulePreview>("/api/rules/preview", {
    match_kind: matchKind,
    pattern,
  });
export const draftRule = (transactionId: number) =>
  api.get<RuleInput>(
    `/api/rules/draft?${query({ transaction_id: transactionId })}`,
  );
