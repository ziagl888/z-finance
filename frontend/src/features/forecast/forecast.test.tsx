// @vitest-environment jsdom
/**
 * Forecast screen (task -024, UC-ZF-006 UI part): the figures, the levy rule (AK7), view and
 * horizon, the items of a clicked or keyboard-chosen day (AK4), what-if items that are stored
 * nowhere (AK3), the adoption as an expected item, the emergency proposal and the German UI.
 * The API is a fake `fetch`; every value is synthetic and hand-computed from the UC.
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
import { dayCategories, forecastSeries } from "./chartData";
import {
  canAdopt,
  emptyWhatIf,
  inView,
  MAX_WHAT_IF_ITEMS,
  nextDay,
  paymentAccounts,
  viewOf,
  whatIfItem,
  whatIfRows,
} from "./draft";
import { ForecastPage } from "./ForecastPage";
import type {
  Emergency,
  Forecast,
  ForecastDay,
  ForecastItem,
  WhatIfItem,
} from "./forecastApi";

const START = "2026-09-19";

const account = (
  id: number,
  name: string,
  area: Account["area"],
  type: Account["type"],
): Account => ({
  id,
  name,
  area,
  type,
  opening_balance_cents: 0,
  opening_date: "2026-01-01",
  reserve_floor_cents: 0,
  archived_at: null,
  balance_cents: 0,
  balance_date: START,
  booking_count: 0,
});

const ACCOUNTS = [
  account(7, "Test giro", "private", "giro"),
  account(8, "Test business", "business", "giro"),
  account(9, "Test loan", "private", "liability"),
];

/** The date `months` after START (the 19th), as the API's horizon end. */
function endOf(months: number): string {
  const index = 8 + months; // September = month index 8
  const year = 2026 + Math.floor(index / 12);
  return `${year}-${String((index % 12) + 1).padStart(2, "0")}-19`;
}

interface Scenario {
  opening: number;
  /** Signed cents acting on a day (the planned items). */
  deltas: Record<string, number>;
  /** Open levy gap, subtracted on every day (the API's line "available after levies"). */
  gap: number;
  reserve: number;
  overdue: number;
  emergency: Emergency | null;
}

/** UC-ZF-006 AK1: balance 2,000 €, reserve 1,000 €, −1,500 € on the 10th, +3,000 € on the 20th. */
const AK1: Scenario = {
  opening: 200_000,
  deltas: { "2026-10-10": -150_000, "2026-10-20": 300_000 },
  gap: 0,
  reserve: 100_000,
  overdue: 0,
  emergency: null,
};

/** UC-ZF-006 AK7: balance 12,000 €, open levy gap 3,000 €, reserve 10,000 €. */
const AK7: Scenario = {
  opening: 1_200_000,
  deltas: {},
  gap: 300_000,
  reserve: 1_000_000,
  overdue: 0,
  emergency: null,
};

const POT_PROPOSAL: Emergency = {
  shortfall_cents: 50_000,
  proposals: [
    {
      account_id: 7,
      amount_cents: 50_000,
      lines: [
        {
          pot_id: 3,
          name: "Test holiday pot",
          rank: 1,
          amount_cents: 50_000,
          new_balance_cents: 150_000,
          target_date: "2027-06-01",
          shifted_target_date: "2027-07-01",
        },
      ],
      covered_cents: 50_000,
      shortfall_cents: 0,
    },
  ],
  covered_cents: 50_000,
  uncovered_cents: 0,
};

const RENT: ForecastItem = {
  transaction_id: 41,
  kind: "planned",
  effective_date: "2026-10-10",
  date: "2026-10-10",
  account_id: 7,
  amount_cents: -150_000,
  text: "Test rent",
  transfer_group_id: null,
  overdue: false,
};

/** The curve the way the API describes it (an independent mini-model; the assertions are by hand). */
function build(
  scenario: Scenario,
  horizon: number,
  extra: WhatIfItem[] = [],
): Forecast {
  const end = endOf(horizon);
  const days: ForecastDay[] = [];
  let balance = scenario.opening;
  for (
    let at = new Date(`${START}T00:00:00Z`);
    ;
    at = new Date(at.getTime() + 86_400_000)
  ) {
    const date = at.toISOString().slice(0, 10);
    balance += scenario.deltas[date] ?? 0;
    balance += extra
      .filter((item) => item.date === date)
      .reduce((sum, item) => sum + item.amount_cents, 0);
    days.push({
      date,
      balance_cents: balance,
      available_cents: balance - scenario.gap,
    });
    if (date === end) break;
  }
  const low = days.reduce((a, b) =>
    b.available_cents < a.available_cents ? b : a,
  );
  const breach =
    scenario.reserve > 0
      ? (days.find((day) => day.available_cents < scenario.reserve)?.date ??
        null)
      : null;
  const planned = [
    ...Object.entries(scenario.deltas).map(([, cents]) => cents),
    ...extra.map((item) => item.amount_cents),
  ];
  return {
    scope: "total",
    account_id: null,
    area: null,
    horizon_months: horizon,
    start: START,
    end,
    days,
    minimum_date: low.date,
    minimum_cents: low.available_cents,
    reserve_cents: scenario.reserve,
    reserve_missing: scenario.reserve <= 0,
    first_breach_date: breach,
    shortfall_cents: breach ? scenario.reserve - low.available_cents : 0,
    planned_income_cents: planned
      .filter((c) => c > 0)
      .reduce((a, b) => a + b, 0),
    planned_expense_cents: planned
      .filter((c) => c < 0)
      .reduce((a, b) => a + b, 0),
    overdue_count: scenario.overdue,
    levy_applies: scenario.gap > 0,
    levy_gap_cents: scenario.gap,
  };
}

const calls: { method: string; url: string; body: unknown }[] = [];

function stubApi(
  scenario: Scenario,
  items: Record<string, ForecastItem[]> = { "2026-10-10": [RENT] },
) {
  const state = { ...scenario };
  calls.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, url, body });
      const horizon = Number(/horizon=(\d+)/.exec(url)?.[1] ?? 12);
      let payload: unknown = null;
      let code = 200;
      if (url.startsWith("/api/accounts")) payload = ACCOUNTS;
      else if (method === "POST" && url === "/api/forecast/what-if")
        payload = build(state, body.horizon, body.items);
      else if (url.startsWith("/api/forecast/items")) {
        payload = items[/date=([\d-]+)/.exec(url)?.[1] ?? ""] ?? [];
      } else if (url.startsWith("/api/forecast/emergency"))
        payload = state.emergency;
      else if (url.startsWith("/api/forecast")) payload = build(state, horizon);
      else if (method === "POST" && url === "/api/pots/emergency") {
        state.emergency = null;
        payload = [];
      } else if (method === "POST" && url === "/api/recurring/expected") {
        code = 201;
        payload = { id: 90, category_id: null, counterparty: null, ...body };
      }
      return new Response(JSON.stringify(payload), {
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
        <ForecastPage />
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

const writes = () => calls.filter((call) => call.method !== "GET");

/** The chart's plot surface, with a box wide enough that clientX = 10 × (day index + 0.5). */
async function plot(days: number): Promise<HTMLElement> {
  const surface = await screen.findByRole("application");
  surface.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: days * 10, height: 100 }) as DOMRect;
  return surface;
}

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

describe("chart data", () => {
  it("ticks each 1st of a month, the year under the first tick and every January", () => {
    // Oracle: spec-anchored — UC-ZF-006 step 2 (daily curve) + dataviz method (sparse axis labels, year on the first tick and in January); dates by hand.
    const days = build(AK1, 12).days;
    const categories = dayCategories(days, "en");
    // 2026-09-19 is 12 days before 1 October: too close, so the first day gets no tick.
    expect(categories[0]).toMatchObject({ key: "2026-09-19", tick: "" });
    const ticks = categories.filter((c) => c.tick !== "");
    expect(ticks.map((c) => c.tick)).toEqual([
      "Oct",
      "Nov",
      "Dec",
      "Jan",
      "Feb",
      "Mar",
      "Apr",
      "May",
      "Jun",
      "Jul",
      "Aug",
      "Sep",
    ]);
    expect(
      ticks.filter((c) => c.tickSub).map((c) => [c.key, c.tickSub]),
    ).toEqual([
      ["2026-10-01", "2026"],
      ["2027-01-01", "2027"],
    ]);
    expect(categories).toHaveLength(366);
    // a start early in the month has room for a tick of its own
    const early = dayCategories(
      [{ date: "2026-09-05", balance_cents: 0, available_cents: 0 }],
      "en",
    );
    expect(early[0]).toMatchObject({ tick: "Sep", tickSub: "2026" });
  });

  it("draws the line after levies at 9,000 € and flags the breach although the balance is above the reserve (AK7)", () => {
    // Oracle: spec-anchored — UC-ZF-006 AK7: balance 12,000 €, levy gap 3,000 €, reserve 10,000 € → line 9,000 €, reserve breached.
    const forecast = build(AK7, 3);
    const chart = forecastSeries(forecast, {
      balance: "Balance",
      available: "Available after levies",
      reserve: "Reserve",
      below: "Below the reserve",
      lowest: "Lowest",
    });
    const values = (key: string) =>
      chart.lines.find((l) => l.key === key)?.values;
    expect(values("balance")?.[0]).toBe(1_200_000);
    expect(values("available")?.[0]).toBe(900_000);
    expect(values("reserve")?.every((v) => v === 1_000_000)).toBe(true);
    expect(chart.lines.find((l) => l.key === "reserve")?.dashed).toBe(true);
    expect(chart.flags?.values.every(Boolean)).toBe(true);
    expect(chart.marks).toEqual([
      { index: 0, lineKey: "available", label: "Lowest" },
    ]);
  });

  it("draws no reserve line and no flags without a reserve, and marks the lowest day (AK1)", () => {
    // Oracle: spec-anchored — UC-ZF-006 3a (no reserve → no alarm) and AK1 (lowest 500 € on the 10th: index 21 from 2026-09-19).
    const noReserve = forecastSeries(build({ ...AK1, reserve: 0 }, 3), {
      balance: "B",
      available: "A",
      reserve: "R",
      below: "X",
      lowest: "L",
    });
    expect(noReserve.lines.map((l) => l.key)).toEqual(["balance", "available"]);
    expect(noReserve.flags).toBeUndefined();
    const withReserve = forecastSeries(build(AK1, 3), {
      balance: "B",
      available: "A",
      reserve: "R",
      below: "X",
      lowest: "L",
    });
    expect(withReserve.marks[0]?.index).toBe(21);
    // flagged: 2026-10-10 up to and including 2026-10-19 (10 days below 1,000 €)
    expect(withReserve.flags?.values.filter(Boolean)).toHaveLength(10);
    expect(withReserve.flags?.values[21]).toBe(true);
    expect(withReserve.flags?.values[20]).toBe(false);
    expect(withReserve.flags?.values[31]).toBe(false);
  });
});

describe("draft rules", () => {
  it("reads the selector value as a view and keeps liabilities out of every view (UC 2c)", () => {
    // Oracle: spec-anchored — UC-ZF-006 step 1 (view: account / area / total) and 2c (liability accounts are hidden in the liquidity view).
    expect(viewOf("total")).toEqual({ accountId: null, area: null });
    expect(viewOf("area:business")).toEqual({
      accountId: null,
      area: "business",
    });
    expect(viewOf("account:7")).toEqual({ accountId: 7, area: null });
    expect(paymentAccounts(ACCOUNTS).map((a) => a.id)).toEqual([7, 8]);
    expect(inView(ACCOUNTS[0], viewOf("area:business"))).toBe(false);
    expect(inView(ACCOUNTS[1], viewOf("area:business"))).toBe(true);
    expect(inView(ACCOUNTS[2], viewOf("total"))).toBe(false);
    expect(inView(ACCOUNTS[0], viewOf("account:8"))).toBe(false);
  });

  it("signs the what-if amount by direction and refuses 0, past days, no account and a 51st item", () => {
    // Oracle: spec-anchored — UC-ZF-006 1a (amount, date, account) and the API rules (amount ≠ 0, date ≥ today, at most 50 items).
    const base = { ...emptyWhatIf("2026-10-01", "7"), amount: "12,50" };
    expect(whatIfItem(base, START, 0)).toEqual({
      item: {
        date: "2026-10-01",
        amount_cents: -1250,
        account_id: 7,
        text: "",
      },
    });
    expect(
      whatIfItem(
        { ...base, direction: "income", text: " Test bonus " },
        START,
        0,
      ),
    ).toEqual({
      item: {
        date: "2026-10-01",
        amount_cents: 1250,
        account_id: 7,
        text: "Test bonus",
      },
    });
    // a typed minus always means an expense
    expect(
      whatIfItem({ ...base, direction: "income", amount: "-3" }, START, 0),
    ).toMatchObject({
      item: { amount_cents: -300 },
    });
    expect(whatIfItem({ ...base, amount: "0" }, START, 0)).toEqual({
      invalid: "amount",
    });
    expect(whatIfItem({ ...base, amount: "abc" }, START, 0)).toEqual({
      invalid: "amount",
    });
    expect(whatIfItem({ ...base, date: "2026-09-18" }, START, 0)).toEqual({
      invalid: "date",
    });
    expect(whatIfItem({ ...base, date: START }, START, 0)).toMatchObject({
      item: { date: START },
    });
    expect(whatIfItem({ ...base, accountId: "" }, START, 0)).toEqual({
      invalid: "account",
    });
    expect(whatIfItem(base, START, MAX_WHAT_IF_ITEMS)).toEqual({
      invalid: "limit",
    });
  });

  it("adopts only items after today and lists the what-if rows of the day inside the view", () => {
    // Oracle: spec-anchored — UC-ZF-006 1a (expected item) + API rule "date after today"; AK4 (rows of exactly that day).
    const item = (date: string, accountId: number): WhatIfItem => ({
      date,
      amount_cents: -100,
      account_id: accountId,
      text: "",
    });
    expect(canAdopt(item(START, 7), START)).toBe(false);
    expect(canAdopt(item("2026-09-20", 7), START)).toBe(true);
    const rows = whatIfRows(
      [item("2026-10-12", 7), item("2026-10-12", 8), item("2026-10-13", 7)],
      "2026-10-12",
      ACCOUNTS,
      viewOf("account:7"),
    );
    expect(rows.map((r) => [r.account_id, r.kind])).toEqual([[7, "what_if"]]);
  });

  it("finds the next calendar day across month and year ends", () => {
    // Oracle: spec-anchored — calendar (31 December → 1 January; 28 February 2026 is not a leap day).
    expect(nextDay("2026-12-31")).toBe("2027-01-01");
    expect(nextDay("2026-02-28")).toBe("2026-03-01");
    expect(nextDay("2028-02-28")).toBe("2028-02-29");
  });
});

describe("figures", () => {
  it("shows the lowest point, the first day under the reserve and the planned sums (AK1)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 AK1: minimum 500 € on the 10th, breach from the 10th; step 3 sums: +3,000 € / −1,500 €.
    stubApi(AK1);
    renderPage();
    await screen.findByText("Liquidity forecast");
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    expect(kpi("Lowest point")).toContain("Oct 10, 2026");
    expect(kpi("First day under the reserve")).toContain("Oct 10, 2026");
    expect(kpi("Planned income")).toContain("€3,000.00");
    expect(kpi("Planned expenses")).toContain("-€1,500.00");
    expect(screen.getByRole("alert").textContent).toContain(
      "Below your reserve from Oct 10, 2026",
    );
    expect(screen.getByText("No overdue items.")).toBeTruthy();
    // the dashed reserve line and the ringed lowest point are drawn
    expect(document.querySelector('[data-line="reserve"]')).not.toBeNull();
    expect(document.querySelector("[data-mark]")).not.toBeNull();
  });

  it("counts overdue items and links to Due items (UC 2b)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 2b: overdue items are counted and linked to UC-ZF-004 ("Fällige Posten").
    stubApi({ ...AK1, overdue: 2 });
    renderPage();
    expect(await screen.findByText(/2 overdue items are counted/)).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Open due items" }).getAttribute("href"),
    ).toBe("/due");
  });

  it("states the core rule and shows the breach although the balance is above the reserve (AK7)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 AK7 (12,000 € balance, 3,000 € gap, 10,000 € reserve → 9,000 € and breached) + step 3.
    stubApi(AK7);
    renderPage();
    await screen.findByText("Liquidity forecast");
    expect(
      screen.getByText(
        /The lowest point and the reserve check refer to Available after levies, not to the balance/,
      ),
    ).toBeTruthy();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€9,000.00"));
    expect(screen.getByRole("alert").textContent).toContain(
      "Below your reserve from Sep 19, 2026",
    );
    expect(screen.getByRole("alert").textContent).toContain("€1,000.00");
    expect(screen.getByText("Open levy gap today: €3,000.00")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("€12,000.00").length).toBeGreaterThan(0);
    expect(within(table).getAllByText("€9,000.00").length).toBeGreaterThan(0);
    expect(within(table).getAllByText("€10,000.00").length).toBeGreaterThan(0);
  });

  it("shows a hint instead of an alarm when no reserve is defined (UC 3a)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 3a: "Reserve nicht gesetzt: Kein Alarm, Hinweis 'keine Reserve definiert'".
    stubApi({ ...AK1, reserve: 0 });
    renderPage();
    await screen.findByText("Liquidity forecast");
    expect(
      await screen.findByText(/No reserve defined, so there is no alarm/),
    ).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(document.querySelector('[data-line="reserve"]')).toBeNull();
    expect(kpi("First day under the reserve")).toContain("—");
  });
});

describe("view and horizon", () => {
  it("asks the API with the chosen horizon and view and does not offer liability accounts (AK4 of the task)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 step 1 (horizon 3/6/12, view account/area/total), 2c (no liability views); API query names from forecast/api.py.
    stubApi(AK1);
    renderPage();
    await screen.findByText("Liquidity forecast");
    const last = () =>
      calls.filter((c) => c.url.startsWith("/api/forecast?")).at(-1)?.url;
    expect(last()).toBe("/api/forecast?horizon=12");

    fireEvent.click(screen.getByLabelText("6 months"));
    await waitFor(() => expect(last()).toBe("/api/forecast?horizon=6"));

    fireEvent.change(screen.getByLabelText("View"), {
      target: { value: "area:business" },
    });
    await waitFor(() =>
      expect(last()).toBe("/api/forecast?area=business&horizon=6"),
    );

    const select = screen.getByLabelText("View") as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "All accounts",
      "Private accounts",
      "Business accounts",
      "Test giro",
      "Test business",
    ]);
    fireEvent.change(select, { target: { value: "account:7" } });
    await waitFor(() =>
      expect(last()).toBe("/api/forecast?account_id=7&horizon=6"),
    );
    expect(
      calls.filter((c) => c.url.startsWith("/api/forecast/emergency")).at(-1)
        ?.url,
    ).toBe("/api/forecast/emergency?account_id=7&horizon=6");
  });
});

describe("items of a day", () => {
  it("lists exactly the items of the clicked day (AK4)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 AK4: a click on a curve point shows exactly the items with that due date; rent −1,500 € on 2026-10-10 (index 21 of 366 days).
    stubApi(AK1);
    renderPage();
    const surface = await plot(366);
    fireEvent.click(surface, { clientX: 215 });
    const table = await screen.findByRole("table", {
      name: "Items on the chosen day",
    });
    expect(
      calls.some((c) => c.url === "/api/forecast/items?date=2026-10-10"),
    ).toBe(true);
    expect(
      screen.getByRole("heading", { name: "Items on Oct 10, 2026" }),
    ).toBeTruthy();
    expect(within(table).getByText("Test rent")).toBeTruthy();
    expect(within(table).getByText("Test giro")).toBeTruthy();
    expect(within(table).getByText("Planned")).toBeTruthy();
    // header + one row + the total of the day
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getAllByText("-€1,500.00")).toHaveLength(2);
    expect(
      screen.getByText(/balance €500\.00, available after levies €500\.00/),
    ).toBeTruthy();
    // a day without items says so
    fireEvent.click(surface, { clientX: 235 });
    expect(
      await screen.findByText("Nothing is planned or booked for this day."),
    ).toBeTruthy();
  });

  it("chooses a day with the arrow keys and Enter (Shift jumps a week)", async () => {
    // Oracle: spec-anchored — task -024 brief "Tag per Pfeiltasten wählbar" + WCAG 2.1.1 (keyboard); 3 × 7 days = index 21 = 2026-10-10.
    stubApi(AK1);
    renderPage();
    const surface = await plot(366);
    fireEvent.focus(surface);
    for (let i = 0; i < 3; i += 1) {
      fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    }
    // moving does not ask the server; only Enter does
    expect(calls.some((c) => c.url.startsWith("/api/forecast/items"))).toBe(
      false,
    );
    fireEvent.keyDown(surface, { key: "Enter" });
    expect(
      await screen.findByRole("heading", { name: "Items on Oct 10, 2026" }),
    ).toBeTruthy();
    expect(await screen.findByText("Test rent")).toBeTruthy();
  });
});

describe("what if", () => {
  async function addItem(amount: string, date: string, text: string) {
    fireEvent.change(screen.getByLabelText("Amount (EUR)"), {
      target: { value: amount },
    });
    fireEvent.change(screen.getByLabelText("Date"), {
      target: { value: date },
    });
    fireEvent.change(screen.getByLabelText("Text (optional)"), {
      target: { value: text },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add to forecast" }));
  }

  it("redraws the curve, stores nothing and is gone after a reload (AK3)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 AK3 + 1a: an expense of 1,200 € on the 12th lowers 500 € (10th) to −700 € (12th–19th); after a reload the item is gone and nothing was written except the calculation.
    stubApi(AK1);
    renderPage();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    await addItem("1200", "2026-10-12", "Test purchase");
    await waitFor(() => expect(kpi("Lowest point")).toContain("-€700.00"));
    expect(kpi("Lowest point")).toContain("Oct 12, 2026");
    expect(kpi("Planned expenses")).toContain("-€2,700.00");
    expect(screen.getByRole("list", { name: "What-if items" })).toBeTruthy();
    // the only request that is not a read is the what-if calculation
    expect(writes().map((c) => `${c.method} ${c.url}`)).toEqual([
      "POST /api/forecast/what-if",
    ]);
    expect(writes()[0]?.body).toEqual({
      account_id: null,
      area: null,
      horizon: 12,
      items: [
        {
          date: "2026-10-12",
          amount_cents: -120_000,
          account_id: 7,
          text: "Test purchase",
        },
      ],
    });

    // a reload = a fresh page: the saved plan again, no what-if items, no write
    cleanup();
    calls.length = 0;
    renderPage();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    expect(screen.queryByRole("list", { name: "What-if items" })).toBeNull();
    expect(writes()).toEqual([]);
  });

  it("shows a what-if item marked in the day list and removes it again", async () => {
    // Oracle: spec-anchored — UC-ZF-006 1a + step 4: the items of a day include the hypothetical one, marked "What if"; removing it restores the curve.
    stubApi(AK1);
    renderPage();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    await addItem("1200", "2026-10-12", "Test purchase");
    await waitFor(() => expect(kpi("Lowest point")).toContain("-€700.00"));
    const surface = await plot(366);
    fireEvent.click(surface, { clientX: 235 });
    const day = await screen.findByRole("table", {
      name: "Items on the chosen day",
    });
    expect(within(day).getByText("Test purchase")).toBeTruthy();
    expect(within(day).getByText("What if")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Remove Test purchase from the what-if items",
      }),
    );
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    expect(screen.queryByRole("list", { name: "What-if items" })).toBeNull();
  });

  it("refuses an empty amount and a date in the past with a message", async () => {
    // Oracle: spec-anchored — API rules of /what-if (amount ≠ 0, date today or later; forecast/service.py _check_items); no request is sent.
    stubApi(AK1);
    renderPage();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    fireEvent.click(screen.getByRole("button", { name: "Add to forecast" }));
    expect(screen.getByText("Enter an amount other than 0.")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Amount (EUR)"), {
      target: { value: "5" },
    });
    fireEvent.change(screen.getByLabelText("Date"), {
      target: { value: "2026-09-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add to forecast" }));
    expect(screen.getByText("The date must be today or later.")).toBeTruthy();
    expect(calls.some((c) => c.url === "/api/forecast/what-if")).toBe(false);
  });

  it("turns a what-if item into an expected item and leaves the list (AK3 adoption)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 1a: "nicht gespeichert, solange [die Nutzerin oder der Nutzer] ihn nicht ausdrücklich als erwarteten Posten übernimmt"; POST /api/recurring/expected body from recurring/api.py ExpectedIn.
    stubApi(AK1);
    renderPage();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    await addItem("1200", "2026-10-12", "Test purchase");
    await screen.findByRole("list", { name: "What-if items" });
    expect(writes().map((c) => c.url)).toEqual(["/api/forecast/what-if"]);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Add Test purchase as an expected item",
      }),
    );
    expect(
      await screen.findByText(
        "Added as an expected item. It is now part of your saved plan.",
      ),
    ).toBeTruthy();
    const stored = writes().find((c) => c.url === "/api/recurring/expected");
    expect(stored?.body).toEqual({
      date: "2026-10-12",
      amount_cents: -120_000,
      account_id: 7,
      category_id: null,
      text: "Test purchase",
      counterparty: null,
    });
    await waitFor(() =>
      expect(screen.queryByRole("list", { name: "What-if items" })).toBeNull(),
    );
  });

  it("does not offer to store an item dated today", async () => {
    // Oracle: spec-anchored — recurring/api.py ExpectedIn: "the date must be after today"; the what-if API accepts today.
    stubApi(AK1);
    renderPage();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    await addItem("10", START, "Test today");
    await screen.findByRole("list", { name: "What-if items" });
    const button = screen.getByRole("button", {
      name: "Add Test today as an expected item",
    });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(button.getAttribute("title")).toBe(
      "An expected item needs a date after today.",
    );
  });
});

describe("emergency proposal", () => {
  it("offers the pots in order and takes them with one click (UC 3b)", async () => {
    // Oracle: spec-anchored — UC-ZF-006 3b: with the breach the pots that would cover the shortfall are shown and can be taken with one click; body from pots/api.py EmergencyIn.
    stubApi({ ...AK1, emergency: POT_PROPOSAL });
    renderPage();
    expect(
      await screen.findByRole("heading", {
        name: "Cover the shortfall from your pots",
      }),
    ).toBeTruthy();
    expect(
      screen.getByText(/falls short of your reserve by €500\.00/),
    ).toBeTruthy();
    expect(screen.getByText("1. Test holiday pot")).toBeTruthy();
    expect(screen.getByText("Jun 1, 2027 → Jul 1, 2027")).toBeTruthy();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Take €500.00 from these pots (Test giro)",
      }),
    );
    expect(
      await screen.findByText(
        "Taken from the pots. The forecast is recalculated.",
      ),
    ).toBeTruthy();
    expect(writes().find((c) => c.url === "/api/pots/emergency")?.body).toEqual(
      {
        account_id: 7,
        lines: [{ pot_id: 3, amount_cents: 50_000 }],
      },
    );
    // reloaded: the fake now answers "no proposal" and the panel is gone
    await waitFor(() =>
      expect(
        screen.queryByRole("heading", {
          name: "Cover the shortfall from your pots",
        }),
      ).toBeNull(),
    );
  });

  it("offers nothing without a breach and says so when no pot can cover it", async () => {
    // Oracle: spec-anchored — UC-ZF-006 3b (only on a breach; locked pots never proposed → nothing coverable is a valid answer).
    stubApi({ ...AK1, reserve: 40_000 });
    const first = renderPage();
    await waitFor(() => expect(kpi("Lowest point")).toContain("€500.00"));
    expect(
      screen.queryByRole("heading", {
        name: "Cover the shortfall from your pots",
      }),
    ).toBeNull();
    first.unmount();

    stubApi({
      ...AK1,
      emergency: {
        shortfall_cents: 50_000,
        proposals: [],
        covered_cents: 0,
        uncovered_cents: 50_000,
      },
    });
    renderPage();
    expect(await screen.findByText(/No pot can cover it/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Take/ })).toBeNull();
  });
});

describe("German", () => {
  it("speaks German and formats dates and amounts in German", async () => {
    // Oracle: spec-anchored — UC NF6 (UI in German and English); the amounts are those of AK1.
    await act(() => activateLocale("de"));
    stubApi(AK1);
    renderPage();
    await screen.findByRole("heading", { name: "Vorschau" });
    await waitFor(() => expect(kpi("Tiefster Punkt")).toContain("500,00"));
    expect(kpi("Tiefster Punkt")).toContain("10.10.2026");
    expect(screen.getByLabelText("3 Monate")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Zur Vorschau hinzufügen" }),
    ).toBeTruthy();
  });
});
