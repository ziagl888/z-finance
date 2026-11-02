// @vitest-environment jsdom
/**
 * Import assistant (task -017, UC-ZF-005 surface): the four steps against a fake API — dialect
 * check, profile mapping, refusal, preview table, corrections, rule from the preview, the
 * double-click guard and the protocol — plus the pure helpers behind them. Every value is
 * synthetic; the counts are hand-computed from the fixture below.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  act,
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import type { Account, Category } from "../accounts/accountsApi";
import { ImportPage } from "./ImportPage";
import type { Detected, PreviewLine, Profile, Run } from "./importApi";
import {
  buildOverrides,
  claimedPlanned,
  guessColumns,
  liveCounts,
  looksGarbled,
  problems,
  reconcileEdits,
  toProfileInput,
  emptyForm,
  willWrite,
  withEdit,
} from "./logic";

const ACCOUNTS: Account[] = [
  {
    id: 1,
    name: "Test giro",
    area: "private",
    type: "giro",
    opening_balance_cents: 0,
    opening_date: "2026-01-01",
    reserve_floor_cents: 0,
    archived_at: null,
    balance_cents: 0,
    balance_date: "2026-09-19",
    booking_count: 0,
  },
  {
    id: 9,
    name: "Test old account",
    area: "private",
    type: "giro",
    opening_balance_cents: 0,
    opening_date: "2020-01-01",
    reserve_floor_cents: 0,
    archived_at: "2026-01-01T00:00:00+00:00",
    balance_cents: 0,
    balance_date: "2026-09-19",
    booking_count: 0,
  },
];

const CATEGORIES: Category[] = [
  {
    id: 7,
    name: "Test food",
    kind: "expense",
    area: null,
    parent_id: null,
    booking_count: 0,
  },
  {
    id: 8,
    name: "Test salary",
    kind: "income",
    area: null,
    parent_id: null,
    booking_count: 0,
  },
];

const DETECTED: Detected = {
  encoding: "utf-8",
  delimiter: "semicolon",
  header: ["Datum", "Text", "Partner", "Betrag"],
  rows: [
    ["01.09.2026", "Test kiosk", "Kiosk Ltd", "-4,50"],
    ["03.09.2026", "Test SVS payment", "", "-300,00"],
  ],
};

const PROFILE: Profile = {
  id: 5,
  created_at: "2026-09-19T10:00:00+00:00",
  bank_name: "Test bank",
  column_map: {
    date: "Datum",
    text: "Text",
    amount: "Betrag",
    debit: null,
    credit: null,
    counterparty: "Partner",
  },
  delimiter: "auto",
  encoding: "auto",
  date_format: "%d.%m.%Y",
  decimal_sep: ",",
  skip_rows: 0,
};

const line = (
  values: Partial<PreviewLine> & Pick<PreviewLine, "line" | "kind">,
): PreviewLine => ({
  date: "2026-09-01",
  amount_cents: -450,
  text: "Test text",
  counterparty: null,
  import_hash: `hash-${values.line}`,
  planned_id: null,
  candidates: [],
  review: false,
  suspect_id: null,
  category_id: null,
  category_source: null,
  ...values,
});

/** Five lines: two new (one with a rule category), one duplicate, one match with two candidates, one suspect. */
const LINES: PreviewLine[] = [
  line({
    line: 2,
    kind: "new",
    text: "Test kiosk",
    counterparty: "Kiosk Ltd",
    amount_cents: -450,
  }),
  line({
    line: 3,
    kind: "new",
    text: "Test SVS payment",
    amount_cents: -30_000,
    category_id: 7,
    category_source: "rule",
  }),
  line({
    line: 4,
    kind: "duplicate",
    text: "Test old payment",
    amount_cents: -1_000,
  }),
  line({
    line: 5,
    kind: "match",
    text: "Test rent Sept",
    amount_cents: -85_000,
    planned_id: 101,
    candidates: [101, 102],
    review: true,
    category_id: 7,
    category_source: "planned",
  }),
  line({
    line: 6,
    kind: "duplicate_suspect",
    text: "Test coffee",
    amount_cents: -1_250,
    suspect_id: 55,
  }),
];

const previewBody = (id = "prev-1") => ({
  preview_id: id,
  account_id: 1,
  profile_id: 5,
  file_hash: "abc",
  encoding: "utf-8",
  delimiter: "semicolon",
  counts: {
    lines: 6,
    new: 2,
    matched: 1,
    duplicates: 1,
    suspects: 1,
    errors: 1,
    rule_hits: 1,
    uncategorized: 1,
  },
  lines: LINES,
  errors: [{ line: 7, message: "Test: the date is not readable" }],
});

const booking = (
  id: number,
  text: string,
  date: string,
  amount_cents: number,
) => ({
  id,
  date,
  amount_cents,
  account_id: 1,
  category_id: null,
  text,
  counterparty: null,
  memo: null,
  status: "planned",
  source: "recurring",
  transfer_group_id: null,
  transfer_account_id: null,
  recurring_id: null,
  created_at: "2026-09-01T00:00:00+00:00",
});

const RUN: Run = {
  id: 3,
  account_id: 1,
  profile_id: 5,
  file_name: "test.csv",
  file_hash: "abc",
  counts: {
    lines: 6,
    new: 2,
    matched: 1,
    duplicates: 1,
    suspects: 1,
    skipped: 1,
    errors: 1,
    uncategorized: 1,
  },
  errors: [{ line: 7, message: "Test: the date is not readable" }],
  created_at: "2026-09-19T10:00:00+00:00",
};

interface Req {
  method: string;
  path: string;
  url: string;
  body?: unknown;
  binaryBytes?: number;
}
type Handler = (request: Req) => Response | Promise<Response>;

/** Fake API: default answers for the happy path; `handlers` (key "METHOD /path") replace one route. */
function stubApi(handlers: Record<string, Handler> = {}): Req[] {
  const requests: Req[] = [];
  const defaults: Record<string, Handler> = {
    "GET /api/accounts": () => Response.json(ACCOUNTS),
    "GET /api/categories": () => Response.json(CATEGORIES),
    "GET /api/imports/profiles": () => Response.json([]),
    "GET /api/imports/runs": () => Response.json([]),
    "POST /api/imports/detect": () => Response.json(DETECTED),
    "POST /api/imports/profiles": () => Response.json(PROFILE, { status: 201 }),
    "POST /api/imports/preview": () => Response.json(previewBody()),
    "GET /api/transactions/101": () =>
      Response.json(booking(101, "Test rent", "2026-09-01", -85_000)),
    "GET /api/transactions/102": () =>
      Response.json(booking(102, "Test rent extra", "2026-09-06", -85_500)),
    "GET /api/transactions/55": () =>
      Response.json(
        booking(55, "Test coffee hand entry", "2026-09-02", -1_250),
      ),
    "POST /api/imports/commit": () => Response.json(RUN, { status: 201 }),
    "POST /api/rules/preview": () => Response.json({ count: 0, samples: [] }),
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = url.split("?")[0] ?? url;
      const method = init?.method ?? "GET";
      const raw = init?.body;
      const request: Req = {
        method,
        path,
        url,
        body: typeof raw === "string" ? JSON.parse(raw) : undefined,
        binaryBytes:
          raw instanceof ArrayBuffer || ArrayBuffer.isView(raw)
            ? raw.byteLength
            : undefined,
      };
      requests.push(request);
      const handler =
        handlers[`${method} ${path}`] ?? defaults[`${method} ${path}`];
      if (!handler)
        return Response.json(
          { detail: `unexpected ${method} ${path}` },
          { status: 500 },
        );
      return handler(request);
    }),
  );
  return requests;
}

const calls = (requests: Req[], method: string, path: string) =>
  requests.filter((r) => r.method === method && r.path === path);

const CSV =
  "Datum;Text;Partner;Betrag\n01.09.2026;Test kiosk;Kiosk Ltd;-4,50\n";

function renderPage() {
  return render(
    <I18nProvider i18n={i18n}>
      <MemoryRouter>
        <ImportPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

/** Steps 1 and 2 through the UI up to the preview table. */
async function reachPreview() {
  renderPage();
  await screen.findByRole("option", { name: "Test giro" });
  fireEvent.change(screen.getByLabelText("Account"), {
    target: { value: "1" },
  });
  fireEvent.change(screen.getByLabelText("CSV file"), {
    target: { files: [new File([CSV], "test.csv", { type: "text/csv" })] },
  });
  await screen.findByText("Test kiosk");
  fireEvent.click(
    screen.getByRole("button", { name: "Continue to the profile" }),
  );
  fireEvent.change(await screen.findByLabelText("Bank name"), {
    target: { value: "Test bank" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Show the preview" }));
  await screen.findByRole("table", { name: "Preview of the import" });
}

const rowOf = (text: string) =>
  screen.getByText(text).closest("tr") as HTMLElement;

const kpis = () =>
  [...document.querySelectorAll(".ui-kpi")].map((tile) => [
    tile.querySelector(".ui-kpi-label")?.textContent,
    tile.querySelector(".ui-kpi-value")?.textContent,
  ]);

// The multi-step flows render a whole wizard; a cold first render on a busy machine needs more than 1 s.
configure({ asyncUtilTimeout: 5000 });

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("profile form helpers", () => {
  it("proposes the columns from the header names", () => {
    // Oracle: spec-anchored — UC-ZF-005 step 2: columns are assigned per dropdown; the proposal names the obvious ones.
    expect(
      guessColumns(["Buchungstag", "Verwendungszweck", "Betrag"]),
    ).toMatchObject({
      date: "Buchungstag",
      text: "Verwendungszweck",
      amount: "Betrag",
      amountMode: "single",
    });
    expect(guessColumns(["Datum", "Text", "Soll", "Haben"])).toMatchObject({
      debit: "Soll",
      credit: "Haben",
      amountMode: "split",
    });
  });

  it("builds the API body only from a complete form", () => {
    // Oracle: spec-anchored — SPEC-012 profile: `amount` or `debit` + `credit`, counterparty optional; skip_rows 0–50.
    const form = {
      ...emptyForm(),
      bankName: " Test bank ",
      date: "Datum",
      text: "Text",
      amount: "Betrag",
    };
    expect(toProfileInput(form)).toEqual({
      bank_name: "Test bank",
      column_map: {
        date: "Datum",
        text: "Text",
        amount: "Betrag",
        debit: null,
        credit: null,
        counterparty: null,
      },
      delimiter: "auto",
      encoding: "auto",
      date_format: "%d.%m.%Y",
      decimal_sep: ",",
      skip_rows: 0,
    });
    expect(problems(emptyForm())).toEqual(["bank", "date", "text", "amount"]);
    expect(toProfileInput({ ...form, amountMode: "split" })).toBeNull();
    expect(problems({ ...form, skipRows: "51" })).toEqual(["skipRows"]);
  });

  it("recognises text that was read with the wrong encoding", () => {
    // Oracle: spec-anchored — UC-ZF-005 1a: the preview shows whether umlauts are right; "Ã¤" is ä read as Windows-1252.
    const rows = (cell: string) => ({ ...DETECTED, rows: [[cell]] });
    expect(looksGarbled(rows("Miete fÃ¼r September"))).toBe(true);
    expect(looksGarbled(rows("Miete f�r September"))).toBe(true);
    expect(looksGarbled(rows("Miete für September"))).toBe(false);
  });
});

describe("corrections of a preview", () => {
  it("imports new and matched lines, leaves duplicates and suspects out by default", () => {
    // Oracle: spec-anchored — SPEC-012 overrides: duplicates and suspects skipped, all else imported; a duplicate never.
    expect(LINES.map((l) => willWrite(l))).toEqual([
      true,
      true,
      false,
      true,
      false,
    ]);
    expect(willWrite(LINES[4] as PreviewLine, { skip: false })).toBe(true);
    expect(willWrite(LINES[2] as PreviewLine, { skip: false })).toBe(false);
  });

  it("sends only what differs from the engine's answer", () => {
    // Oracle: spec-anchored — SPEC-017 AK5: an untouched line sends no override; `planned_id: null` removes a match.
    let edits = withEdit({}, LINES[0] as PreviewLine, { categoryId: 8 });
    edits = withEdit(edits, LINES[3] as PreviewLine, { planned: null });
    edits = withEdit(edits, LINES[4] as PreviewLine, { skip: false });
    edits = withEdit(edits, LINES[1] as PreviewLine, { categoryId: 7 }); // the rule's own category: no change
    edits = withEdit(edits, LINES[1] as PreviewLine, { skip: false }); // an imported line stays imported
    expect(buildOverrides(LINES, edits)).toEqual([
      { line: 2, category_id: 8 },
      { line: 5, planned_id: null },
      { line: 6, skip: false },
    ]);
    // Changing back to the engine's answer removes the edit again.
    expect(
      buildOverrides(
        LINES,
        withEdit(edits, LINES[3] as PreviewLine, { planned: 101 }),
      ),
    ).toHaveLength(2);
  });

  it("keeps the corrections for a new preview unless the engine now says the same", () => {
    // Oracle: spec-anchored — SPEC-017 AK6: after a rule is saved the file is previewed again; corrections stay, an override the engine cannot take (`planned_id` outside the candidates) would be refused.
    const edits = {
      2: { categoryId: 8 },
      3: { categoryId: 8 },
      5: { planned: 102 },
      6: { skip: false },
    };
    const again = LINES.map((l) =>
      l.line === 2
        ? { ...l, category_id: 8, category_source: "rule" as const }
        : l.line === 5
          ? { ...l, candidates: [101] }
          : l,
    );
    expect(reconcileEdits(again, edits)).toEqual({
      3: { categoryId: 8 },
      6: { skip: false },
    });
  });

  it("counts what the commit will do", () => {
    // Oracle: spec-anchored — hand count of the fixture: lines 2, 3, 5 are written; 2 new + 1 matched; line 2 has no category.
    expect(liveCounts(LINES, {})).toEqual({
      write: 3,
      fresh: 2,
      matched: 1,
      skipped: 1,
      uncategorized: 1,
    });
    const edits = withEdit(
      withEdit({}, LINES[3] as PreviewLine, { planned: null }),
      LINES[0] as PreviewLine,
      {
        categoryId: 8,
      },
    );
    // The match removed: three new bookings, no match; the corrected category leaves nothing uncategorised.
    expect(liveCounts(LINES, edits)).toEqual({
      write: 3,
      fresh: 3,
      matched: 0,
      skipped: 1,
      uncategorized: 0,
    });
  });

  it("does not offer a planned item that another written line uses", () => {
    // Oracle: spec-anchored — SPEC-012: "each planned id once"; the engine rejects a second claim.
    const other = line({
      line: 8,
      kind: "match",
      planned_id: 102,
      candidates: [101, 102],
    });
    expect([...claimedPlanned([...LINES, other], {}, 5)]).toEqual([102]);
    expect([...claimedPlanned([...LINES, other], {}, 8)]).toEqual([101]);
  });
});

describe("step 1: file and dialect", () => {
  it("offers active accounts, sends the file untouched and shows what the engine read", async () => {
    // Oracle: spec-anchored — UC-ZF-005 step 1: the system detects delimiter and encoding; archived accounts are not bookable.
    const requests = stubApi();
    renderPage();
    await screen.findByRole("option", { name: "Test giro" });
    expect(
      screen.queryByRole("option", { name: "Test old account" }),
    ).toBeNull();
    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [new File([CSV], "test.csv", { type: "text/csv" })] },
    });
    await screen.findByText("Test kiosk");
    const detect = calls(requests, "POST", "/api/imports/detect")[0] as Req;
    expect(detect.url).toBe(
      "/api/imports/detect?encoding=auto&delimiter=auto&skip_rows=0",
    );
    expect(detect.binaryBytes).toBe(new TextEncoder().encode(CSV).byteLength);
    expect(
      screen.getByText(/Read as utf-8, separator: Semicolon/),
    ).toBeTruthy();
    // Continue needs an account.
    const next = screen.getByRole("button", {
      name: "Continue to the profile",
    }) as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Account"), {
      target: { value: "1" },
    });
    expect(next.disabled).toBe(false);
  });

  it("lets the user choose the encoding by hand and warns about garbled umlauts", async () => {
    // Oracle: spec-anchored — UC-ZF-005 1a: manual choice; the preview shows whether umlauts are right.
    const requests = stubApi({
      "POST /api/imports/detect": (request) =>
        request.url.includes("encoding=cp1252")
          ? Response.json(DETECTED)
          : Response.json({
              ...DETECTED,
              rows: [["01.09.2026", "Miete fÃ¼r Test", "", "-1,00"]],
            }),
    });
    renderPage();
    await screen.findByRole("option", { name: "Test giro" });
    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [new File([CSV], "test.csv")] },
    });
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /Umlauts or the euro sign look wrong/,
    );
    fireEvent.change(screen.getByLabelText("Encoding"), {
      target: { value: "cp1252" },
    });
    await screen.findByText("Test kiosk");
    expect(
      screen.queryByText(/Umlauts or the euro sign look wrong/),
    ).toBeNull();
    expect(
      calls(requests, "POST", "/api/imports/detect").at(-1)?.url,
    ).toContain("encoding=cp1252");
  });

  it("shows the refusal of an undetectable file and stays on the step", async () => {
    // Oracle: spec-anchored — SPEC-012: with `auto` an uncertain dialect is refused, the user chooses it.
    stubApi({
      "POST /api/imports/detect": () =>
        Response.json(
          { detail: "The column separator could not be detected." },
          { status: 400 },
        ),
    });
    renderPage();
    await screen.findByRole("option", { name: "Test giro" });
    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [new File([CSV], "test.csv")] },
    });
    expect((await screen.findByRole("alert")).textContent).toBe(
      "The column separator could not be detected.",
    );
    fireEvent.change(screen.getByLabelText("Account"), {
      target: { value: "1" },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Continue to the profile",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
});

describe("step 2: profile", () => {
  it("fills the dropdowns from the header, saves the new profile and asks for the preview with it", async () => {
    // Oracle: spec-anchored — UC-ZF-005 step 2: columns per dropdown; the profile is stored per bank (POST body per SPEC-012).
    const requests = stubApi();
    await reachPreview();
    const create = calls(requests, "POST", "/api/imports/profiles")[0] as Req;
    expect(create.body).toEqual({
      bank_name: "Test bank",
      column_map: {
        date: "Datum",
        text: "Text",
        amount: "Betrag",
        debit: null,
        credit: null,
        counterparty: "Partner",
      },
      delimiter: "auto",
      encoding: "auto",
      date_format: "%d.%m.%Y",
      decimal_sep: ",",
      skip_rows: 0,
    });
    const preview = calls(requests, "POST", "/api/imports/preview")[0] as Req;
    expect(preview.url).toBe(
      "/api/imports/preview?profile_id=5&account_id=1&file_name=test.csv",
    );
    expect(preview.binaryBytes).toBe(new TextEncoder().encode(CSV).byteLength);
  });

  it("cannot show the preview without a bank name or a mapped column", async () => {
    // Oracle: spec-anchored — SPEC-012: bank name and the date, text and amount roles are required.
    stubApi();
    renderPage();
    await screen.findByRole("option", { name: "Test giro" });
    fireEvent.change(screen.getByLabelText("Account"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [new File([CSV], "test.csv")] },
    });
    await screen.findByText("Test kiosk");
    fireEvent.click(
      screen.getByRole("button", { name: "Continue to the profile" }),
    );
    const show = (await screen.findByRole("button", {
      name: "Show the preview",
    })) as HTMLButtonElement;
    expect(show.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Bank name"), {
      target: { value: "Test bank" },
    });
    expect(show.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Amount column"), {
      target: { value: "" },
    });
    expect(show.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("Debit and credit columns"));
    fireEvent.change(screen.getByLabelText("Debit column (money out)"), {
      target: { value: "Betrag" },
    });
    fireEvent.change(screen.getByLabelText("Credit column (money in)"), {
      target: { value: "Text" },
    });
    expect(show.disabled).toBe(false);
  });

  it("shows the refusal with the missing column and commits nothing", async () => {
    // Oracle: spec-anchored — UC-ZF-005 2a / AK4: the file is refused and the missing column is named; no booking.
    const requests = stubApi({
      "POST /api/imports/preview": () =>
        Response.json(
          { detail: "The column Betrag is missing in the file." },
          { status: 400 },
        ),
    });
    renderPage();
    await screen.findByRole("option", { name: "Test giro" });
    fireEvent.change(screen.getByLabelText("Account"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [new File([CSV], "test.csv")] },
    });
    await screen.findByText("Test kiosk");
    fireEvent.click(
      screen.getByRole("button", { name: "Continue to the profile" }),
    );
    fireEvent.change(await screen.findByLabelText("Bank name"), {
      target: { value: "Test bank" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show the preview" }));
    const refusal = await screen.findByText(
      "The column Betrag is missing in the file.",
    );
    expect(refusal.getAttribute("role")).toBe("alert");
    // Still on the profile step, the form is there to fix it.
    expect(screen.getByLabelText("Amount column")).toBeTruthy();
    expect(calls(requests, "POST", "/api/imports/commit")).toHaveLength(0);
  });

  it("changes a stored profile with PUT and flags a column the file does not have", async () => {
    // Oracle: spec-anchored — UC-ZF-005 2a: "[Die Nutzerin oder der Nutzer] passt das Profil an, alte Importe bleiben unberührt" (PUT replaces the profile).
    const changed = {
      ...PROFILE,
      column_map: { ...PROFILE.column_map, amount: "Umsatz" },
    };
    const requests = stubApi({
      "GET /api/imports/profiles": () => Response.json([changed]),
      "PUT /api/imports/profiles/5": () => Response.json(PROFILE),
    });
    renderPage();
    await screen.findByRole("option", { name: "Test giro" });
    fireEvent.change(screen.getByLabelText("Account"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByLabelText("CSV file"), {
      target: { files: [new File([CSV], "test.csv")] },
    });
    await screen.findByText("Test kiosk");
    fireEvent.click(
      screen.getByRole("button", { name: "Continue to the profile" }),
    );
    fireEvent.change(await screen.findByLabelText("Profile"), {
      target: { value: "5" },
    });
    expect(
      screen.getByText("“Umsatz” is not a column of this file."),
    ).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Amount column"), {
      target: { value: "Betrag" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show the preview" }));
    await screen.findByRole("table", { name: "Preview of the import" });
    const put = calls(requests, "PUT", "/api/imports/profiles/5")[0] as Req;
    expect(
      (put.body as { column_map: { amount: string } }).column_map.amount,
    ).toBe("Betrag");
    expect(calls(requests, "POST", "/api/imports/profiles")).toHaveLength(0);
  });
});

describe("step 3: preview table", () => {
  it("shows every line with its classification, the planned item and the resembled booking", async () => {
    // Oracle: spec-anchored — UC-ZF-005 step 3: new, duplicates grey, rule hits with category, matches with the planned item beside them; hand counts of the fixture.
    stubApi();
    await reachPreview();
    const duplicate = rowOf("Test old payment");
    expect(within(duplicate).getByText("Duplicate")).toBeTruthy();
    expect(duplicate.className).toContain("import-row-out");
    expect(within(duplicate).queryByRole("checkbox")).toBeNull();
    expect(within(duplicate).queryByRole("combobox")).toBeNull();

    const kiosk = rowOf("Test kiosk");
    expect(within(kiosk).getByText("New")).toBeTruthy();
    expect(within(kiosk).getByText("Kiosk Ltd")).toBeTruthy();
    expect(within(kiosk).getByText("-€4.50")).toBeTruthy();

    const rule = rowOf("Test SVS payment");
    expect(
      (within(rule).getByLabelText("Category of line 3") as HTMLSelectElement)
        .value,
    ).toBe("7");
    expect(within(rule).getByText("From a rule")).toBeTruthy();

    const match = rowOf("Test rent Sept");
    expect(within(match).getByText("Matches planned item")).toBeTruthy();
    expect(within(match).getByText("Check the match")).toBeTruthy();
    // The planned item (fetched by id) stands beside the line: text, its date and amount.
    const planned = match.querySelector(".import-match > span") as HTMLElement;
    await waitFor(() =>
      expect(planned.textContent).toMatch(/^Test rent · .* · -€850\.00$/),
    );

    const suspect = rowOf("Test coffee");
    expect(within(suspect).getByText("Possible duplicate")).toBeTruthy();
    await waitFor(() =>
      expect(
        within(suspect).getByText(/Looks like: Test coffee hand entry/),
      ).toBeTruthy(),
    );
    expect(
      (within(suspect).getByRole("checkbox") as HTMLInputElement).checked,
    ).toBe(false);

    expect(kpis()).toEqual([
      ["New", "2"],
      ["Match a planned item", "1"],
      ["Duplicates", "1"],
      ["Possible duplicates", "1"],
      ["Without category", "1"],
      ["Lines with errors", "1"],
    ]);
    expect(screen.getByText("Test: the date is not readable")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Apply: 3 bookings" }),
    ).toBeTruthy();
  });

  it("hides the duplicates on request", async () => {
    // Oracle: spec-anchored — UC-ZF-005 step 3: duplicates are shown grey; hiding them is a display choice only.
    stubApi();
    await reachPreview();
    fireEvent.click(screen.getByLabelText("Hide duplicates"));
    expect(screen.queryByText("Test old payment")).toBeNull();
    expect(screen.getByText("Test kiosk")).toBeTruthy();
  });
});

describe("step 3: corrections and commit", () => {
  it("sends the corrections as overrides: category, removed match, imported suspect, left-out line", async () => {
    // Oracle: spec-anchored — UC-ZF-005 step 4: correct single categories, remove a wrong match; SPEC-012 overrides (skip / category_id / planned_id).
    const requests = stubApi();
    await reachPreview();
    fireEvent.change(
      within(rowOf("Test kiosk")).getByLabelText("Category of line 2"),
      { target: { value: "8" } },
    );
    fireEvent.click(
      within(rowOf("Test rent Sept")).getByRole("button", {
        name: "Remove the match of line 5",
      }),
    );
    expect(
      within(rowOf("Test rent Sept")).getByText(
        "No match: booked as a new entry.",
      ),
    ).toBeTruthy();
    fireEvent.click(within(rowOf("Test coffee")).getByRole("checkbox"));
    fireEvent.click(within(rowOf("Test SVS payment")).getByRole("checkbox"));
    // Live figures follow: written 2 (kiosk, rent as new) + suspect = 3; SVS left out.
    expect(
      screen.getByRole("button", { name: "Apply: 3 bookings" }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Apply: 3 bookings" }));
    await screen.findByText("Import finished");
    const commit = calls(requests, "POST", "/api/imports/commit")[0] as Req;
    expect(commit.body).toEqual({
      preview_id: "prev-1",
      overrides: [
        { line: 2, category_id: 8 },
        { line: 3, skip: true },
        { line: 5, planned_id: null },
        { line: 6, skip: false },
      ],
    });
  });

  it("re-hangs an ambiguous match to another candidate", async () => {
    // Oracle: spec-anchored — UC-ZF-005 3b: the user can re-hang an ambiguous match in the preview.
    const requests = stubApi();
    await reachPreview();
    const match = rowOf("Test rent Sept");
    const choice = within(match).getByLabelText(
      "Planned item for line 5",
    ) as HTMLSelectElement;
    await waitFor(() =>
      expect(within(choice).getAllByRole("option")[1]?.textContent).toMatch(
        /Test rent extra/,
      ),
    );
    fireEvent.change(choice, { target: { value: "102" } });
    fireEvent.click(screen.getByRole("button", { name: "Apply: 3 bookings" }));
    await screen.findByText("Import finished");
    const commit = calls(requests, "POST", "/api/imports/commit")[0] as Req;
    expect((commit.body as { overrides: unknown[] }).overrides).toEqual([
      { line: 5, planned_id: 102 },
    ]);
  });

  it("sends nothing but the preview id when the user changed nothing", async () => {
    // Oracle: spec-anchored — SPEC-017 AK5: a line without changes sends no override.
    const requests = stubApi();
    await reachPreview();
    fireEvent.click(screen.getByRole("button", { name: "Apply: 3 bookings" }));
    await screen.findByText("Import finished");
    expect(
      (calls(requests, "POST", "/api/imports/commit")[0] as Req).body,
    ).toEqual({
      preview_id: "prev-1",
      overrides: [],
    });
  });

  it("sends exactly one commit for a double click on Apply", async () => {
    // Oracle: spec-anchored — orchestrator brief: the second commit of one preview answers 500, so the UI must not send it.
    let finish: (response: Response) => void = () => {};
    const requests = stubApi({
      "POST /api/imports/commit": () =>
        new Promise<Response>((resolve) => (finish = resolve)),
    });
    await reachPreview();
    const apply = screen.getByRole("button", {
      name: "Apply: 3 bookings",
    }) as HTMLButtonElement;
    fireEvent.click(apply);
    fireEvent.click(apply);
    expect(apply.disabled).toBe(true);
    expect(calls(requests, "POST", "/api/imports/commit")).toHaveLength(1);
    await act(async () => finish(Response.json(RUN, { status: 201 })));
    await screen.findByText("Import finished");
    expect(calls(requests, "POST", "/api/imports/commit")).toHaveLength(1);
  });

  it("keeps the preview and the corrections when the commit fails and lets the user preview again", async () => {
    // Oracle: spec-anchored — UC-ZF-005 6a: nothing is written after an error, the file can be imported again.
    let attempt = 0;
    const requests = stubApi({
      "POST /api/imports/commit": () =>
        ++attempt === 1
          ? Response.json(
              {
                detail:
                  "Line 3 could not be imported, nothing was written: Test reason",
              },
              { status: 409 },
            )
          : Response.json(RUN, { status: 201 }),
      "POST /api/imports/preview": (request) =>
        Response.json(
          previewBody(
            calls(requests, "POST", request.path).length > 1
              ? "prev-2"
              : "prev-1",
          ),
        ),
    });
    await reachPreview();
    fireEvent.change(
      within(rowOf("Test kiosk")).getByLabelText("Category of line 2"),
      { target: { value: "8" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply: 3 bookings" }));
    expect(
      (
        await screen.findByText(/nothing was written: Test reason/)
      ).getAttribute("role"),
    ).toBe("alert");
    // Still the preview, the correction is kept, Apply is usable again.
    expect(
      (
        within(rowOf("Test kiosk")).getByLabelText(
          "Category of line 2",
        ) as HTMLSelectElement
      ).value,
    ).toBe("8");
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Apply: 3 bookings",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(false),
    );
    fireEvent.click(screen.getByRole("button", { name: "Preview again" }));
    await waitFor(() =>
      expect(calls(requests, "POST", "/api/imports/preview")).toHaveLength(2),
    );
    fireEvent.click(screen.getByRole("button", { name: "Apply: 3 bookings" }));
    await screen.findByText("Import finished");
    const commits = calls(requests, "POST", "/api/imports/commit");
    expect(commits[1]?.body).toEqual({
      preview_id: "prev-2",
      overrides: [{ line: 2, category_id: 8 }],
    });
  });
});

describe("step 3: rule from the preview", () => {
  it("offers the corrected category as a rule and previews again after it is saved", async () => {
    // Oracle: spec-anchored — UC-ZF-005 step 6 / task -017: a corrected category can be saved as a rule through the rule dialog of -010; corrections stay.
    const requests = stubApi({
      "POST /api/rules": () =>
        Response.json(
          {
            rule: {
              id: 1,
              match_kind: "contains",
              pattern: "Test kiosk",
              category_id: 8,
              counterparty: "Kiosk Ltd",
              priority: 0,
              invalid_reason: null,
              created_at: "2026-09-19T10:00:00+00:00",
            },
            applied_count: 0,
          },
          { status: 201 },
        ),
    });
    await reachPreview();
    const kiosk = rowOf("Test kiosk");
    expect(
      within(kiosk).queryByRole("button", { name: /as a rule/ }),
    ).toBeNull();
    fireEvent.change(within(kiosk).getByLabelText("Category of line 2"), {
      target: { value: "8" },
    });
    fireEvent.click(
      within(kiosk).getByRole("button", {
        name: "Save the category of line 2 as a rule",
      }),
    );

    const dialog = await screen.findByRole("dialog", { name: "Make a rule" });
    expect(
      (within(dialog).getByLabelText("Text to look for") as HTMLInputElement)
        .value,
    ).toBe("Test kiosk");
    fireEvent.click(within(dialog).getByRole("button", { name: "Save rule" }));
    await screen.findByText(/Rule saved/);
    expect((calls(requests, "POST", "/api/rules")[0] as Req).body).toEqual({
      match_kind: "contains",
      pattern: "Test kiosk",
      category_id: 8,
      counterparty: "Kiosk Ltd",
      priority: 0,
    });
    // The file is classified again so other lines pick the rule up; the correction stays.
    await waitFor(() =>
      expect(calls(requests, "POST", "/api/imports/preview")).toHaveLength(2),
    );
    expect(
      (
        within(rowOf("Test kiosk")).getByLabelText(
          "Category of line 2",
        ) as HTMLSelectElement
      ).value,
    ).toBe("8");
  });
});

describe("step 4: protocol", () => {
  it("shows the run's figures and lists the earlier runs on the first step", async () => {
    // Oracle: spec-anchored — UC-ZF-005 AK5: the protocol names new / duplicates / matched / uncategorised as the run reports them (fixture RUN: 2, 1, 1, 1).
    const requests = stubApi({
      "GET /api/imports/runs": () => Response.json([RUN]),
    });
    await reachPreview();
    fireEvent.click(screen.getByRole("button", { name: "Apply: 3 bookings" }));
    await screen.findByText("Import finished");
    expect(screen.getByText("3 bookings written, in one step.")).toBeTruthy();
    expect(kpis()).toEqual([
      ["New", "2"],
      ["Matched to planned items", "1"],
      ["Duplicates", "1"],
      ["Uncategorised", "1"],
    ]);
    expect(
      screen.getByText(
        /Left out: 1\. Possible duplicates found: 1\. Lines with errors: 1\./,
      ),
    ).toBeTruthy();
    expect(screen.getByText("Test: the date is not readable")).toBeTruthy();
    await waitFor(() =>
      expect(
        calls(requests, "GET", "/api/imports/runs").length,
      ).toBeGreaterThan(1),
    );

    fireEvent.click(
      screen.getByRole("button", { name: "Import another file" }),
    );
    const runs = await screen.findByRole("table", { name: "Earlier imports" });
    expect(within(runs).getByText("test.csv")).toBeTruthy();
    expect(within(runs).getByText("Test giro")).toBeTruthy();
  });
});
