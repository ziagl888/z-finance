// @vitest-environment jsdom
/**
 * Debts (task -013, UC-ZF-010): the form's draft rules, what the form sends, and the capped special
 * payment notice. The API is a fake `fetch`; every value is synthetic.
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
import type { Account } from "../accounts/accountsApi";
import { DebtDetail } from "./DebtDetail";
import { DebtForm } from "./DebtForm";
import { DebtsPage } from "./DebtsPage";
import type { Debt } from "./debtsApi";
import { emptyDraft, toInput } from "./draft";

const ACCOUNTS: Account[] = [1].map((id) => ({
  id,
  name: `Test account ${id}`,
  area: "private",
  type: "giro",
  opening_balance_cents: 0,
  opening_date: "2026-01-01",
  reserve_floor_cents: 0,
  archived_at: null,
  balance_cents: 0,
  balance_date: "2026-09-19",
  booking_count: 0,
}));

const DEBT: Debt = {
  id: 4,
  creditor: "Test bank",
  area: "private",
  total_amount_cents: 500_000,
  installment_cents: 25_000,
  interval: "monthly",
  first_due: "2026-10-01",
  pay_account_id: 1,
  interest_rate_pct: null,
  contract_end: null,
  target_end: null,
  account_id: 9,
  recurring_id: 2,
  closed: false,
  closed_at: null,
  remaining_cents: 400_000,
  paid_cents: 100_000,
  plan: {
    count: 16,
    next_due: "2026-10-01",
    end: "2028-01-01",
    last_installment_cents: 25_000,
  },
  required_monthly_cents: null,
};

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("draft rules", () => {
  it("builds the API body with and without a fixed instalment", () => {
    // Oracle: spec-anchored — UC-ZF-010 AK1 (5,000 €, 1,000 € repaid, 250 € monthly from 2026-10-01) and 1a; cents by hand.
    const draft = {
      ...emptyDraft("1"),
      creditor: " Test bank ",
      total: "5000",
      repaid: "1000",
      installment: "250,00",
      firstDue: "2026-10-01",
      interestRate: "4,5",
    };
    expect(toInput(draft)).toEqual({
      input: {
        creditor: "Test bank",
        area: "private",
        total_amount_cents: 500_000,
        repaid_cents: 100_000,
        installment_cents: 25_000,
        interval: "monthly",
        first_due: "2026-10-01",
        pay_account_id: 1,
        interest_rate_pct: "4,5",
        contract_end: null,
        target_end: null,
      },
    });
    const free = toInput({
      ...draft,
      hasInstallment: false,
      targetEnd: "2027-09-30",
    });
    expect("input" in free && free.input).toMatchObject({
      installment_cents: null,
      interval: null,
      first_due: null,
      target_end: "2027-09-30",
    });
  });

  it("names the first field that stops the save", () => {
    // Oracle: spec-anchored — UC-ZF-010 step 1: creditor, total, repaid below total, instalment with due date and account.
    const draft = {
      ...emptyDraft("1"),
      creditor: "Test bank",
      total: "5000",
      installment: "250",
      firstDue: "2026-10-01",
    };
    expect(toInput({ ...draft, creditor: " " })).toEqual({
      invalid: "creditor",
    });
    expect(toInput({ ...draft, total: "0" })).toEqual({ invalid: "total" });
    expect(toInput({ ...draft, repaid: "5000" })).toEqual({
      invalid: "repaid",
    });
    expect(toInput({ ...draft, installment: "" })).toEqual({
      invalid: "installment",
    });
    expect(toInput({ ...draft, firstDue: "" })).toEqual({
      invalid: "firstDue",
    });
    expect(toInput({ ...draft, payAccountId: "" })).toEqual({
      invalid: "payAccount",
    });
    expect(toInput({ ...draft, interestRate: "abc" })).toEqual({
      invalid: "interestRate",
    });
  });
});

describe("debt form", () => {
  it("posts a new debt once and nothing while a field is invalid", async () => {
    // Oracle: spec-anchored — UC-ZF-010 steps 1-2: a valid debt is sent once with the repaid amount, an invalid one never.
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        bodies.push(JSON.parse(init?.body as string));
        return Response.json(DEBT, { status: 201 });
      }),
    );
    const onSaved = vi.fn(async () => {});
    render(
      <I18nProvider i18n={i18n}>
        <DebtForm
          accounts={ACCOUNTS}
          editing={null}
          onSaved={onSaved}
          onCancelEdit={vi.fn()}
        />
      </I18nProvider>,
    );
    const field = (label: string) => screen.getByLabelText(label);
    fireEvent.change(field("Creditor"), { target: { value: "Test bank" } });
    fireEvent.change(field("Total amount"), { target: { value: "5000" } });
    fireEvent.change(field("Already repaid"), { target: { value: "1000" } });
    fireEvent.change(field("Instalment"), { target: { value: "250" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Choose the first due date.",
    );
    expect(bodies).toEqual([]);

    fireEvent.change(field("First due date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(bodies).toEqual([
      expect.objectContaining({
        total_amount_cents: 500_000,
        repaid_cents: 100_000,
        installment_cents: 25_000,
        first_due: "2026-10-01",
        pay_account_id: 1,
      }),
    ]);
  });
});

describe("debt detail", () => {
  it("says so when a special payment was capped at the remaining amount", async () => {
    // Oracle: spec-anchored — UC-ZF-010 5a: 5,000 € against 4,000 € remaining books 4,000 € and warns.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url.endsWith("/history")
          ? Response.json([])
          : Response.json(
              {
                debt: { ...DEBT, closed: true, remaining_cents: 0, plan: null },
                booked_cents: 400_000,
                capped: true,
              },
              { status: 201 },
            ),
      ),
    );
    const onChanged = vi.fn(async () => {});
    render(
      <I18nProvider i18n={i18n}>
        <DebtDetail
          debt={DEBT}
          accounts={ACCOUNTS}
          allAccounts={ACCOUNTS}
          onChanged={onChanged}
        />
      </I18nProvider>,
    );
    expect(screen.getByText(/16 instalments left/)).not.toBeNull();
    fireEvent.change(screen.getByLabelText("Amount"), {
      target: { value: "5000" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Book special payment" }),
    );
    expect((await screen.findByRole("status")).textContent).toMatch(
      /more than the remaining amount/,
    );
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it("does not send a special payment without a pay account", async () => {
    // Oracle: spec-anchored — task AK3: no pay account loaded means no request and a disabled button.
    const fetchMock = vi.fn(async () => Response.json([]));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <I18nProvider i18n={i18n}>
        <DebtDetail
          debt={{ ...DEBT, pay_account_id: null }}
          accounts={[]}
          allAccounts={ACCOUNTS}
          onChanged={vi.fn(async () => {})}
        />
      </I18nProvider>,
    );
    fireEvent.change(screen.getByLabelText("Amount"), {
      target: { value: "500" },
    });
    const button = screen.getByRole("button", {
      name: "Book special payment",
    });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /Choose an account/,
    );
    const paid = (
      fetchMock.mock.calls as unknown as [string, RequestInit?][]
    ).filter(([, init]) => init?.method === "POST");
    expect(paid).toHaveLength(0);
  });
});

describe("debt list", () => {
  it("selects a row with the keyboard", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-032 AK6 (Enter selects like a click). */
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/accounts")) return Response.json(ACCOUNTS);
        if (url.endsWith("/history")) return Response.json([]);
        if (url.endsWith("/settings")) {
          return Response.json({ payoff_budget_cents: 0 });
        }
        return Response.json({
          debts: [DEBT],
          remaining_by_area: { private: 400_000, business: 0 },
          remaining_total_cents: 400_000,
        });
      }),
    );
    render(
      <I18nProvider i18n={i18n}>
        <DebtsPage />
      </I18nProvider>,
    );
    const row = (
      await screen.findByRole("button", { name: "Test bank" })
    ).closest("tr") as HTMLTableRowElement;
    expect(row.tabIndex).toBe(0);
    expect(screen.queryByRole("region", { name: "Test bank" })).toBeNull();

    row.focus();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(
      await screen.findByRole("region", { name: "Test bank" }),
    ).not.toBeNull();
  });
});
