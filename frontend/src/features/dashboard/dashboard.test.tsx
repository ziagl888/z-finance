// @vitest-environment jsdom
/**
 * Dashboard (task -026, UC NF5 / SPEC AK8): the three figures, the sub line of the core rule, the
 * hints, the 7-day highlight and the links into the features. The API is a fake `fetch`; every
 * value is synthetic and hand-computed from the use cases.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { miniCurveLines } from "./chartData";
import { DashboardPage } from "./DashboardPage";
import { feature } from "./route";
import type { CurveDay, Dashboard } from "./dashboardApi";

/** 90 days of a flat curve of 12,000 € with 3,000 € reserved for levies (the core rule figure). */
const CURVE: CurveDay[] = Array.from({ length: 90 }, (_, day) => ({
  date: new Date(Date.UTC(2026, 8, 19 + day)).toISOString().slice(0, 10),
  balance_cents: 1_200_000,
  available_cents: 900_000,
}));

/** The state of UC NF5: 12,000 € free of which 3,000 € are the levy share of a booked income. */
function dashboard(overrides: Partial<Dashboard> = {}): Dashboard {
  return {
    today: "2026-09-19",
    month_end: "2026-09-30",
    free: {
      as_of: "2026-09-30",
      accounts_cents: 1_500_000,
      planned_cents: 0,
      reserve_cents: 0,
      levy_gap_cents: 300_000,
      free_cents: 1_200_000,
    },
    debt: {
      total_cents: 80_000,
      previous_month_cents: 100_000,
      trend_cents: -20_000,
    },
    overdue_count: 1,
    due_items: [
      {
        id: 4,
        date: "2026-09-01",
        amount_cents: -10_000,
        text: "Test rent",
        account_id: 1,
        category_id: null,
        days_overdue: 18,
        long_overdue: false,
      },
      {
        id: 5,
        date: "2026-09-25",
        amount_cents: -20_000,
        text: "Test subscription",
        account_id: 1,
        category_id: null,
        days_overdue: 0,
        long_overdue: false,
      },
    ],
    balances: {
      private_cents: 100_000,
      business_cents: 1_400_000,
      total_cents: 1_500_000,
    },
    month: {
      month: "2026-09",
      income_cents: 200_000,
      expense_cents: -50_000,
      balance_cents: 150_000,
      uncategorized_count: 0,
      top_lines: [
        {
          flow: "income",
          category_id: 1,
          category_name: "Test fees",
          amount_cents: 200_000,
        },
        {
          flow: "expense",
          category_id: 2,
          category_name: "Test rent",
          amount_cents: -50_000,
        },
      ],
    },
    mini_curve: CURVE,
    proposals: [
      {
        id: 9,
        date: "2026-09-10",
        text: "Test client fee",
        amount_cents: 400_000,
        bookable_cents: 80_000,
        rule_name: "Test business income",
        days_open: 9,
        overdue: true,
      },
    ],
    receipts_review_count: 2,
    hints: { levies_not_covered: false, tax_plan_missing: true },
    ...overrides,
  };
}

function fakeApi(data: Dashboard): { calls: string[] } {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      calls.push(url);
      return Response.json(data);
    }),
  );
  return { calls };
}

function renderPage() {
  return render(
    <I18nProvider i18n={i18n}>
      <MemoryRouter>
        <DashboardPage />
      </MemoryRouter>
    </I18nProvider>,
  );
}

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the three figures", () => {
  it("shows free until the end of the month with the levy share beside it", async () => {
    // Oracle: spec-anchored — UC NF5 / SPEC AK8: the one figure with "of which reserved for levies".
    const api = fakeApi(dashboard());
    renderPage();

    await screen.findByText("€12,000.00");
    expect(api.calls).toEqual(["/api/dashboard"]);
    expect(screen.getByText(/Free until/)).toBeTruthy();
    expect(
      screen.getByText(/of which reserved for levies: €3,000.00/),
    ).toBeTruthy();
  });

  it("names the debt trend and the overdue items", async () => {
    // Oracle: spec-anchored — UC NF5: remaining debt with the trend to the previous month, overdue count.
    fakeApi(dashboard());
    renderPage();

    await screen.findByText("€800.00");
    expect(
      screen.getByText(/€200\.00 less than at the end of last month/),
    ).toBeTruthy();
    expect(screen.getByText(/2 items due in the next 14 days/)).toBeTruthy();
    const overdue = screen.getByText("Overdue items").parentElement;
    expect(overdue?.querySelector(".ui-kpi-value")?.textContent).toBe("1");
  });
});

describe("the cards", () => {
  it("lists the due items, marks the overdue one and links to the screen", async () => {
    // Oracle: spec-anchored — SPEC AK8 (due items of the next 14 days) + UC-ZF-004 (overdue marker).
    fakeApi(dashboard());
    renderPage();

    const card = (await screen.findByText("Due in the next 14 days"))
      .parentElement as HTMLElement;
    const rows = within(card).getAllByRole("row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0] as HTMLElement).getByText("overdue")).toBeTruthy();
    expect(
      within(card).getByRole("link", { name: "Go to Due items" }),
    ).toBeTruthy();
  });

  it("highlights an allocation proposal open for seven days or more", async () => {
    // Oracle: spec-anchored — UC-ZF-014 4b / AK5: the dashboard highlights it, nothing is booked.
    fakeApi(dashboard());
    renderPage();

    const card = (await screen.findByText("Allocate"))
      .parentElement as HTMLElement;
    expect(
      within(card).getByText("1 proposal has been waiting for 7 days or more."),
    ).toBeTruthy();
    expect(within(card).getByText("9 days")).toBeTruthy();
    expect(
      within(card).getByRole("link", { name: "Go to Allocation" }),
    ).toBeTruthy();
  });

  it("counts the receipts to check and shows the month and the balances", async () => {
    // Oracle: spec-anchored — UC-ZF-013 step 7 (counter) + SPEC AK8 (month by category, balances).
    fakeApi(dashboard());
    renderPage();

    const receipts = (await screen.findByText("Receipts to check"))
      .parentElement as HTMLElement;
    expect(within(receipts).getByText("2")).toBeTruthy();
    expect(screen.getByText("September 2026 so far")).toBeTruthy();
    expect(screen.getByText("Test fees")).toBeTruthy();
    const balances = screen.getByText("Account balances")
      .parentElement as HTMLElement;
    expect(within(balances).getByText("€15,000.00")).toBeTruthy();
  });

  it("draws the 90-day curve with both lines", async () => {
    // Oracle: spec-anchored — SPEC AK8 ("Mini-Kurve 90 Tage"), drawn by the shared chart component.
    fakeApi(dashboard());
    renderPage();

    await screen.findByText("The next 90 days");
    const lines = miniCurveLines(CURVE, {
      balance: "Balance",
      available: "Available after levies",
    });
    expect(lines.map((line) => line.key)).toEqual(["balance", "available"]);
    expect(lines[1]?.values).toHaveLength(90);
    expect(screen.getByRole("link", { name: "Go to Forecast" })).toBeTruthy();
  });
});

describe("the hints", () => {
  it("warns that the levies are not covered and that no tax plan is stored", async () => {
    // Oracle: spec-anchored — UC-ZF-015 2b ("Abgaben nicht gedeckt") and UC-ZF-008 1a.
    fakeApi(
      dashboard({
        hints: { levies_not_covered: true, tax_plan_missing: true },
      }),
    );
    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/Levies not covered/)).toBeTruthy(),
    );
    expect(screen.getByText(/Taxes and levies are not set/)).toBeTruthy();
  });

  it("stays silent when both are in order", async () => {
    // Oracle: spec-anchored — a hint without its condition would be noise (UC NF5: three figures).
    fakeApi(
      dashboard({
        hints: { levies_not_covered: false, tax_plan_missing: false },
      }),
    );
    renderPage();

    await screen.findByText("€12,000.00");
    expect(screen.queryByText(/Levies not covered/)).toBeNull();
    expect(screen.queryByText(/Taxes and levies are not set/)).toBeNull();
  });
});

describe("the route", () => {
  it("claims the start page", () => {
    // Oracle: spec-anchored — task contract: the dashboard replaces the previous start page.
    expect(feature.path).toBe("");
    expect(feature.nav?.group).toBe("capture");
  });
});
