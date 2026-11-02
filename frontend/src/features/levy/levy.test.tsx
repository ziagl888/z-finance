// @vitest-environment jsdom
/**
 * Levy & revenue (task -023, UC-ZF-015 UI part): the year card, the warnings, the back-payment
 * row, the required-revenue split, the flagged income month, the rate change and the two
 * languages. The API is a fake `fetch`; every value is synthetic and hand-computed from the UC.
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
import type { Account } from "../accounts/accountsApi";
import { debtSeries, expenseSeries, incomeSeries } from "./chartData";
import {
  colorOfCategory,
  extendSlots,
  formatRatePercent,
  fromMonthDay,
  isRateJump,
  parseRatePercent,
  toMonthDay,
} from "./draft";
import { LevyPage } from "./LevyPage";
import type {
  ExpenseMonth,
  FreeAmount,
  IncomeMonth,
  LevyStatus,
  LevyYear,
  RequiredRevenue,
} from "./levyApi";

const ACCOUNT: Account = {
  id: 7,
  name: "Test business",
  area: "business",
  type: "giro",
  opening_balance_cents: 0,
  opening_date: "2026-01-01",
  reserve_floor_cents: 0,
  archived_at: null,
  balance_cents: 0,
  balance_date: "2026-09-19",
  booking_count: 0,
};

const YEAR: LevyYear = {
  year: 2026,
  base_cents: 2_000_000,
  target_cents: 700_000,
  prepayments_cents: 300_000,
  pot_share_cents: 400_000,
  actual_cents: 700_000,
  gap_cents: 0,
  backpayment_cents: 400_000,
  backpayment_date: "2027-07-01",
  backpayment_transaction_id: 91,
  backpayment_state: "open",
};

function status(
  changes: Partial<LevyStatus> = {},
  year: Partial<LevyYear> = {},
): LevyStatus {
  return {
    settings: {
      levy_rate_bp: 3500,
      levy_base: "income",
      due_month_day: "07-01",
      levy_pot_id: 5,
      account_id: 7,
      settings_missing: false,
    },
    levy_pot_id: 5,
    levy_pot_missing: false,
    levy_pot_ambiguous: false,
    pot_balance_cents: 400_000,
    years: [{ ...YEAR, ...year }],
    levy_gap_cents: 0,
    free_balance_cents: 1_000_000,
    levy_uncovered: false,
    backpayment_account_missing: false,
    ...changes,
  };
}

const FREE: FreeAmount = {
  as_of: "2026-09-30",
  accounts_cents: 1_500_000,
  planned_cents: -200_000,
  reserve_cents: 100_000,
  levy_gap_cents: 300_000,
  free_cents: 900_000,
};

// Costs 3,000 € at 35 %: 3,000 / 0.65 = 4,615.38 €, of which levies 1,615.38 € (UC-ZF-015 AK3).
const REQUIRED: RequiredRevenue = {
  required_cents: 461_538,
  fixed_costs_cents: 200_000,
  installments_cents: 50_000,
  payoff_budget_cents: 30_000,
  pot_rates_cents: 20_000,
  reserve_build_cents: 0,
  levy_cents: 161_538,
  levy_rate_bp: 3500,
};

const MONTHS = Array.from({ length: 24 }, (_, i) => {
  const index = 9 + i; // 2025-10 … 2027-09
  const year = 2025 + Math.floor(index / 12);
  return `${year}-${String((index % 12) + 1).padStart(2, "0")}`;
});

// Month 3 has 3,000 € income under the need of 4,615.38 € (marked), month 4 has 5,000 € (not).
const INCOME: IncomeMonth[] = MONTHS.map((month, i) => ({
  month,
  actual_cents: i === 3 ? 300_000 : i === 4 ? 500_000 : 0,
  planned_cents: 0,
  required_cents: 461_538,
  below_required: i !== 4,
}));

const EXPENSES: ExpenseMonth[] = MONTHS.map((month, i) => ({
  month,
  planned: i > 11,
  categories:
    i === 3
      ? [
          { category_id: 1, category_name: "Test rent", amount_cents: 120_000 },
          { category_id: null, category_name: null, amount_cents: 5_000 },
        ]
      : [],
  liabilities_cents: i === 3 ? 25_000 : 0,
  remaining_debt_cents: 400_000 - i * 1_000,
}));

interface Fake {
  status: LevyStatus;
  free: FreeAmount;
  required: RequiredRevenue;
  income: IncomeMonth[];
  expenses: ExpenseMonth[];
}

const calls: { method: string; url: string; body: unknown }[] = [];

function stubApi(
  fake: Partial<Fake> = {},
  onPut?: (body: unknown) => Partial<Fake>,
) {
  let current: Fake = {
    status: status(),
    free: FREE,
    required: REQUIRED,
    income: INCOME,
    expenses: EXPENSES,
    ...fake,
  };
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, url, body });
      let payload: unknown = null;
      let code = 200;
      if (method === "PUT" && url === "/api/levy/settings") {
        current = { ...current, ...onPut?.(body) };
        payload = current.status.settings;
      } else if (method === "POST" && url.includes("/archive")) {
        code = 204;
      } else if (url === "/api/levy/status") payload = current.status;
      else if (url === "/api/levy/free-amount") payload = current.free;
      else if (url === "/api/levy/required-revenue") payload = current.required;
      else if (url === "/api/levy/charts/income") payload = current.income;
      else if (url.startsWith("/api/levy/charts/expenses"))
        payload = current.expenses;
      else if (url.startsWith("/api/pots")) {
        payload = {
          pots: [
            { id: 5, name: "Test levy pot", closed: false, is_levy_pot: true },
          ],
        };
      } else if (url.startsWith("/api/accounts")) payload = [ACCOUNT];
      return new Response(code === 204 ? null : JSON.stringify(payload), {
        status: code,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

const renderPage = () =>
  render(
    <I18nProvider i18n={i18n}>
      <MemoryRouter>
        <LevyPage />
      </MemoryRouter>
    </I18nProvider>,
  );

/** Text of the KPI tile with this label. */
function kpi(label: string): string {
  const tile = screen
    .getByText(label, { selector: ".ui-kpi-label" })
    .closest(".ui-kpi");
  return tile?.textContent ?? "";
}

const count = (method: string, url: string) =>
  calls.filter((call) => call.method === method && call.url === url).length;

// The first render of a cold test run is slow, especially while other jobs share the machine.
configure({ asyncUtilTimeout: 5000 });

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("rate and date rules", () => {
  it("turns percentages into basis points without floats and refuses the rest (AK12)", () => {
    // Oracle: spec-anchored — UC-ZF-015 preconditions (rate as a percentage, e.g. 35 %) and the API
    // limit "0 to 90 %" (levy_rate_bp ≤ 9000); basis points by hand: 35,5 % = 3550.
    expect(parseRatePercent("35")).toBe(3500);
    expect(parseRatePercent("35,5")).toBe(3550);
    expect(parseRatePercent("35.25")).toBe(3525);
    expect(parseRatePercent(" 0 ")).toBe(0);
    expect(parseRatePercent("90")).toBe(9000);
    expect(parseRatePercent("90,01")).toBeNull();
    expect(parseRatePercent("91")).toBeNull();
    expect(parseRatePercent("-1")).toBeNull();
    expect(parseRatePercent("3.555")).toBeNull();
    expect(parseRatePercent("abc")).toBeNull();
    expect(parseRatePercent("")).toBeNull();
  });

  it("shows basis points as an editable percentage in the UI language", () => {
    // Oracle: spec-anchored — orchestrator note (UI locale for numbers); 3550 bp = 35,5 % in German, 35.5 in English.
    expect(formatRatePercent(3500, "en")).toBe("35");
    expect(formatRatePercent(3550, "de")).toBe("35,5");
    expect(formatRatePercent(5, "en")).toBe("0.05");
  });

  it("flags only a change of more than 10 percentage points (UC 6a)", () => {
    // Oracle: spec-anchored — UC-ZF-015 6a ("weicht stark ab (> 10 Punkte)"): 10 points is not more than 10.
    expect(isRateJump(3500, 4500)).toBe(false);
    expect(isRateJump(3500, 4501)).toBe(true);
    expect(isRateJump(4500, 3000)).toBe(true);
  });

  it("builds and reads the due date as MM-DD", () => {
    // Oracle: spec-anchored — UC-ZF-015 preconditions (default 1 July → "07-01"); the API takes MM-DD.
    expect(toMonthDay(7, 1)).toBe("07-01");
    expect(toMonthDay(12, 31)).toBe("12-31");
    expect(toMonthDay(13, 1)).toBeNull();
    expect(toMonthDay(7, 0)).toBeNull();
    expect(toMonthDay(7, 1.5)).toBeNull();
    expect(fromMonthDay("07-01")).toEqual({ month: 7, day: 1 });
    expect(fromMonthDay("nonsense")).toEqual({ month: 7, day: 1 });
  });
});

describe("chart data", () => {
  it("keeps a category's colour when a filter removes others and folds the eighth into Other", () => {
    // Oracle: spec-anchored — dataviz method ("color follows the entity, never its rank"; 7 hues, then Other).
    const month = (ids: number[]): ExpenseMonth => ({
      month: "2026-09",
      planned: false,
      categories: ids.map((id) => ({
        category_id: id,
        category_name: `Category ${id}`,
        amount_cents: 1_000 * (10 - id),
      })),
      liabilities_cents: 0,
      remaining_debt_cents: 0,
    });
    const first = extendSlots(new Map(), [month([1, 2, 3])]);
    expect([...first.entries()]).toEqual([
      ["1", 0],
      ["2", 1],
      ["3", 2],
    ]);
    // filter to area with only category 3, plus a new category 4: 3 keeps slot 2, 4 gets slot 3
    const second = extendSlots(first, [month([3, 4])]);
    expect(second.get("3")).toBe(2);
    expect(second.get("4")).toBe(3);
    // categories 5..9 fill slots 4..6, the rest folds into Other (null)
    const third = extendSlots(second, [month([5, 6, 7, 8, 9])]);
    expect([5, 6, 7].map((id) => third.get(String(id)))).toEqual([4, 5, 6]);
    expect([8, 9].map((id) => third.get(String(id)))).toEqual([null, null]);
    expect(colorOfCategory(third, 9)).toBe("var(--chart-other)");
    expect(colorOfCategory(third, 1)).toBe("var(--chart-1)");
  });

  it("maps the API months to bars, the need line and the flags", () => {
    // Oracle: spec-anchored — UC-ZF-015 step 4 + AK4 (booked and planned income as bars, need as line, months below marked).
    const chart = incomeSeries(INCOME, {
      booked: "Booked",
      planned: "Planned",
      required: "Need",
      below: "Below",
    });
    expect(chart.bars[0]?.values[3]).toBe(300_000);
    expect(chart.bars[0]?.values[4]).toBe(500_000);
    expect(chart.lines[0]?.values.every((v) => v === 461_538)).toBe(true);
    expect(chart.flags.values[3]).toBe(true);
    expect(chart.flags.values[4]).toBe(false);
  });

  it("stacks categories, sums the folded ones and keeps the debt in its own series", () => {
    // Oracle: spec-anchored — UC-ZF-015 step 5 / AK6 (expenses by category, liabilities line, remaining debt curve).
    const slots = extendSlots(new Map(), EXPENSES);
    const chart = expenseSeries(EXPENSES, slots, {
      liabilities: "Liabilities",
      other: "Other",
      uncategorised: "Uncategorised",
    });
    expect(chart.bars.map((bar) => bar.label)).toEqual([
      "Test rent",
      "Uncategorised",
    ]);
    expect(chart.bars[0]?.values[3]).toBe(120_000);
    expect(chart.bars[1]?.values[3]).toBe(5_000);
    expect(chart.lines[0]?.values[3]).toBe(25_000);
    expect(debtSeries(EXPENSES, "Debt")[0]?.values[0]).toBe(400_000);
  });
});

describe("levy page", () => {
  it("shows target, set aside and gap of the year: gap 0 when covered (AK1)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 AK1: rate 35 %, income 20,000 €, pot 4,000 €, prepayments 3,000 € → target 7,000 €, actual 7,000 €, gap 0 €.
    stubApi();
    renderPage();
    expect(
      await screen.findByRole("heading", { name: "Levy reserve 2026" }),
    ).toBeTruthy();
    expect(kpi("Business income so far")).toContain("€20,000.00");
    expect(kpi("Target")).toContain("€7,000.00");
    expect(kpi("Set aside")).toContain("€7,000.00");
    expect(kpi("Gap")).toContain("€0.00");
    expect(kpi("Gap")).toContain("Fully covered");
    expect(screen.queryByText("Levies not covered")).toBeNull();
  });

  it("shows a gap of 3,000 € when the pot holds only 1,000 € (AK1)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 AK1: with pot 1,000 € the gap is 3,000 € (actual 4,000 €).
    stubApi({
      status: status(
        { pot_balance_cents: 100_000 },
        { pot_share_cents: 100_000, actual_cents: 400_000, gap_cents: 300_000 },
      ),
    });
    renderPage();
    await screen.findByRole("heading", { name: "Levy reserve 2026" });
    expect(kpi("Set aside")).toContain("€4,000.00");
    expect(kpi("Gap")).toContain("€3,000.00");
    expect(kpi("Gap")).toContain("Target minus set aside");
  });

  it("warns 'Levies not covered' when the gap exceeds the free balance (UC 2b)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 2b (deutliche Warnung "Abgaben nicht gedeckt"); gap 5,000 € vs free 1,000 €.
    stubApi({
      status: status(
        {
          levy_uncovered: true,
          levy_gap_cents: 500_000,
          free_balance_cents: 100_000,
        },
        { gap_cents: 500_000, actual_cents: 200_000 },
      ),
    });
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Levies not covered");
    expect(alert.textContent).toContain("€5,000.00");
    expect(alert.textContent).toContain("€1,000.00");
  });

  it("hints at a missing or ambiguous levy pot (UC 2a)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 2a (kein Abgaben-Topf markiert: Ist = nur Vorauszahlungen; Hinweis, einen Topf zu markieren).
    stubApi({ status: status({ levy_pot_missing: true, levy_pot_id: null }) });
    renderPage();
    expect(await screen.findByText(/No levy pot is set/)).toBeTruthy();
    cleanup();
    stubApi({ status: status({ levy_pot_ambiguous: true }) });
    renderPage();
    expect(await screen.findByText(/Several pots are flagged/)).toBeTruthy();
  });

  it("asks for the rate while nothing is saved", async () => {
    // Oracle: spec-anchored — /api/levy/settings docs ("rate 0 while nothing was saved"); nothing is reserved then.
    stubApi({
      status: status(
        {
          settings: {
            levy_rate_bp: 0,
            levy_base: "income",
            due_month_day: "07-01",
            levy_pot_id: null,
            account_id: null,
            settings_missing: true,
          },
        },
        {},
      ),
    });
    renderPage();
    expect(await screen.findByText(/Nothing is reserved yet/)).toBeTruthy();
    expect(
      (screen.getByLabelText("Levy rate (%)") as HTMLInputElement).value,
    ).toBe("");
  });

  it("lists the back-payment of the year and archives the estimate on request (AK4)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 AK2 (target 7,000 € − prepayments 3,000 € = 4,000 € on 01.07. of the next year) and 3a (estimate archived, not deleted).
    stubApi();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    const row = (await screen.findByRole("row", {
      name: /^2026/,
    })) as HTMLElement;
    expect(within(row).getByText("€4,000.00")).toBeTruthy();
    expect(within(row).getByText("Jul 1, 2027")).toBeTruthy();
    expect(within(row).getByText("Planned")).toBeTruthy();
    fireEvent.click(
      within(row).getByRole("button", { name: "Assessment received" }),
    );
    expect(confirm).toHaveBeenCalledOnce();
    await waitFor(() =>
      expect(count("POST", "/api/levy/backpayments/2026/archive")).toBe(1),
    );
    // the page reloads afterwards
    await waitFor(() => expect(count("GET", "/api/levy/status")).toBe(2));
  });

  it("offers no archive button without a planned item and none after cancelling", async () => {
    // Oracle: spec-anchored — UC-ZF-015 3a (archive replaces the planned item; nothing planned means nothing to replace).
    stubApi({
      status: status(
        {},
        { backpayment_transaction_id: null, backpayment_cents: 0 },
      ),
    });
    renderPage();
    const row = (await screen.findByRole("row", {
      name: /^2026/,
    })) as HTMLElement;
    expect(
      within(row).queryByRole("button", { name: "Assessment received" }),
    ).toBeNull();
    cleanup();
    stubApi();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "Assessment received" }),
    );
    expect(count("POST", "/api/levy/backpayments/2026/archive")).toBe(0);
  });

  it("splits the required revenue by block and names the biggest cost block (AK5)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 AK3 (costs 2,000 + 500 + 300 + 200 at 35 % → 4,615 €/month; levy block 1,615 €) and 4a (which block drives the need).
    stubApi();
    renderPage();
    const table = await screen.findByRole("table", {
      name: "Required revenue by block",
    });
    expect(within(table).getAllByText("€4,615.38").length).toBeGreaterThan(0);
    const fixed = within(table)
      .getByText("Fixed costs")
      .closest("tr") as HTMLElement;
    expect(within(fixed).getByText("€2,000.00")).toBeTruthy();
    expect(within(fixed).getByText("Biggest cost block")).toBeTruthy();
    expect(within(fixed).getByText("43 %")).toBeTruthy();
    const levy = within(table)
      .getByText("Levies on the revenue")
      .closest("tr") as HTMLElement;
    expect(within(levy).getByText("€1,615.38")).toBeTruthy();
    expect(within(levy).queryByText("Biggest cost block")).toBeNull();
    // exactly one driver
    expect(within(table).getAllByText("Biggest cost block").length).toBe(1);
  });

  it("shows the free amount net of the levy gap and says why (core rule)", async () => {
    // Oracle: spec-anchored — SPEC-z-finance "Kernregel": free = Σ accounts + planned − reserve − levy gap; 15,000 − 2,000 − 1,000 − 3,000 = 9,000 €.
    stubApi();
    renderPage();
    const card = (
      await screen.findByRole("heading", { name: /Free until/ })
    ).closest("section") as HTMLElement;
    expect(within(card).getByText("€15,000.00")).toBeTruthy();
    expect(within(card).getByText("-€2,000.00")).toBeTruthy();
    expect(within(card).getByText("-€1,000.00")).toBeTruthy();
    expect(within(card).getByText("-€3,000.00")).toBeTruthy();
    const total = within(card)
      .getByText("Free", { selector: "th" })
      .closest("tr") as HTMLElement;
    expect(within(total).getByText("€9,000.00")).toBeTruthy();
    expect(
      within(card).getByText(/subtracted from what your accounts hold/),
    ).toBeTruthy();
  });

  it("marks only the month under the need in the income chart (AK6)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 AK4: a month with 3,000 € income at need 4,615 € is marked, a month with 5,000 € is not.
    stubApi();
    renderPage();
    const income = await screen.findByRole("figure", {
      name: "Income against required revenue",
    });
    // 2026-01 has 3,000 € (index 3, marked), 2026-02 has 5,000 € (index 4, not marked)
    expect(income.querySelector('[data-flag="2026-01"]')).not.toBeNull();
    expect(income.querySelector('[data-flag="2026-02"]')).toBeNull();
    // the table twin says it in words for the 3,000 € month
    fireEvent.click(
      within(income).getByRole("button", { name: "Show as table" }),
    );
    const row = within(income)
      .getByText("January 2026")
      .closest("tr") as HTMLElement;
    expect(within(row).getByText("€3,000.00")).toBeTruthy();
    expect(within(row).getByText("Yes")).toBeTruthy();
    const ok = within(income)
      .getByText("February 2026")
      .closest("tr") as HTMLElement;
    expect(within(ok).getByText("€5,000.00")).toBeTruthy();
    expect(within(ok).getByText("No")).toBeTruthy();
  });

  it("shows expenses, liabilities and remaining debt with the API's figures (AK9)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 AK6 (Ausgaben nach Kategorie, Verbindlichkeiten-Linie, Restschuld; Werte wie UC-ZF-007/-010); amounts are the fake API's, shown unchanged.
    stubApi();
    renderPage();
    const chart = await screen.findByRole("figure", {
      name: "Expenses and liabilities",
    });
    fireEvent.click(
      within(chart).getByRole("button", { name: "Show as table" }),
    );
    const row = within(chart)
      .getByText("January 2026")
      .closest("tr") as HTMLElement;
    expect(within(row).getByText("€1,200.00")).toBeTruthy(); // Test rent
    expect(within(row).getByText("€50.00")).toBeTruthy(); // uncategorised
    expect(within(row).getByText("€250.00")).toBeTruthy(); // liabilities
    // legend and column headers both name the categories
    expect(within(chart).getAllByText("Test rent").length).toBe(2);
    expect(within(chart).getAllByText("Uncategorised").length).toBe(2);
    const debt = screen.getByRole("figure", { name: "Remaining debt" });
    fireEvent.click(
      within(debt).getByRole("button", { name: "Show as table" }),
    );
    const first = within(debt)
      .getByText("October 2025")
      .closest("tr") as HTMLElement;
    expect(within(first).getByText("€4,000.00")).toBeTruthy();
  });

  it("reloads the expense chart for another area and keeps the page while it loads", async () => {
    // Oracle: spec-anchored — dataviz method (one filter row above the charts; refetch keeps the frame); the API takes ?area=.
    stubApi();
    renderPage();
    await screen.findByRole("figure", { name: "Expenses and liabilities" });
    expect(count("GET", "/api/levy/charts/expenses?area=all")).toBe(1);
    fireEvent.click(screen.getByLabelText("Business"));
    await waitFor(() =>
      expect(count("GET", "/api/levy/charts/expenses?area=business")).toBe(1),
    );
    expect(
      screen.getByRole("figure", { name: "Income against required revenue" }),
    ).toBeTruthy();
  });

  it("saves the changed rate and reloads status, required revenue and both charts (AK7)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 AK5/step 6: rate 35 → 40 % changes target, back-payment and need without further input; 40 % = 4000 basis points.
    stubApi({}, () => ({
      status: status(
        {
          settings: {
            levy_rate_bp: 4000,
            levy_base: "income",
            due_month_day: "07-01",
            levy_pot_id: 5,
            account_id: 7,
            settings_missing: false,
          },
        },
        { target_cents: 800_000, actual_cents: 700_000, gap_cents: 100_000 },
      ),
      required: {
        ...REQUIRED,
        required_cents: 500_000,
        levy_cents: 200_000,
        levy_rate_bp: 4000,
      },
    }));
    renderPage();
    const rate = (await screen.findByLabelText(
      "Levy rate (%)",
    )) as HTMLInputElement;
    expect(rate.value).toBe("35");
    fireEvent.change(rate, { target: { value: "40" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save and recalculate" }),
    );
    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT")).toBe(true),
    );
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/levy/settings");
    expect(put?.body).toEqual({
      levy_rate_bp: 4000,
      levy_base: "income",
      due_month_day: "07-01",
      levy_pot_id: 5,
      account_id: 7,
    });
    // everything is read again, and the new figures are on screen
    await waitFor(() => expect(kpi("Target")).toContain("€8,000.00"));
    for (const url of [
      "/api/levy/status",
      "/api/levy/free-amount",
      "/api/levy/required-revenue",
      "/api/levy/charts/income",
      "/api/levy/charts/expenses?area=all",
    ]) {
      expect(count("GET", url)).toBe(2);
    }
    expect(screen.getAllByText("€5,000.00").length).toBeGreaterThan(0);
    expect(screen.queryByText(/more than 10 percentage points/)).toBeNull();
  });

  it("refuses an invalid rate without calling the API", async () => {
    // Oracle: spec-anchored — API limit "Enter a levy rate between 0 and 90 %" (LevyIn/levy service); nothing may be sent for 95.
    stubApi();
    renderPage();
    const rate = await screen.findByLabelText("Levy rate (%)");
    fireEvent.change(rate, { target: { value: "95" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save and recalculate" }),
    );
    expect(
      await screen.findByText("Enter a rate from 0 to 90, e.g. 35 or 35,5."),
    ).toBeTruthy();
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("explains a jump of more than 10 points after saving (UC 6a)", async () => {
    // Oracle: spec-anchored — UC-ZF-015 6a: a rate that differs by > 10 points gets a hint naming the difference and the allocation rule; 35 → 50 %.
    stubApi({}, () => ({
      status: status({
        settings: {
          levy_rate_bp: 5000,
          levy_base: "income",
          due_month_day: "07-01",
          levy_pot_id: 5,
          account_id: 7,
          settings_missing: false,
        },
      }),
    }));
    renderPage();
    const rate = await screen.findByLabelText("Levy rate (%)");
    fireEvent.change(rate, { target: { value: "50" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save and recalculate" }),
    );
    const hint = await screen.findByText(/more than 10 percentage points/);
    expect(hint.textContent).toContain("from 35 % to 50 %");
    expect(hint.textContent).toContain("allocation rule");
  });

  it("shows the API's error message when saving fails", async () => {
    // Oracle: spec-anchored — orchestrator note (errors from the API are shown as they are, localized by the server).
    stubApi();
    renderPage();
    const rate = await screen.findByLabelText("Levy rate (%)");
    const original = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) =>
        init?.method === "PUT"
          ? new Response(
              JSON.stringify({ detail: "The pot Test is closed." }),
              {
                status: 409,
                headers: { "Content-Type": "application/json" },
              },
            )
          : original(url, init),
      ),
    );
    fireEvent.change(rate, { target: { value: "36" } });
    fireEvent.click(
      screen.getByRole("button", { name: "Save and recalculate" }),
    );
    expect(await screen.findByText("The pot Test is closed.")).toBeTruthy();
  });

  it("speaks German: heading, KPI labels and chart legend (AK11)", async () => {
    // Oracle: spec-anchored — UC NF6 (zweisprachige Beschriftung) and the German terms of UC-ZF-015 (Abgaben & Umsatz, Soll, Lücke).
    await act(() => activateLocale("de"));
    stubApi();
    renderPage();
    expect(
      await screen.findByRole("heading", { name: "Abgaben-Rückstellung 2026" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("heading", { name: "Abgaben & Umsatz" }),
    ).toBeTruthy();
    expect(kpi("Soll")).toContain("7.000,00");
    expect(kpi("Lücke")).toContain("0,00");
    expect(
      screen.getByRole("figure", { name: "Einnahmen gegen Umsatzbedarf" }),
    ).toBeTruthy();
    expect(screen.getAllByText("Gebuchte Einnahmen").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Unter Umsatzbedarf").length).toBeGreaterThan(0);
  });
});
