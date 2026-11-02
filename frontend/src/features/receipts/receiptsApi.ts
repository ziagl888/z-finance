import { api } from "../../api/client";

export type ReceiptStatus = "booked" | "review" | "duplicate";
export type ReviewReason =
  | "unreadable"
  | "missing_fields"
  | "foreign_currency"
  | "low_confidence"
  | "no_account"
  | "not_bookable";

/** The fields the receipt agent read from the photo (all may be missing). */
export interface Extracted {
  date?: string | null;
  amount_cents?: number | null;
  merchant?: string | null;
  payment_method?: string;
  category_suggestion?: string | null;
  confidence?: number | null;
  currency?: string;
  hints?: string;
}

/** A stored receipt; `booking_*` describe its booking (amount signed, expense negative). */
export interface Receipt {
  id: number;
  status: ReceiptStatus;
  reason: ReviewReason | null;
  transaction_id: number | null;
  confidence: number | null;
  extracted: Extracted;
  duplicate_of: number | null;
  message_ref: string | null;
  run_id: number | null;
  created_at: string;
  booked_at: string | null;
  booking_date: string | null;
  booking_amount_cents: number | null;
  booking_text: string | null;
  booking_account_id: number | null;
  photo_url: string;
}

/** Body of POST /api/receipts/{id}/book; `amount_cents` is the positive receipt total. */
export interface BookInput {
  date: string;
  amount_cents: number;
  merchant: string;
  account_id: number;
  category_id: number | null;
}

/** Account per area and payment method; null = chosen automatically. */
export type AccountMapping = Record<
  "private" | "business",
  Record<"cash" | "card", number | null>
>;

export interface ReceiptSettings {
  confidence_threshold: number;
  accounts: AccountMapping;
}

export interface AgentRun {
  id: number;
  started_at: string;
  finished_at: string | null;
  counts: Record<string, unknown>;
  note: string | null;
  booked: number;
  review: number;
  duplicate: number;
}

/** The review list: receipts waiting for a correction and possible duplicates. */
export const listReview = () =>
  api.get<Receipt[]>("/api/receipts?status=review&status=duplicate");
export const listBooked = () =>
  api.get<Receipt[]>("/api/receipts?status=booked&limit=20");
export const bookReceipt = (id: number, input: BookInput) =>
  api.post<Receipt>(`/api/receipts/${id}/book`, input);
export const discardReceipt = (id: number) =>
  api.delete<void>(`/api/receipts/${id}`);
export const listRuns = () => api.get<AgentRun[]>("/api/receipts/runs");
export const getReceiptSettings = () =>
  api.get<ReceiptSettings>("/api/receipts/settings");
export const putReceiptSettings = (settings: ReceiptSettings) =>
  api.put<ReceiptSettings>("/api/receipts/settings", settings);
