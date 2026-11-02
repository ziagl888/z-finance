// @vitest-environment jsdom
/**
 * Payoff plan (task -021, UC-ZF-012): the order helpers, the own order by buttons and by drag and
 * drop, what the form sends, the shortfall view and the what-if slider. The API is a fake `fetch`;
 * every value is synthetic.
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
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { PayoffPage } from "./PayoffPage";
import { PlanForm } from "./PlanForm";
import { WhatIf } from "./WhatIf";
import { formatMonth, moveItem, type PayoffStatus } from "./payoffApi";

const STATUS: PayoffStatus = {
  saved: true,
  strategy: "manual",
  manual_order: [],
  budget_cents: 30_000,
  total_monthly_cents: 60_000,
  income_override_cents: null,
  availability: {
    income_cents: 300_000,
    income_months: ["2026-06", "2026-07", "2026-08"],
    income_overridden: false,
    fixed_costs_cents: 0,
    installments_cents: 30_000,
    pot_rates_cents: 0,
    levy_cents: 0,
    levy_rate_bp: 0,
    available_cents: 270_000,
  },
  remaining_total_cents: 600_000,
  has_plan: true,
  shortfall_cents: 0,
  debts: [
    {
      id: 1,
      creditor: "Test creditor A",
      remaining_cents: 200_000,
      installment_cents: 10_000,
      interval: "monthly",
      interest_rate_pct: "3",
      end_month: "2027-02",
    },
    {
      id: 2,
      creditor: "Test creditor B",
      remaining_cents: 400_000,
      installment_cents: 20_000,
      interval: "monthly",
      interest_rate_pct: "9",
      end_month: "2027-07",
    },
  ],
  months: [
    {
      month: "2026-10",
      installments_cents: 30_000,
      extra_cents: 30_000,
      extra_to: [[1, 30_000]],
      remaining_cents: 540_000,
    },
  ],
  debt_free_month: "2027-07",
  baseline_end_month: "2027-07",
  shift_months: 0,
  progress: [],
  behind: false,
  catch_up_cents: 0,
  over_budget: false,
  suggested_budget_cents: null,
  free_now_cents: 100_000,
  missing_pay_account: [],
};

function renderWithI18n(node: React.ReactNode) {
  return render(
    <I18nProvider i18n={i18n}>
      <MemoryRouter>{node}</MemoryRouter>
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

describe("helpers", () => {
  it("moves an item and formats a month", () => {
    // Oracle: spec-anchored — UC-ZF-012 4a (pull a debt to the front); month names by hand.
    expect(moveItem(["A", "B", "C"], 2, 0)).toEqual(["C", "A", "B"]);
    expect(moveItem(["A", "B"], 0, 5)).toEqual(["A", "B"]);
    expect(formatMonth("2027-07", "en")).toBe("July 2027");
  });
});

describe("plan form", () => {
  it("reorders by keyboard buttons and by drag and drop, then saves that order", async () => {
    // Oracle: spec-anchored — UC-ZF-012 4a + orchestrator note (order by dragging AND by keyboard/buttons).
    const onSave = vi.fn(async () => {});
    renderWithI18n(<PlanForm status={STATUS} onSave={onSave} />);
    const list = screen.getByRole("list", { name: "Your order" });
    const names = () =>
      within(list)
        .getAllByRole("listitem")
        .map((item) => item.querySelector(".payoff-order-name")?.textContent);
    expect(names()).toEqual(["Test creditor A", "Test creditor B"]);

    fireEvent.click(
      screen.getByRole("button", { name: "Move Test creditor B up" }),
    );
    expect(names()).toEqual(["Test creditor B", "Test creditor A"]);

    const items = within(list).getAllByRole("listitem");
    fireEvent.dragStart(items[0]!);
    fireEvent.dragOver(items[1]!);
    fireEvent.drop(items[1]!);
    expect(names()).toEqual(["Test creditor A", "Test creditor B"]);

    fireEvent.click(
      screen.getByRole("button", { name: "Move Test creditor A down" }),
    );
    fireEvent.change(screen.getByLabelText("Extra per month"), {
      target: { value: "400" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({
      strategy: "manual",
      budget_cents: 40_000,
      manual_order: [2, 1],
      income_override_cents: null,
    });
  });

  it("refuses a negative budget without saving", () => {
    // Oracle: spec-anchored — AK15 (budget in cents, never negative; the API answers 422).
    const onSave = vi.fn(async () => {});
    renderWithI18n(<PlanForm status={STATUS} onSave={onSave} />);
    fireEvent.change(screen.getByLabelText("Extra per month"), {
      target: { value: "-5" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save plan" }));
    expect(screen.getByRole("alert").textContent).toMatch(/0 or more/);
    expect(onSave).not.toHaveBeenCalled();
  });
});

describe("payoff page", () => {
  it("shows the shortfall with links instead of a plan", async () => {
    // Oracle: spec-anchored — UC-ZF-012 2a / AK3 (no plan, shortfall per month, links to Month and Debts).
    const status: PayoffStatus = {
      ...STATUS,
      has_plan: false,
      shortfall_cents: 80_000,
      months: [],
      debt_free_month: null,
      availability: { ...STATUS.availability, available_cents: -80_000 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json(status)),
    );
    renderWithI18n(<PayoffPage />);
    const heading = await screen.findByRole("heading", {
      name: "No plan possible",
    });
    const card = heading.closest(".payoff-shortfall") as HTMLElement;
    expect(card.textContent).toMatch(/exceed your income by €800\.00 a month/);
    expect(
      within(card).getByRole("link", { name: "Month" }).getAttribute("href"),
    ).toBe("/month");
    expect(
      within(card).getByRole("link", { name: "Debts" }).getAttribute("href"),
    ).toBe("/debts");
    expect(screen.queryByRole("slider")).toBeNull();
  });
});

describe("what if", () => {
  it("asks for the plan with 100 € more and shows the new month", async () => {
    // Oracle: spec-anchored — UC-ZF-012 AK5 (+100 € changes the date; only a GET, nothing saved).
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, method: init?.method ?? "GET" });
        return Response.json({
          budget_cents: 40_000,
          total_monthly_cents: 70_000,
          has_plan: true,
          debt_free_month: "2027-06",
          ends: { "1": "2027-01", "2": "2027-06" },
        });
      }),
    );
    renderWithI18n(<WhatIf budgetCents={30_000} debtFreeMonth="2027-07" />);
    fireEvent.change(screen.getByRole("slider"), {
      target: { value: "10000" },
    });

    expect(
      await screen.findByText("Debt-free: June 2027 instead of July 2027"),
    ).not.toBeNull();
    expect(calls).toEqual([
      { url: "/api/payoff/what-if?delta_cents=10000", method: "GET" },
    ]);
  });

  it("says the month plainly when the extra money does not move it", async () => {
    // Oracle: spec-anchored — UC-ZF-012 AK5: the answer is the date; "July 2027 instead of
    // July 2027" states a change that did not happen.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          budget_cents: 35_000,
          total_monthly_cents: 65_000,
          has_plan: true,
          debt_free_month: "2027-07",
          ends: { "1": "2027-07" },
        }),
      ),
    );
    renderWithI18n(<WhatIf budgetCents={30_000} debtFreeMonth="2027-07" />);
    fireEvent.change(screen.getByRole("slider"), { target: { value: "5000" } });

    expect(await screen.findByText("Debt-free: July 2027")).not.toBeNull();
    expect(screen.queryByText(/instead of/)).toBeNull();
  });
});
