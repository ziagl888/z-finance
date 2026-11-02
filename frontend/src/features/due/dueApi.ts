import { api } from "../../api/client";

/** One planned item to review; `amount_cents` is signed (expense negative). */
export interface DueItem {
  id: number;
  date: string;
  due_date: string | null;
  amount_cents: number;
  account_id: number;
  /** Set for a transfer: the account the money goes to. */
  transfer_account_id: number | null;
  category_id: number | null;
  text: string;
  counterparty: string | null;
  recurring_id: number | null;
  /** Changed by hand: the recurring item's schedule no longer decides its date. */
  moved: boolean;
  days_overdue: number;
  /** More than 30 days overdue. */
  long_overdue: boolean;
  /** The date a confirm without a date books on: the planned date, at most today. */
  confirm_date: string;
}

/** The item after a change: booked (confirm) or still planned on its new date (postpone). */
export interface Booking {
  id: number;
  date: string;
  amount_cents: number;
  account_id: number;
  status: "booked" | "planned" | "skipped";
  recurring_id: number | null;
  transfer_group_id: string | null;
}

/** Overrides of a confirm; `amount_cents` is positive (the sign of the item stays). */
export interface ConfirmBody {
  amount_cents?: number;
  date?: string;
}

export const listDue = () => api.get<DueItem[]>("/api/due");
export const confirmDue = (id: number, body: ConfirmBody) =>
  api.post<Booking>(`/api/due/${id}/confirm`, body);
export const skipDue = (id: number) => api.post<Booking>(`/api/due/${id}/skip`);
export const postponeDue = (id: number, date: string) =>
  api.post<Booking>(`/api/due/${id}/postpone`, { date });
