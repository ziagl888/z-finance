import { api } from "../../api/client";

export type Interval = "weekly" | "monthly" | "quarterly" | "yearly";
export const INTERVALS: Interval[] = [
  "monthly",
  "quarterly",
  "yearly",
  "weekly",
];

/** Body of POST /api/recurring and PUT /api/recurring/{id}. Transfers have a positive amount. */
export interface RecurringInput {
  description: string;
  amount_cents: number;
  account_id: number;
  category_id: number | null;
  transfer_target_account_id: number | null;
  interval: Interval;
  /** 1–31 for monthly/quarterly/yearly, null for weekly. */
  day_of_month: number | null;
  /** 0 = Monday … 6 = Sunday for weekly, null otherwise. */
  weekday: number | null;
  start_date: string;
  end_date: string | null;
}

/** A stored recurring item; `next_due_date` is its earliest planned row. */
export interface Recurring extends RecurringInput {
  id: number;
  active: boolean;
  next_due_date: string | null;
  planned_count: number;
  created_at: string;
}

/** One occurrence of a recurring item (for a transfer: its outgoing leg). */
export interface PlannedItem {
  id: number;
  date: string;
  due_date: string;
  amount_cents: number;
  account_id: number;
  status: "booked" | "planned" | "skipped";
  moved: boolean;
  transfer_group_id: string | null;
}

/** Body of POST /api/recurring/expected: a planned income or expense dated after today. */
export interface ExpectedInput {
  date: string;
  amount_cents: number;
  account_id: number;
  category_id: number | null;
  text: string;
  counterparty: string | null;
}

export interface Expected extends ExpectedInput {
  id: number;
}

export const listRecurring = () => api.get<Recurring[]>("/api/recurring");
export const createRecurring = (input: RecurringInput) =>
  api.post<Recurring>("/api/recurring", input);
export const updateRecurring = (id: number, input: RecurringInput) =>
  api.put<Recurring>(`/api/recurring/${id}`, input);
export const setRecurringActive = (id: number, active: boolean) =>
  api.post<Recurring>(
    `/api/recurring/${id}/${active ? "activate" : "deactivate"}`,
  );
export const deleteRecurring = (id: number) =>
  api.delete<void>(`/api/recurring/${id}`);
export const listItems = (id: number) =>
  api.get<PlannedItem[]>(`/api/recurring/${id}/items`);
/** Move one planned occurrence; `amount_cents` is positive, the sign stays. */
export const moveItem = (id: number, date: string, amountCents: number) =>
  api.put<PlannedItem>(`/api/recurring/items/${id}`, {
    date,
    amount_cents: amountCents,
  });
export const listExpected = () =>
  api.get<Expected[]>("/api/recurring/expected");
export const createExpected = (input: ExpectedInput) =>
  api.post<Expected>("/api/recurring/expected", input);
/** Expected items are ordinary bookings: deleted through the transactions API. */
export const deleteExpected = (id: number) =>
  api.delete<void>(`/api/transactions/${id}`);
