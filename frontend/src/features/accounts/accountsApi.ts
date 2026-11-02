import { api } from "../../api/client";

export type Area = "private" | "business";
export type AccountType = "giro" | "savings" | "cash" | "card" | "liability";
export type CategoryKind = "income" | "expense" | "transfer";

export const AREAS: Area[] = ["private", "business"];
export const ACCOUNT_TYPES: AccountType[] = [
  "giro",
  "savings",
  "cash",
  "card",
  "liability",
];
export const CATEGORY_KINDS: CategoryKind[] = ["income", "expense", "transfer"];

/** Body of POST /api/accounts and PUT /api/accounts/{id}. Amounts in integer cents. */
export interface AccountInput {
  name: string;
  area: Area;
  type: AccountType;
  opening_balance_cents: number;
  opening_date: string;
  reserve_floor_cents: number;
}

/** An account as the API returns it, with its balance on `balance_date`. */
export interface Account extends AccountInput {
  id: number;
  archived_at: string | null;
  balance_cents: number;
  balance_date: string;
  booking_count: number;
}

/** Body of POST /api/categories and PUT /api/categories/{id}. */
export interface CategoryInput {
  name: string;
  kind: CategoryKind;
  area: Area | null;
  parent_id: number | null;
}

export interface Category extends CategoryInput {
  id: number;
  booking_count: number;
}

/** One entry of the default category set (top level). */
export interface CategoryDraft {
  name: string;
  kind: CategoryKind;
  area: Area | null;
}

/** Response of GET /api/accounts/setup. */
export interface SetupState {
  offer_default_set: boolean;
  language: string;
  default_categories: CategoryDraft[];
}

export interface AccountSettings {
  total_reserve_floor_cents: number;
}

export const listAccounts = (includeArchived: boolean) =>
  api.get<Account[]>(
    `/api/accounts${includeArchived ? "?include_archived=true" : ""}`,
  );
export const createAccount = (input: AccountInput) =>
  api.post<Account>("/api/accounts", input);
export const updateAccount = (id: number, input: AccountInput) =>
  api.put<Account>(`/api/accounts/${id}`, input);
export const archiveAccount = (id: number, archived: boolean) =>
  api.post<Account>(
    `/api/accounts/${id}/${archived ? "archive" : "unarchive"}`,
  );
export const deleteAccount = (id: number) =>
  api.delete<void>(`/api/accounts/${id}`);

export const listCategories = () => api.get<Category[]>("/api/categories");
export const createCategory = (input: CategoryInput) =>
  api.post<Category>("/api/categories", input);
export const updateCategory = (id: number, input: CategoryInput) =>
  api.put<Category>(`/api/categories/${id}`, input);
export const deleteCategory = (id: number, targetId?: number) =>
  api.delete<void>(
    `/api/categories/${id}${targetId === undefined ? "" : `?target_id=${targetId}`}`,
  );

export const getSetup = (language: string) =>
  api.get<SetupState>(
    `/api/accounts/setup?language=${encodeURIComponent(language)}`,
  );
export const completeSetup = (categories: CategoryDraft[]) =>
  api.post<Category[]>("/api/accounts/setup", { categories });

export const getAccountSettings = () =>
  api.get<AccountSettings>("/api/accounts/settings");
export const putAccountSettings = (settings: AccountSettings) =>
  api.put<AccountSettings>("/api/accounts/settings", settings);

/** How the page lets a section change data: run it, reload, resolve to false on failure (message shown). */
export type RunChange = (change: () => Promise<unknown>) => Promise<boolean>;

const EURO_INPUT = /^(-)?(\d{1,13})(?:[.,](\d{1,2}))?$/;

/**
 * Parse a euro amount typed by the user ("1234", "-200", "12.5", "12,50") into integer cents
 * without going through a float. Returns null for anything else (thousands separators, three
 * decimals, letters, empty).
 */
export function parseEuroInput(text: string): number | null {
  const match = EURO_INPUT.exec(text.replace(/\s/g, ""));
  if (!match) return null;
  const [, minus, euros, fraction = ""] = match;
  const cents = Number(euros) * 100 + Number(fraction.padEnd(2, "0"));
  return minus && cents !== 0 ? -cents : cents;
}

/** Cents as an editable amount ("-1234.56"; "," as separator for German). */
export function centsToInput(cents: number, locale: string): string {
  const sign = cents < 0 ? "-" : "";
  const absolute = Math.abs(cents);
  const fraction = String(absolute % 100).padStart(2, "0");
  const separator = locale.startsWith("de") ? "," : ".";
  return `${sign}${Math.floor(absolute / 100)}${separator}${fraction}`;
}
