import { i18n } from "@lingui/core";

import { api, readResponse } from "../../api/client";

/** One snapshot folder in the backup directory (GET /api/backup, POST /api/backup/export). */
export interface Snapshot {
  name: string;
  trigger: "auto" | "manual";
  created_at: string;
  path: string;
  /** The `export.json` alone (no photos). */
  download_url: string;
  /** A ZIP of `export.json` and the receipt photos. */
  archive_url: string;
}

/** Response of GET /api/backup: newest snapshot first. */
export interface SnapshotList {
  directory: string;
  snapshots: Snapshot[];
}

/** Response of POST /api/backup/restore. */
export interface RestoreReport {
  export_schema_version: number;
  schema_version: number;
  migrations_applied: string[];
  rows: Record<string, number>;
  receipt_photos: number;
}

export const listBackups = () => api.get<SnapshotList>("/api/backup");

export const createBackup = () => api.post<Snapshot>("/api/backup/export");

/** Whether a chosen file is a backup archive (ZIP) rather than an `export.json`. */
export const isArchive = (file: File) =>
  /\.zip$/i.test(file.name) || file.type === "application/zip";

/**
 * Upload a backup file for restore: an archive (ZIP with receipt photos) as it is, an export file
 * as text. The text goes to the server unchanged: parsing and re-serialising it in the browser
 * would turn `1.0` into `1` and lose integer precision, which breaks the checksums inside the
 * export. Hence a raw body instead of `api.post`.
 */
export async function restoreBackup(file: File): Promise<RestoreReport> {
  const archive = isArchive(file);
  const headers: Record<string, string> = {
    Accept: "application/json",
    "Content-Type": archive ? "application/zip" : "application/json",
  };
  // Same as `api.*` (src/api/client.ts): error messages follow the UI language.
  if (i18n.locale) headers["Accept-Language"] = i18n.locale;
  const response = await fetch("/api/backup/restore", {
    method: "POST",
    headers,
    body: archive ? file : await file.text(),
  });
  return readResponse<RestoreReport>(response);
}
