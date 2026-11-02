import { i18n } from "@lingui/core";

import { api, ApiError } from "../../api/client";
import type { Transaction } from "../entry/entryApi";

export type Encoding = "auto" | "utf-8" | "cp1252";
export type Delimiter = "auto" | "semicolon" | "comma" | "tab";
export type DecimalSep = "," | ".";

/** Header names per role; `amount` or `debit` + `credit` (the profile's column mapping). */
export interface ColumnMap {
  date: string;
  text: string;
  amount: string | null;
  debit: string | null;
  credit: string | null;
  counterparty: string | null;
}

/** Body of POST /api/imports/profiles and PUT .../profiles/{id} (full replacement). */
export interface ProfileInput {
  bank_name: string;
  column_map: ColumnMap;
  delimiter: Delimiter;
  encoding: Encoding;
  date_format: string;
  decimal_sep: DecimalSep;
  skip_rows: number;
}

export interface Profile extends ProfileInput {
  id: number;
  created_at: string;
}

/** POST /api/imports/detect: the dialect actually used, the header and the first five records. */
export interface Detected {
  encoding: string;
  delimiter: string;
  header: string[];
  rows: string[][];
}

export type LineKind = "new" | "duplicate" | "match" | "duplicate_suspect";

/** One classified line of POST /api/imports/preview; amounts are signed cents. */
export interface PreviewLine {
  line: number;
  date: string;
  amount_cents: number;
  text: string;
  counterparty: string | null;
  kind: LineKind;
  import_hash: string;
  planned_id: number | null;
  candidates: number[];
  review: boolean;
  suspect_id: number | null;
  category_id: number | null;
  category_source: "rule" | "planned" | null;
}

/** A line that is not imported, with the reason (in the request language). */
export interface LineError {
  line: number;
  message: string;
}

export interface Preview {
  preview_id: string;
  account_id: number;
  profile_id: number;
  file_hash: string;
  encoding: string;
  delimiter: string;
  counts: Record<string, number>;
  lines: PreviewLine[];
  errors: LineError[];
}

/** A correction of one line for the commit. `planned_id` (an id or null) is sent only to change the match. */
export interface Override {
  line: number;
  skip?: boolean;
  category_id?: number;
  planned_id?: number | null;
}

/** A committed import; `counts`: lines, new, matched, duplicates, suspects, skipped, errors, uncategorized. */
export interface Run {
  id: number;
  account_id: number;
  profile_id: number | null;
  file_name: string | null;
  file_hash: string;
  counts: Record<string, number>;
  errors: LineError[];
  created_at: string;
}

export const listProfiles = () => api.get<Profile[]>("/api/imports/profiles");
export const createProfile = (input: ProfileInput) =>
  api.post<Profile>("/api/imports/profiles", input);
export const updateProfile = (id: number, input: ProfileInput) =>
  api.put<Profile>(`/api/imports/profiles/${id}`, input);
export const deleteProfile = (id: number) =>
  api.delete<void>(`/api/imports/profiles/${id}`);

export const commitImport = (previewId: string, overrides: Override[]) =>
  api.post<Run>("/api/imports/commit", { preview_id: previewId, overrides });
export const listRuns = () => api.get<Run[]>("/api/imports/runs");

/** A planned or booked entry the preview refers to (`planned_id`, `candidates`, `suspect_id`). */
export const getBooking = (id: number) =>
  api.get<Transaction>(`/api/transactions/${id}`);

/** The file's bytes, untouched: the encoding is part of the file. FileReader also runs in jsdom. */
export function readFileBytes(file: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.onerror = () =>
      reject(reader.error ?? new Error("Reading the file failed"));
    reader.readAsArrayBuffer(file);
  });
}

/**
 * POST the raw file as the request body (the API takes `text/csv`, not a multipart upload). The
 * `api` client speaks JSON only, so this repeats its error handling: a failure throws `ApiError`
 * with the backend's `detail` in the UI language.
 */
async function postFile<T>(path: string, file: ArrayBuffer): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": "text/csv",
  };
  if (i18n.locale) headers["Accept-Language"] = i18n.locale;
  const response = await fetch(path, { method: "POST", headers, body: file });
  const payload: unknown = await response.json().catch(() => undefined);
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && "detail" in payload
        ? payload.detail
        : payload;
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

const query = (params: Record<string, string | number>) =>
  Object.entries(params)
    .map(([key, value]) => `${key}=${encodeURIComponent(String(value))}`)
    .join("&");

/** Encoding, delimiter, header and first five records; `auto` refuses an undetectable file. */
export const detectFile = (
  file: ArrayBuffer,
  encoding: Encoding,
  delimiter: Delimiter,
  skipRows: number,
) =>
  postFile<Detected>(
    `/api/imports/detect?${query({ encoding, delimiter, skip_rows: skipRows })}`,
    file,
  );

/** Classify the file's lines for the account; nothing is written until `commitImport`. */
export const previewFile = (
  file: ArrayBuffer,
  profileId: number,
  accountId: number,
  fileName: string,
) =>
  postFile<Preview>(
    `/api/imports/preview?${query({ profile_id: profileId, account_id: accountId, file_name: fileName })}`,
    file,
  );
