// @vitest-environment jsdom
/**
 * Month screen (task -010, UC-ZF-007): figures with comparison, area switch, drilldown, the
 * uncategorised list with "make a rule" and its hit count, invalid rules, the export link.
 * The API is a fake `fetch`; every value is synthetic.
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
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatCents } from "../../api/client";
import { activateLocale } from "../../i18n/locale";
import type { Category } from "../accounts/accountsApi";
import { currentMonth, shiftMonth } from "./monthApi";
import type { MonthBooking, MonthReport, Rule } from "./monthApi";
import { MonthPage } from "./MonthPage";

const money = (cents: number) => formatCents(cents, "en");

const CATEGORIES: Category[] = [
  {
    id: 7,
    name: "Test groceries",
    kind: "expense",
    area: null,
    parent_id: null,
    booking_count: 0,
  },
  {
    id: 8,
    name: "Test fees",
    kind: "income",
    area: null,
    parent_id: null,
    booking_count: 0,
  },
];

const REPORT: MonthReport = {
  month: "2026-08",
  area: "all",
  income: {
    amount_cents: 500_000,
    previous_cents: 400_000,
    average_6m_cents: 450_000,
  },
  expense: {
    amount_cents: -62_000,
    previous_cents: -60_000,
    average_6m_cents: -55_000,
  },
  balance: {
    amount_cents: 438_000,
    previous_cents: 340_000,
    average_6m_cents: 395_000,
  },
  movement_in_cents: 0,
  movement_out_cents: 0,
  uncategorized_count: 1,
  lines: [
    {
      flow: "income",
      category_id: 8,
      category_name: "Test fees",
      parent_name: null,
      booking_count: 1,
      comparison: {
        amount_cents: 500_000,
        previous_cents: 400_000,
        average_6m_cents: 450_000,
      },
    },
    {
      flow: "expense",
      category_id: 7,
      category_name: "Test groceries",
      parent_name: null,
      booking_count: 2,
      comparison: {
        amount_cents: -30_000,
        previous_cents: -25_000,
        average_6m_cents: -20_000,
      },
    },
    {
      flow: "expense",
      category_id: 9,
      category_name: "Test old",
      parent_name: null,
      booking_count: 0,
      comparison: {
        amount_cents: 0,
        previous_cents: 0,
        average_6m_cents: -15_000,
      },
    },
  ],
};

const UNCATEGORIZED: MonthBooking = {
  id: 41,
  date: "2026-08-12",
  amount_cents: -1_250,
  account_id: 1,
  account_name: "Test giro",
  category_id: null,
  text: "Test kiosk",
  counterparty: "Kiosk Ltd",
  memo: null,
};

const GROCERY_BOOKINGS: MonthBooking[] = [
  {
    ...UNCATEGORIZED,
    id: 51,
    amount_cents: -10_000,
    category_id: 7,
    text: "Test shop A",
    counterparty: null,
  },
  {
    ...UNCATEGORIZED,
    id: 52,
    amount_cents: -20_000,
    category_id: 7,
    text: "Test shop B",
    counterparty: null,
  },
];

interface Request {
  url: string;
  method: string;
  body?: unknown;
}

interface Api {
  requests: Request[];
  rules: Rule[];
  draftStatus: number;
}

/** Fake API: answers by URL; records every request. */
function stubApi(overrides: Partial<Api> = {}): Api {
  const api: Api = { requests: [], rules: [], draftStatus: 200, ...overrides };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      api.requests.push({ url, method, body });
      if (url === "/api/categories") return Response.json(CATEGORIES);
      if (url === "/api/rules" && method === "GET")
        return Response.json(api.rules);
      if (url === "/api/rules" && method === "POST") {
        return Response.json(
          {
            rule: { ...body, id: 1, invalid_reason: null, created_at: "x" },
            applied_count: 2,
          },
          { status: 201 },
        );
      }
      if (url.startsWith("/api/rules/") && method === "DELETE")
        return new Response(null, { status: 204 });
      if (url.startsWith("/api/rules/preview")) {
        const invalid =
          (body as { pattern?: string } | undefined)?.pattern === "(";
        return invalid
          ? Response.json(
              { detail: "The regular expression is not valid: missing )" },
              { status: 400 },
            )
          : Response.json({ count: 2, samples: [] });
      }
      if (url.startsWith("/api/rules/draft")) {
        if (api.draftStatus !== 200)
          return Response.json(
            { detail: "The booking has no text to make a rule from." },
            { status: api.draftStatus },
          );
        return Response.json({
          match_kind: "contains",
          pattern: "Test kiosk",
          category_id: 7,
          counterparty: "Kiosk Ltd",
          priority: 0,
        });
      }
      if (url.startsWith("/api/reports/month/bookings")) {
        return Response.json(
          url.includes("category_id=7") ? GROCERY_BOOKINGS : [UNCATEGORIZED],
        );
      }
      if (url.startsWith("/api/reports/month?")) return Response.json(REPORT);
      if (url === "/api/transactions/41" && method === "PUT")
        return Response.json({ id: 41 });
      return Response.json(
        { detail: `unexpected ${method} ${url}` },
        { status: 500 },
      );
    }),
  );
  return api;
}

function renderPage() {
  render(
    <I18nProvider i18n={i18n}>
      <MonthPage />
    </I18nProvider>,
  );
}

const urls = (api: Api, method = "GET") =>
  api.requests.filter((r) => r.method === method).map((r) => r.url);

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("month helpers", () => {
  it("shifts months across the year end", () => {
    // Oracle: spec-anchored — calendar arithmetic: December + 1 = January of the next year.
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -1)).toBe("2025-12");
    expect(shiftMonth("2026-08", -6)).toBe("2026-02");
    expect(currentMonth(new Date(2026, 8, 19))).toBe("2026-09");
  });
});

describe("month screen", () => {
  it("shows the figures with previous month and six-month average", async () => {
    // Oracle: spec-anchored — UC-ZF-007 step 2: per category with previous month and 6-month average.
    stubApi();
    renderPage();

    const groceries = await screen.findByRole("button", {
      name: "Test groceries",
    });
    const row = groceries.closest("tr") as HTMLElement;
    const cells = within(row)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells.slice(1)).toEqual([
      money(-30_000),
      money(-25_000),
      money(-20_000),
    ]);

    expect(
      screen.getByText(money(500_000), { selector: "strong" }),
    ).toBeTruthy();
    expect(
      screen.getByText(money(438_000), { selector: "strong" }),
    ).toBeTruthy();
    // A category that only had bookings earlier is listed, muted and not clickable.
    const old = screen.getByRole("button", {
      name: "Test old",
    }) as HTMLButtonElement;
    expect(old.disabled).toBe(true);
  });

  it("refetches the report for the chosen area", async () => {
    // Oracle: spec-anchored — UC-ZF-007 step 1: month and area (private/business/all) are chosen.
    const api = stubApi();
    renderPage();
    await screen.findByRole("button", { name: "Test groceries" });

    fireEvent.change(screen.getByLabelText("Area"), {
      target: { value: "business" },
    });

    await waitFor(() =>
      expect(
        urls(api).some(
          (url) =>
            url.startsWith("/api/reports/month?") &&
            url.includes("area=business"),
        ),
      ).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: "Next month" }));
    await waitFor(() =>
      expect(
        urls(api).some((url) =>
          url.includes(`month=${shiftMonth(currentMonth(), 1)}`),
        ),
      ).toBe(true),
    );
  });

  it("opens the bookings behind a category; their sum is the category amount", async () => {
    // Oracle: spec-anchored — UC-ZF-007 AK4: the single bookings sum to the category amount (-100 - 200 = -300).
    const api = stubApi();
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: "Test groceries" }),
    );

    const panel = await screen.findByRole("region", {
      name: "Bookings of the category",
    });
    expect(await within(panel).findByText("Test shop A")).toBeTruthy();
    expect(within(panel).getByText("Test shop B")).toBeTruthy();
    expect(within(panel).getByText(money(-30_000))).toBeTruthy();
    expect(
      urls(api).some(
        (url) => url.includes("category_id=7") && url.includes("flow=expense"),
      ),
    ).toBe(true);
    fireEvent.click(within(panel).getByRole("button", { name: "Close" }));
    expect(
      screen.queryByRole("region", { name: "Bookings of the category" }),
    ).toBeNull();
  });

  it("links the CSV export with month and area", async () => {
    // Oracle: spec-anchored — UC-ZF-007 assumption: CSV export of the month for the tax advisor.
    stubApi();
    renderPage();
    await screen.findByRole("button", { name: "Test groceries" });

    const link = screen.getByRole("link", {
      name: "Export as CSV",
    }) as HTMLAnchorElement;

    expect(link.getAttribute("href")).toBe(
      `/api/reports/month/export.csv?month=${currentMonth()}&area=all`,
    );
  });

  it("shows an API error instead of the report", async () => {
    // Oracle: spec-anchored — SPEC NF: a failing request is shown, never swallowed.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json(
          { detail: "Enter the month as YYYY-MM." },
          { status: 400 },
        ),
      ),
    );
    renderPage();

    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe(
      "Enter the month as YYYY-MM.",
    );
  });
});

describe("month screen while paging", () => {
  const first = currentMonth();
  const second = shiftMonth(first, 1);
  const third = shiftMonth(first, 2);
  const strong = { selector: "strong" };

  /** Reports of the held months wait until their resolver is called. */
  function holdReports(held: string[]) {
    stubApi();
    const base = globalThis.fetch;
    const resolvers = new Map<string, (cents: number) => void>();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const month = held.find(
          (m) =>
            url.startsWith("/api/reports/month?") && url.includes(`month=${m}`),
        );
        if (!month) return base(url, init);
        const cents = await new Promise<number>((resolve) =>
          resolvers.set(month, resolve),
        );
        const income = { ...REPORT.income, amount_cents: cents };
        return Response.json({ ...REPORT, month, income });
      }),
    );
    return resolvers;
  }

  const next = () =>
    fireEvent.click(screen.getByRole("button", { name: "Next month" }));

  it("hides the old figures and shows loading until the new month arrives", async () => {
    // Oracle: spec-anchored — task -012 AK1: figures under a month heading belong to that month.
    const resolvers = holdReports([second]);
    renderPage();
    await screen.findByText(money(500_000), strong);

    next();

    expect((await screen.findAllByText("Loading…")).length).toBeGreaterThan(0);
    expect(screen.queryByText(money(500_000), strong)).toBeNull();
    await waitFor(() => expect(resolvers.has(second)).toBe(true));
    await act(async () => resolvers.get(second)?.(777_000));
    expect(await screen.findByText(money(777_000), strong)).toBeTruthy();
    expect(screen.queryAllByText("Loading…")).toHaveLength(0);
  });

  it("drops the answer of a month that was left before it arrived", async () => {
    // Oracle: spec-anchored — task -012 AK2: a late answer of an older choice never replaces the current one.
    const resolvers = holdReports([second, third]);
    renderPage();
    await screen.findByText(money(500_000), strong);

    next();
    await waitFor(() => expect(resolvers.has(second)).toBe(true));
    next();
    await waitFor(() => expect(resolvers.has(third)).toBe(true));
    await act(async () => resolvers.get(third)?.(333_000));
    await act(async () => resolvers.get(second)?.(999_000));

    expect(await screen.findByText(money(333_000), strong)).toBeTruthy();
    expect(screen.queryByText(money(999_000), strong)).toBeNull();
  });

  it("shows no old figures when the new month fails to load", async () => {
    // Oracle: spec-anchored — task -012 AK3: a failed fetch leaves the error, not the old figures.
    stubApi();
    const base = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) =>
        url.startsWith("/api/reports/month?") && url.includes(`month=${second}`)
          ? Response.json({ detail: "Boom" }, { status: 500 })
          : base(url, init),
      ),
    );
    renderPage();
    await screen.findByText(money(500_000), strong);

    next();

    expect((await screen.findAllByRole("alert"))[0]?.textContent).toBe("Boom");
    expect(screen.queryByText(money(500_000), strong)).toBeNull();
  });
});

describe("uncategorised bookings and rules", () => {
  it("assigns a category, offers a rule with its hit count and saves it", async () => {
    // Oracle: spec-anchored — UC-ZF-007 steps 3-5: assign, offer "contains <text>", show hits, apply and report.
    const api = stubApi();
    renderPage();
    const select = await screen.findByLabelText("Category for Test kiosk");

    fireEvent.change(select, { target: { value: "7" } });

    const put = await waitFor(() => {
      const found = api.requests.find((r) => r.method === "PUT");
      expect(found).toBeTruthy();
      return found as Request;
    });
    expect(put.url).toBe("/api/transactions/41");
    expect(put.body).toEqual({
      date: "2026-08-12",
      amount_cents: -1_250,
      account_id: 1,
      category_id: 7,
      text: "Test kiosk",
      counterparty: "Kiosk Ltd",
      memo: null,
    });

    const dialog = await screen.findByRole("dialog", { name: "Make a rule" });
    expect(
      (within(dialog).getByLabelText("Text to look for") as HTMLInputElement)
        .value,
    ).toBe("Test kiosk");
    expect(
      await within(dialog).findByText("2 other uncategorised bookings match."),
    ).toBeTruthy();
    const preview = api.requests.find((r) => r.url === "/api/rules/preview");
    expect(preview?.body).toEqual({
      match_kind: "contains",
      pattern: "Test kiosk",
    });

    fireEvent.change(within(dialog).getByLabelText("Priority"), {
      target: { value: "3" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save rule" }));

    expect(
      // The saved state is a new dialog element; `dialog` above is the form that was replaced.
      await screen.findByText("Rule saved. 2 bookings categorised."),
    ).toBeTruthy();
    const post = api.requests.find(
      (r) => r.method === "POST" && r.url === "/api/rules",
    );
    expect(post?.body).toEqual({
      match_kind: "contains",
      pattern: "Test kiosk",
      category_id: 7,
      counterparty: "Kiosk Ltd",
      priority: 3,
    });
  });

  it("refuses to save a pattern the server rejects", async () => {
    // Oracle: spec-anchored — SPEC AK14: an invalid regular expression is reported before saving and cannot be saved.
    stubApi();
    renderPage();
    fireEvent.change(await screen.findByLabelText("Category for Test kiosk"), {
      target: { value: "7" },
    });
    const dialog = await screen.findByRole("dialog", { name: "Make a rule" });
    await within(dialog).findByText("2 other uncategorised bookings match.");

    fireEvent.change(within(dialog).getByLabelText("Match"), {
      target: { value: "regex" },
    });
    fireEvent.change(within(dialog).getByLabelText("Text to look for"), {
      target: { value: "(" },
    });

    const alert = await within(dialog).findByRole("alert");
    expect(alert.textContent).toBe(
      "The regular expression is not valid: missing )",
    );
    expect(
      within(dialog).queryByText("2 other uncategorised bookings match."),
    ).toBeNull();
    const save = within(dialog).getByRole("button", {
      name: "Save rule",
    }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
  });

  it("offers no rule when the booking has no text to make one from", async () => {
    // Oracle: spec-anchored — SPEC AK13: no text, no draft; the assignment itself still succeeds and reloads.
    const api = stubApi({ draftStatus: 400 });
    renderPage();
    const select = await screen.findByLabelText("Category for Test kiosk");

    fireEvent.change(select, { target: { value: "7" } });

    await waitFor(() =>
      expect(urls(api).some((url) => url.startsWith("/api/rules/draft"))).toBe(
        true,
      ),
    );
    await waitFor(() =>
      expect(urls(api, "PUT")).toEqual(["/api/transactions/41"]),
    );
    expect(screen.queryByRole("dialog", { name: "Make a rule" })).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("marks invalid rules and deletes a rule after asking", async () => {
    // Oracle: spec-anchored — SPEC AK15: an invalid rule is marked and kept until the user deletes it.
    const base = {
      match_kind: "regex" as const,
      category_id: 7,
      counterparty: null,
      priority: 0,
      created_at: "x",
    };
    const api = stubApi({
      rules: [
        { ...base, id: 1, pattern: "(a|aa)+$", invalid_reason: "timeout" },
        { ...base, id: 2, pattern: "(", invalid_reason: "syntax" },
        {
          ...base,
          id: 3,
          pattern: "ok",
          match_kind: "contains",
          invalid_reason: null,
        },
      ],
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();

    expect(await screen.findByText("Too slow, skipped")).toBeTruthy();
    expect(screen.getByText("Invalid pattern, skipped")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Delete rule (" }));

    await waitFor(() => expect(urls(api, "DELETE")).toEqual(["/api/rules/2"]));
    expect(confirm).toHaveBeenCalledOnce();
  });
});
