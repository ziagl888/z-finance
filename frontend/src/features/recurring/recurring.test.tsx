// @vitest-environment jsdom
/**
 * Recurring items (task -009, UC-ZF-003): the form's draft rules and what the form sends or
 * refuses. The API is a fake `fetch`; every value is synthetic.
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
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import type { Account, Category } from "../accounts/accountsApi";
import { emptyDraft, toInput } from "./draft";
import type { PlannedItem, Recurring } from "./recurringApi";
import { RecurringForm } from "./RecurringForm";
import { RecurringPage } from "./RecurringPage";

const ACCOUNTS: Account[] = [1, 2].map((id) => ({
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

const CATEGORIES: Category[] = [
  {
    id: 7,
    name: "Test rent",
    kind: "expense",
    area: null,
    parent_id: null,
    booking_count: 0,
  },
];

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("draft rules", () => {
  it("builds the API body: expense negative, transfer positive, day or weekday", () => {
    // Oracle: spec-anchored — UC-ZF-003 step 1 (text, amount, account, category, interval, day, start, end); hand-computed cents.
    const draft = {
      ...emptyDraft("2026-09-19", "1"),
      description: " Test rent ",
      amount: "800,00",
      categoryId: "7",
      dayOfMonth: "31",
    };
    expect(toInput(draft)).toEqual({
      input: {
        description: "Test rent",
        amount_cents: -80_000,
        account_id: 1,
        category_id: 7,
        transfer_target_account_id: null,
        interval: "monthly",
        day_of_month: 31,
        weekday: null,
        start_date: "2026-09-19",
        end_date: null,
      },
    });
    const transfer = toInput({
      ...draft,
      kind: "transfer",
      categoryId: "",
      toAccountId: "2",
      interval: "weekly",
      weekday: "4",
    });
    expect("input" in transfer && transfer.input).toMatchObject({
      amount_cents: 80_000,
      transfer_target_account_id: 2,
      category_id: null,
      day_of_month: null,
      weekday: 4,
    });
  });

  it("names the first field that stops the save", () => {
    // Oracle: spec-anchored — UC-ZF-003 step 1 required fields, 1a day range, 1b end not before start.
    const draft = {
      ...emptyDraft("2026-09-19", "1"),
      description: "Test rent",
      amount: "800",
      categoryId: "7",
    };
    expect(toInput({ ...draft, description: " " })).toEqual({
      invalid: "description",
    });
    expect(toInput({ ...draft, amount: "0" })).toEqual({ invalid: "amount" });
    expect(toInput({ ...draft, categoryId: "" })).toEqual({
      invalid: "category",
    });
    expect(toInput({ ...draft, dayOfMonth: "32" })).toEqual({ invalid: "day" });
    expect(toInput({ ...draft, endDate: "2026-09-18" })).toEqual({
      invalid: "end",
    });
    expect(toInput({ ...draft, kind: "transfer", toAccountId: "1" })).toEqual({
      invalid: "account",
    });
  });

  it("starts a new item on today's day of the month", () => {
    // Oracle: spec-anchored — form default: start today, monthly on today's day (2026-09-19 is a Saturday = weekday 5).
    const draft = emptyDraft("2026-09-19", "1");
    expect([draft.interval, draft.dayOfMonth, draft.weekday]).toEqual([
      "monthly",
      "19",
      "5",
    ]);
  });
});

describe("recurring form", () => {
  it("posts the item and refuses an end before the start", async () => {
    // Oracle: spec-anchored — UC-ZF-003 step 1-2 and 1b: a valid item is sent once, an invalid one never.
    const bodies: unknown[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        bodies.push(JSON.parse(init?.body as string));
        return Response.json({ id: 3 }, { status: 201 });
      }),
    );
    const onSaved = vi.fn(async () => {});
    render(
      <I18nProvider i18n={i18n}>
        <RecurringForm
          accounts={ACCOUNTS}
          categories={CATEGORIES}
          editing={null}
          onSaved={onSaved}
          onCancelEdit={vi.fn()}
        />
      </I18nProvider>,
    );
    const field = (label: string) => screen.getByLabelText(label);
    fireEvent.change(field("Text"), { target: { value: "Test rent" } });
    fireEvent.change(field("Amount (€)"), { target: { value: "800" } });
    fireEvent.change(field("Category"), { target: { value: "7" } });
    fireEvent.change(field("Day of the month"), { target: { value: "31" } });
    // Days 29-31 explain the month-end rule (UC 1a) before saving.
    expect(
      screen.queryByText(/falls on the last day of the month/),
    ).not.toBeNull();
    fireEvent.change(field("Starts on"), { target: { value: "2026-10-01" } });
    fireEvent.change(field("Ends on (optional)"), {
      target: { value: "2026-09-30" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "The end date must not be before the start date.",
    );
    expect(bodies).toEqual([]);

    fireEvent.change(field("Ends on (optional)"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    expect(bodies).toEqual([
      expect.objectContaining({
        amount_cents: -80_000,
        day_of_month: 31,
        start_date: "2026-10-01",
        end_date: null,
      }),
    ]);
  });
});

describe("recurring list", () => {
  /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-032 AK6 (Enter selects like a click). */
  it("selects a row with the keyboard", async () => {
    const rent: Recurring = {
      id: 5,
      description: "Test flat rent",
      amount_cents: -80_000,
      account_id: 1,
      category_id: 7,
      transfer_target_account_id: null,
      interval: "monthly",
      day_of_month: 1,
      weekday: null,
      start_date: "2026-10-01",
      end_date: null,
      active: true,
      next_due_date: "2026-10-01",
      planned_count: 12,
      created_at: "2026-09-19T08:00:00",
    };
    const occurrence: PlannedItem = {
      id: 51,
      date: "2026-10-01",
      due_date: "2026-10-01",
      amount_cents: -80_000,
      account_id: 1,
      status: "planned",
      moved: false,
      transfer_group_id: null,
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.startsWith("/api/accounts")) return Response.json(ACCOUNTS);
        if (url === "/api/categories") return Response.json(CATEGORIES);
        if (url === "/api/recurring/expected") return Response.json([]);
        if (url === "/api/recurring/5/items") {
          return Response.json([occurrence]);
        }
        if (url === "/api/recurring") return Response.json([rent]);
        return Response.json({ detail: url }, { status: 404 });
      }),
    );
    render(
      <I18nProvider i18n={i18n}>
        <MemoryRouter>
          <RecurringPage />
        </MemoryRouter>
      </I18nProvider>,
    );
    const cell = await screen.findByRole("cell", { name: /^Test flat rent/ });
    const row = cell.closest("tr");
    if (!row) throw new Error("no row for Test flat rent");
    expect(row.tabIndex).toBe(0);
    expect(screen.queryByRole("heading", { name: /Items of/ })).toBeNull();

    row.focus();
    fireEvent.keyDown(row, { key: "Enter" });
    expect(
      await screen.findByRole("heading", { name: "Items of “Test flat rent”" }),
    ).not.toBeNull();
    expect(row.className).toContain("rec-row-selected");
  });
});
