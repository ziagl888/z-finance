// @vitest-environment jsdom
/**
 * Backup page (task -033): archive download links, what a restore sends for a ZIP versus a JSON
 * file, and the photo count in the success notice. The API is a fake `fetch`; every value is
 * synthetic.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { BackupPage } from "./BackupPage";
import { isArchive, restoreBackup } from "./backupApi";

const NAME = "20260919T101530123Z-manual";
const REPORT = {
  export_schema_version: 90,
  schema_version: 90,
  migrations_applied: [],
  rows: { receipt: 2 },
  receipt_photos: 2,
};

interface Sent {
  contentType: string | undefined;
  body: unknown;
}

/** A fake API: one snapshot on the list, every restore is recorded and answered with `REPORT`. */
function fakeApi() {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/backup/restore") {
        const headers = init?.headers as Record<string, string>;
        sent.push({ contentType: headers["Content-Type"], body: init?.body });
        return Response.json(REPORT);
      }
      return Response.json({
        directory: "data/backups",
        snapshots: [
          {
            name: NAME,
            trigger: "manual",
            created_at: "2026-09-19T10:15:30.123+00:00",
            path: `data/backups/${NAME}`,
            download_url: `/api/backup/${NAME}/download`,
            archive_url: `/api/backup/${NAME}/archive`,
          },
        ],
      });
    }),
  );
  return sent;
}

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("restore upload", () => {
  it("sends a ZIP as it is and an export as text", async () => {
    // Oracle: spec-anchored — SPEC-T-2026-CU-9130-033 restore: application/zip for the archive, application/json for export.json.
    const sent = fakeApi();
    const archive = new File(["PK"], "z-finance-backup.zip");
    const exported = new File(['{"a":1.0}'], "export.json");

    await restoreBackup(archive);
    await restoreBackup(exported);

    expect(sent[0]).toEqual({ contentType: "application/zip", body: archive });
    expect(sent[1]).toEqual({
      contentType: "application/json",
      body: '{"a":1.0}',
    });
  });

  it("recognises an archive by name or type", () => {
    // Oracle: spec-anchored — file chooser accepts .zip and .json; anything not a ZIP is treated as an export.
    expect(isArchive(new File([""], "Backup.ZIP"))).toBe(true);
    expect(
      isArchive(new File([""], "download", { type: "application/zip" })),
    ).toBe(true);
    expect(isArchive(new File([""], "export.json"))).toBe(false);
  });
});

describe("backup page", () => {
  it("offers the archive first, the bare export second", async () => {
    // Oracle: spec-anchored — SPEC-T-2026-CU-9130-033 UI: "With receipt photos (.zip)" and "export.json only" per snapshot.
    fakeApi();
    render(
      <I18nProvider i18n={i18n}>
        <BackupPage />
      </I18nProvider>,
    );

    const zip = await screen.findByRole("link", {
      name: "With receipt photos (.zip)",
    });
    const json = screen.getByRole("link", { name: "export.json only" });

    expect(zip.getAttribute("href")).toBe(`/api/backup/${NAME}/archive`);
    expect(json.getAttribute("href")).toBe(`/api/backup/${NAME}/download`);
  });

  it("names the restored photos", async () => {
    // Oracle: spec-anchored — the restore report carries receipt_photos = 2; the notice states it.
    fakeApi();
    const { container } = render(
      <I18nProvider i18n={i18n}>
        <BackupPage />
      </I18nProvider>,
    );
    await screen.findByRole("link", { name: "export.json only" });

    const chooser = container.querySelector('input[type="file"]');
    if (!(chooser instanceof HTMLInputElement)) {
      throw new Error("file chooser missing");
    }
    fireEvent.change(chooser, {
      target: { files: [new File(["PK"], "backup.zip")] },
    });
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));

    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toBe(
        "Restored 2 rows in 1 table. Restored 2 receipt photos.",
      ),
    );
  });
});
