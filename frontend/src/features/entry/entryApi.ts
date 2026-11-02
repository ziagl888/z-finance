import { api } from "../../api/client";

export type Status = "booked" | "planned" | "skipped";
export type Source =
  "manual" | "import" | "recurring" | "receipt" | "allocation";

/** Body of POST /api/transactions and PUT /api/transactions/{id}. Amount in signed cents, never 0. */
export interface BookingInput {
  date: string;
  amount_cents: number;
  account_id: number;
  category_id: number | null;
  text: string;
  counterparty: string | null;
  memo: string | null;
}

/** Body of POST /api/transactions/transfers and PUT .../transfers/{group}. Amount positive, from → to. */
export interface TransferInput {
  date: string;
  amount_cents: number;
  from_account_id: number;
  to_account_id: number;
  category_id: number | null;
  text: string;
  memo: string | null;
}

/** A booking as the API returns it; a transfer leg names the other leg's account. */
export interface Transaction extends BookingInput {
  id: number;
  status: Status;
  source: Source;
  transfer_group_id: string | null;
  transfer_account_id: number | null;
  recurring_id: number | null;
  created_at: string;
}

/** Category and counterparty last used for an earlier booking text. */
export interface Suggestion {
  text: string;
  category_id: number | null;
  counterparty: string | null;
  last_date: string;
}

export const listTransactions = () =>
  api.get<Transaction[]>("/api/transactions");
export const createTransaction = (input: BookingInput) =>
  api.post<Transaction>("/api/transactions", input);
export const updateTransaction = (id: number, input: BookingInput) =>
  api.put<Transaction>(`/api/transactions/${id}`, input);
export const deleteTransaction = (id: number) =>
  api.delete<void>(`/api/transactions/${id}`);
export const createTransfer = (input: TransferInput) =>
  api.post<Transaction[]>("/api/transactions/transfers", input);
export const updateTransfer = (groupId: string, input: TransferInput) =>
  api.put<Transaction[]>(
    `/api/transactions/transfers/${encodeURIComponent(groupId)}`,
    input,
  );
export const suggest = (query: string) =>
  api.get<Suggestion[]>(
    `/api/transactions/suggest?q=${encodeURIComponent(query)}`,
  );
