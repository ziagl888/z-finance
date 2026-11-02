// @vitest-environment jsdom
/**
 * Due items (task -011, UC-ZF-004): what a row sends, the overdue marker and the keyboard flow.
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
} from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { confirmBody, rowDraft, tomorrow } from "./draft";
import type { DueItem } from "./dueApi";
import { DuePage } from "./DuePage";

/** A rent of 800.00 € planned for 2026-08-19, 31 days overdue on 2026-09-19. */
function item(overrides: Partial<DueItem> = {}): DueItem {
  return {
    id: 1,
    date: "2026-08-19",
    due_date: "2026-08-19",
    amount_cents: -80_000,
    account_id: 1,
    transfer_account_id: null,
    category_id: 7,
    text: "Test rent",
    counterparty: null,
    recurring_id: 3,
    moved: false,
    days_overdue: 31,
    long_overdue: true,
    confirm_date: "2026-08-19",
    ...overrides,
  };
}

const ACCOUNTS = [
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
];

interface Call {
  method: string;
  url: string;
  body: unknown;
}

/** A fake API: the due list is served from `due`, changes are recorded and drop the item. */
function fakeApi(due: DueItem[]) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      if (method === "GET" && url.startsWith("/api/accounts")) {
        return Response.json(ACCOUNTS);
      }
      if (method === "GET" && url === "/api/due") return Response.json(due);
      calls.push({ method, url, body });
      const id = Number(/\/api\/due\/(\d+)\//.exec(url)?.[1]);
      due.splice(
        due.findIndex((entry) => entry.id === id),
        1,
      );
      return Response.json({ id });
    }),
  );
  return calls;
}

function renderPage() {
  return render(
    <I18nProvider i18n={i18n}>
      <MemoryRouter>
        <DuePage />
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

describe("draft rules", () => {
  it("sends only what differs from the plan", () => {
    // Oracle: spec-anchored — UC-ZF-004 main 4-5: amount/date typed in the row; untouched fields stay the plan's.
    const planned = item();
    const draft = rowDraft(planned, "en");
    expect(draft).toEqual({ amount: "800.00", date: "2026-08-19" });
    expect(confirmBody(planned, draft)).toEqual({ body: {} });
    expect(confirmBody(planned, { amount: "850", date: "2026-08-21" })).toEqual(
      { body: { amount_cents: 85_000, date: "2026-08-21" } },
    );
  });

  it("sends no amount for an equal amount typed in another format", () => {
    // Oracle: spec-anchored — task AK1: 800.00 typed as "800.00", "800" or "800,0" is the planned 80,000 cents.
    const planned = item();
    const draft = rowDraft(planned, "de");
    expect(draft.amount).toBe("800,00");
    for (const amount of ["800.00", "800", "800,0", " 800,00 "]) {
      expect(confirmBody(planned, { ...draft, amount })).toEqual({
        body: {},
      });
    }
    expect(confirmBody(planned, { ...draft, amount: "800,01" })).toEqual({
      body: { amount_cents: 80_001 },
    });
  });

  it("names the field that stops the confirm", () => {
    // Oracle: spec-anchored — amount must be positive (a sign is the item's), date is required.
    const planned = item();
    const draft = rowDraft(planned, "en");
    expect(confirmBody(planned, { ...draft, amount: "0" })).toEqual({
      invalid: "amount",
    });
    expect(confirmBody(planned, { ...draft, amount: "-5" })).toEqual({
      invalid: "amount",
    });
    expect(confirmBody(planned, { ...draft, amount: "abc" })).toEqual({
      invalid: "amount",
    });
    expect(confirmBody(planned, { ...draft, date: "" })).toEqual({
      invalid: "date",
    });
  });

  it("postpones to tomorrow by default", () => {
    // Oracle: hand-computed — the day after 2026-12-31 is 2027-01-01.
    expect(tomorrow(new Date(2026, 11, 31, 23, 30))).toBe("2027-01-01");
  });
});

describe("due page", () => {
  it("marks an item over 30 days overdue in words, not one at exactly 30", async () => {
    // Oracle: spec-anchored — UC-ZF-004 AK4 / 1a: 31 days is highlighted; barrier-free: text, not colour alone.
    fakeApi([
      item(),
      item({
        id: 2,
        date: "2026-08-20",
        text: "Test thirty",
        days_overdue: 30,
        long_overdue: false,
      }),
    ]);
    renderPage();

    expect(await screen.findByText("Test rent")).not.toBeNull();
    expect(screen.getAllByText("Over 30 days overdue")).toHaveLength(1);
    expect(screen.getByText("31 days overdue")).not.toBeNull();
    expect(screen.getByText("30 days overdue")).not.toBeNull();
    expect(
      screen.getByText(/1 item is more than 30 days overdue/),
    ).not.toBeNull();
  });

  it("confirms with Enter in the amount field and goes on with the next item", async () => {
    // Oracle: spec-anchored — UC-ZF-004 main 2 (Enter) and 4 (changed amount/date): one POST, the list reloads.
    const calls = fakeApi([
      item(),
      item({ id: 2, text: "Test insurance", date: "2026-09-01" }),
    ]);
    renderPage();
    const amount = (await screen.findAllByLabelText("Amount (€)"))[0]!;

    fireEvent.change(amount, { target: { value: "850" } });
    fireEvent.keyDown(amount, { key: "Enter" });

    await waitFor(() => expect(screen.queryByText("Test rent")).toBeNull());
    expect(calls).toEqual([
      {
        method: "POST",
        url: "/api/due/1/confirm",
        body: { amount_cents: 85_000 },
      },
    ]);
    expect(screen.getByRole("status").textContent).toBe("Booked “Test rent”.");
    // Focus moved to the row that took its place, so the next Enter confirms the next item.
    await waitFor(() =>
      expect(document.activeElement?.textContent).toContain("Test insurance"),
    );
  });

  it("refuses a missing amount without calling the API", async () => {
    // Oracle: spec-anchored — the server refuses amounts <= 0; the row says so first.
    const calls = fakeApi([item()]);
    renderPage();
    const amount = await screen.findByLabelText("Amount (€)");

    fireEvent.change(amount, { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    // (The 31-days-overdue notice above the table is an alert too, so look the row's message up by text.)
    expect(
      await screen.findByText("Enter an amount greater than 0."),
    ).not.toBeNull();
    expect(calls).toEqual([]);
  });

  it("postpones to the chosen date", async () => {
    // Oracle: spec-anchored — UC-ZF-004 4a: Postpone asks for a new date and sends exactly that date.
    const calls = fakeApi([item()]);
    renderPage();
    await screen.findByText("Test rent");

    fireEvent.click(screen.getByRole("button", { name: "Postpone" }));
    fireEvent.change(screen.getByLabelText("New date"), {
      target: { value: "2026-10-01" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Postpone" }));

    await waitFor(() => expect(screen.queryByText("Test rent")).toBeNull());
    expect(calls).toEqual([
      {
        method: "POST",
        url: "/api/due/1/postpone",
        body: { date: "2026-10-01" },
      },
    ]);
  });

  it("skips only after the dialog is accepted", async () => {
    // Oracle: spec-anchored — UC-ZF-004 main 6-7: Skip drops the item; declining the question changes nothing.
    const calls = fakeApi([item()]);
    const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await screen.findByText("Test rent");

    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(ask).toHaveBeenCalledTimes(1);
    expect(calls).toEqual([]);

    ask.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));

    await waitFor(() => expect(screen.queryByText("Test rent")).toBeNull());
    expect(calls).toEqual([
      { method: "POST", url: "/api/due/1/skip", body: undefined },
    ]);
    expect(screen.getByRole("status").textContent).toBe("Skipped “Test rent”.");
    ask.mockRestore();
  });
});
