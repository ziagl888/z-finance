// @vitest-environment jsdom
/**
 * Savings pots screen (task -018, UC-ZF-011): the figures on the cards, the guard message at the
 * field, the emergency dialog, the drag order and the locked-pot confirmation. The API is a fake
 * `fetch` that records its calls; every value is synthetic.
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
import type { Account } from "../accounts/accountsApi";
import { emptyDraft, orderedPots, reorder, sumLines, toInput } from "./draft";
import { OrderList } from "./OrderList";
import { PotsPage } from "./PotsPage";
import type { AccountSplit, Overview, Pot } from "./potsApi";

const account = (id: number, name: string, type: Account["type"]): Account => ({
  id,
  name,
  area: "private",
  type,
  opening_balance_cents: 0,
  opening_date: "2026-01-01",
  reserve_floor_cents: 0,
  archived_at: null,
  balance_cents: 0,
  balance_date: "2026-09-19",
  booking_count: 0,
});
const ACCOUNTS = [
  account(1, "Test savings", "savings"),
  account(2, "Test current", "giro"),
];

const pot = (over: Partial<Pot>): Pot => ({
  id: 1,
  name: "Test pot",
  account_id: 1,
  area: "private",
  target_amount_cents: null,
  target_date: null,
  plan_expense: false,
  locked: false,
  is_levy_pot: false,
  emergency_rank: 1,
  plan_start: "2026-09-01",
  planned_transaction_id: null,
  closed: false,
  closed_at: null,
  balance_cents: 0,
  progress_pct: null,
  monthly_rate_cents: null,
  status: null,
  expected_balance_cents: null,
  savings_plan_id: null,
  savings_plan_amount_cents: null,
  expected_reach: null,
  ...over,
});

const split = (over: Partial<AccountSplit> = {}): AccountSplit => ({
  account_id: 1,
  balance_cents: 400_000,
  pots_cents: 280_000,
  free_cents: 120_000,
  emergency_available_cents: 180_000,
  ...over,
});

/** UC-ZF-011 AK6: TV (1, 300 €), holiday (2, 900 €), PC (3, 600 €), emergency fund (locked, 1,000 €). */
const TV = pot({
  id: 1,
  name: "Test TV",
  balance_cents: 30_000,
  emergency_rank: 1,
});
const HOLIDAY = pot({
  id: 2,
  name: "Test holiday",
  balance_cents: 90_000,
  emergency_rank: 2,
  target_amount_cents: 200_000,
  target_date: "2027-06-01",
});
const PC = pot({
  id: 3,
  name: "Test PC",
  balance_cents: 60_000,
  emergency_rank: 3,
});
const FUND = pot({
  id: 4,
  name: "Test fund",
  balance_cents: 100_000,
  emergency_rank: null,
  locked: true,
});

interface Call {
  method: string;
  url: string;
  body: unknown;
}
type Handler = (call: Call) => unknown;

/** Fake fetch: `routes` maps "METHOD /path" to a body, a Response or a function returning one. */
function fakeApi(routes: Record<string, Handler | unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const call: Call = {
        method: init?.method ?? "GET",
        url,
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      };
      calls.push(call);
      const route = routes[`${call.method} ${url.split("?")[0]}`];
      const answer =
        typeof route === "function" ? (route as Handler)(call) : route;
      if (answer instanceof Response) return answer;
      if (answer === undefined) return new Response(null, { status: 204 });
      return Response.json(answer, {
        status: call.method === "POST" ? 201 : 200,
      });
    }),
  );
  return calls;
}

const refuse = (detail: string) => Response.json({ detail }, { status: 409 });

function overviewOf(pots: Pot[], splits: AccountSplit[] = [split()]): Overview {
  return { pots, accounts: splits };
}

/** Opens the form once the accounts have loaded (the button is disabled until then). */
async function openNewPot() {
  const button = await screen.findByRole("button", { name: "New pot" });
  await waitFor(() => expect(button.hasAttribute("disabled")).toBe(false));
  fireEvent.click(button);
}

function renderPage() {
  return render(
    <I18nProvider i18n={i18n}>
      <MemoryRouter>
        <PotsPage />
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
  vi.restoreAllMocks();
});

describe("draft rules", () => {
  it("builds the API body of a pot and names the first invalid field", () => {
    // Oracle: spec-anchored — UC-ZF-011 step 1 (name, account, target 3,000 €, date, plan expense, locked) and 1a/1b (target optional); cents by hand.
    const draft = {
      ...emptyDraft(ACCOUNTS[0]),
      name: " Test car ",
      target: "3000",
      targetDate: "2027-07-19",
      planExpense: true,
      position: "locked",
      isLevyPot: true,
    };
    expect(toInput(draft)).toEqual({
      input: {
        name: "Test car",
        account_id: 1,
        area: "private",
        target_amount_cents: 300_000,
        target_date: "2027-07-19",
        plan_expense: true,
        locked: true,
        is_levy_pot: true,
      },
    });
    expect(
      toInput({ ...draft, target: "", targetDate: "", planExpense: false }),
    ).toMatchObject({
      input: { target_amount_cents: null, target_date: null },
    });
    expect(toInput({ ...draft, name: " " })).toEqual({ invalid: "name" });
    expect(toInput({ ...draft, accountId: "" })).toEqual({
      invalid: "account",
    });
    expect(toInput({ ...draft, target: "0" })).toEqual({ invalid: "target" });
    expect(toInput({ ...draft, target: "abc" })).toEqual({ invalid: "target" });
    expect(toInput({ ...draft, targetDate: "" })).toEqual({
      invalid: "planExpense",
    });
  });

  it("orders unlocked pots by rank and moves a pot to a new place", () => {
    // Oracle: spec-anchored — UC-ZF-011 AK7 (the order changes by moving one pot): [1,2,3] with 3 dropped on 1 is [3,1,2]; 1 dropped on 3 is [2,3,1]; a pot dropped on itself changes nothing.
    expect(reorder([1, 2, 3], 3, { before: 1 })).toEqual([3, 1, 2]);
    expect(reorder([1, 2, 3], 1, { before: 3 })).toEqual([2, 3, 1]);
    expect(reorder([1, 2, 3], 2, { index: 0 })).toEqual([2, 1, 3]);
    expect(reorder([1, 2, 3], 2, { index: 9 })).toEqual([1, 3, 2]);
    expect(reorder([1, 2, 3], 2, { before: 2 })).toBeNull();
    expect(reorder([1, 2, 3], 9, { index: 0 })).toBeNull();
    const ranked = orderedPots([PC, FUND, TV, HOLIDAY], 1).map((p) => p.id);
    expect(ranked).toEqual([1, 2, 3]);
  });

  it("sums the emergency lines and refuses a line that is not an amount", () => {
    // Oracle: spec-anchored — UC-ZF-011 AK6: 300 + 200 = 500; an empty line counts 0.
    expect(sumLines(["300", "200,00", ""])).toBe(50_000);
    expect(sumLines(["300", "abc"])).toBeNull();
    expect(sumLines(["-5"])).toBeNull();
  });
});

describe("pot cards", () => {
  it("shows balance, target, progress and the monthly rate", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK1: target 3,000 €, balance 500 €, 10 months → 250 €; 500 of 3,000 is 16 % (rounded down by the API, given as progress_pct).
    fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf(
        [
          pot({
            id: 1,
            name: "Test car",
            balance_cents: 50_000,
            target_amount_cents: 300_000,
            target_date: "2027-07-19",
            progress_pct: 16,
            monthly_rate_cents: 25_000,
            status: "on_track",
          }),
        ],
        [
          split({
            balance_cents: 50_000,
            pots_cents: 50_000,
            free_cents: 0,
            emergency_available_cents: 50_000,
          }),
        ],
      ),
    });
    renderPage();
    const car = await screen.findByRole("article", { name: "Test car" });
    expect(within(car).getByText("Needs €250.00 a month.")).not.toBeNull();
    expect(within(car).getByText(/€500\.00 of €3,000\.00/)).not.toBeNull();
    expect(within(car).getByText("On track")).not.toBeNull();
    expect(within(car).getByText("16 %")).not.toBeNull();
    // Rank 1, no lock.
    expect(within(car).getByText("Emergency rank 1")).not.toBeNull();
  });

  it("exposes the progress bar with a name and its value", async () => {
    // Oracle: spec-anchored — SPEC-T-2026-CU-9130-018 AK12 (progress bar has an accessible name and value).
    fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([
        pot({
          id: 1,
          name: "Test car",
          target_amount_cents: 300_000,
          progress_pct: 40,
          status: "on_track",
        }),
      ]),
    });
    renderPage();
    const bar = (await screen.findByRole("progressbar", {
      name: "Progress of Test car",
    })) as HTMLProgressElement;
    expect(bar.value).toBe(40);
    expect(bar.max).toBe(100);
  });

  it("a pot behind plan names the new rate", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK3/4a: (3,000 − 500) ÷ 8 months = 312.50 € new monthly rate.
    fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([
        pot({
          id: 1,
          name: "Test car",
          balance_cents: 50_000,
          target_amount_cents: 300_000,
          target_date: "2027-05-19",
          progress_pct: 16,
          monthly_rate_cents: 31_250,
          status: "behind",
        }),
      ]),
    });
    renderPage();
    const car = await screen.findByRole("article", { name: "Test car" });
    expect(within(car).getByText("Behind plan")).not.toBeNull();
    expect(
      within(car).getByText("Behind plan: needs €312.50 a month from now on."),
    ).not.toBeNull();
  });

  it("shows only the balance for a pot without target and marks locked and levy pots", async () => {
    // Oracle: spec-anchored — UC-ZF-011 1b (no target: balance and history only) and step 1 (locked, levy marker as badges).
    fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([{ ...FUND, is_levy_pot: true }]),
    });
    renderPage();
    const fund = await screen.findByRole("article", { name: "Test fund" });
    expect(within(fund).queryByRole("progressbar")).toBeNull();
    expect(
      within(fund).getByText(
        "No target amount: only the balance and its history.",
      ),
    ).not.toBeNull();
    expect(within(fund).getByText("Locked")).not.toBeNull();
    expect(within(fund).getByText("Levy reserve")).not.toBeNull();
    expect(
      within(fund).getByText("Locked: never used in an emergency."),
    ).not.toBeNull();
  });

  it("shows the free balance and what an emergency could take per account", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK2/step 4 with the AK6 pots on a 4,000 € account: free = 4,000 − (300 + 900 + 600 + 1,000) = 1,200 €; available in an emergency = 300 + 900 + 600 = 1,800 € (locked fund left out).
    fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY, PC, FUND]),
    });
    renderPage();
    const section = await screen.findByRole("region", { name: "Test savings" });
    const tile = (label: string) =>
      within(section).getByText(label).closest(".ui-kpi") as HTMLElement;
    expect(within(tile("Free balance")).getByText("€1,200.00")).not.toBeNull();
    expect(
      within(tile("Account balance")).getByText("€4,000.00"),
    ).not.toBeNull();
    expect(
      within(tile("Available in an emergency")).getByText("€1,800.00"),
    ).not.toBeNull();
  });
});

describe("assigning money", () => {
  it("deposit above the free balance shows the backend message at the field", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK2/3a: an assignment above the free balance is refused naming the free balance (message text from the backend catalog).
    const message =
      "Only 1200.00 € of the account are free; the pot cannot get more.";
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY]),
      "POST /api/pots/1/deposits": () => refuse(message),
    });
    renderPage();
    fireEvent.click(
      within(await screen.findByRole("article", { name: "Test TV" })).getByRole(
        "button",
        { name: "Add money" },
      ),
    );
    const amount = screen.getByLabelText("Amount");
    fireEvent.change(amount, { target: { value: "5000" } });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Assign",
      }),
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
    expect(amount.getAttribute("aria-describedby")).toContain(alert.id);
    // The dialog stays open with the typed value.
    expect((amount as HTMLInputElement).value).toBe("5000");
    expect(calls.filter((c) => c.method === "POST")).toEqual([
      expect.objectContaining({
        url: "/api/pots/1/deposits",
        body: { amount_cents: 500_000, from_account_id: null },
      }),
    ]);
  });

  it("sends nothing while the amount is not a positive number", async () => {
    // Oracle: spec-anchored — UC-ZF-011 step 3: an amount must be greater than 0.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY]),
    });
    renderPage();
    fireEvent.click(
      within(await screen.findByRole("article", { name: "Test TV" })).getByRole(
        "button",
        { name: "Add money" },
      ),
    );
    fireEvent.change(screen.getByLabelText("Amount"), {
      target: { value: "0" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Assign",
      }),
    );
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Enter an amount greater than 0, e.g. 500.",
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("a locked pot needs the confirmation to take money out", async () => {
    // Oracle: spec-anchored — UC-ZF-011 5c: taking from a locked pot only after an explicit confirmation; the request carries confirm_locked.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, FUND]),
      "POST /api/pots/4/withdrawals": { ...FUND, balance_cents: 90_000 },
    });
    renderPage();
    fireEvent.click(
      within(
        await screen.findByRole("article", { name: "Test fund" }),
      ).getByRole("button", { name: "Take out" }),
    );
    fireEvent.change(screen.getByLabelText("Amount"), {
      target: { value: "100" },
    });
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Take out" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Confirm that you want to take money from the locked pot.",
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Take out" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        amount_cents: 10_000,
        confirm_locked: true,
      }),
    );
    expect((await screen.findByRole("status")).textContent).toBe(
      "€100.00 taken out of Test fund.",
    );
  });

  it("move sends both pots and the amount", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK5/3b: 200 € from pot A to pot B, no account involved.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY]),
      "POST /api/pots/transfers": [HOLIDAY, TV],
    });
    renderPage();
    fireEvent.click(
      within(
        await screen.findByRole("article", { name: "Test holiday" }),
      ).getByRole("button", { name: "Move money" }),
    );
    fireEvent.change(screen.getByLabelText("Amount"), {
      target: { value: "200" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Move" }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.url === "/api/pots/transfers")?.body).toEqual({
        from_pot_id: 2,
        to_pot_id: 1,
        amount_cents: 20_000,
        confirm_locked: false,
      }),
    );
    expect((await screen.findByRole("status")).textContent).toBe(
      "€200.00 moved from Test holiday to Test TV.",
    );
  });

  it("assigning an expense reports the split", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK4/5a: a 500 € expense on a pot holding 300 € — the pot covers 300 €, 200 € come from the free balance.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY]),
      "GET /api/transactions": [
        {
          id: 7,
          date: "2026-09-10",
          amount_cents: -50_000,
          account_id: 1,
          category_id: null,
          text: "Test shop",
          counterparty: null,
          memo: null,
          status: "booked",
          source: "manual",
          transfer_group_id: null,
          transfer_account_id: null,
          recurring_id: null,
          created_at: "2026-09-10T10:00:00Z",
        },
        {
          id: 8,
          date: "2026-09-11",
          amount_cents: 12_000,
          account_id: 1,
          category_id: null,
          text: "Test income",
          counterparty: null,
          memo: null,
          status: "booked",
          source: "manual",
          transfer_group_id: null,
          transfer_account_id: null,
          recurring_id: null,
          created_at: "2026-09-11T10:00:00Z",
        },
      ],
      "POST /api/pots/1/expenses": {
        pot: TV,
        covered_cents: 30_000,
        from_free_cents: 20_000,
      },
    });
    renderPage();
    fireEvent.click(
      within(await screen.findByRole("article", { name: "Test TV" })).getByRole(
        "button",
        { name: "Assign expense" },
      ),
    );
    const select = (await screen.findByLabelText(
      "Expense",
    )) as HTMLSelectElement;
    // Only the booked expense is offered, not the income.
    await waitFor(() => expect(select.options.length).toBe(1));
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Assign expense",
      }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        transaction_id: 7,
        confirm_locked: false,
      }),
    );
    expect((await screen.findByRole("status")).textContent).toBe(
      "Test TV covered €300.00; €200.00 came from the free balance of the account.",
    );
  });

  it("the savings plan offers the monthly rate and posts it", async () => {
    // Oracle: spec-anchored — UC-ZF-011 step 2/AK1: the offered monthly amount is the rate 250 € (from the API), paid from another account.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([
        pot({
          id: 1,
          name: "Test car",
          target_amount_cents: 300_000,
          target_date: "2027-07-19",
          monthly_rate_cents: 25_000,
          progress_pct: 16,
          status: "on_track",
        }),
      ]),
      "PUT /api/pots/1/savings-plan": pot({ id: 1, name: "Test car" }),
    });
    renderPage();
    fireEvent.click(
      within(
        await screen.findByRole("article", { name: "Test car" }),
      ).getByRole("button", { name: "Savings plan" }),
    );
    const amount = screen.getByLabelText("Per month") as HTMLInputElement;
    expect(amount.value).toBe("250.00");
    // Only the other account can pay in.
    const from = screen.getByLabelText("Paid from") as HTMLSelectElement;
    expect([...from.options].map((o) => o.textContent)).toEqual([
      "Test current",
    ]);
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Save plan",
      }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        from_account_id: 2,
        amount_cents: 25_000,
      }),
    );
  });
});

describe("closing", () => {
  it("asks first and closes only after the answer yes", async () => {
    // Oracle: spec-anchored — UC-ZF-011 step 6: closing returns the rest to the free balance — a decision that asks first.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY]),
      "POST /api/pots/1/close": { ...TV, closed: true },
    });
    const confirm = vi
      .spyOn(window, "confirm")
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true);
    renderPage();
    const tv = await screen.findByRole("article", { name: "Test TV" });
    fireEvent.click(within(tv).getByRole("button", { name: "Close" }));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    fireEvent.click(within(tv).getByRole("button", { name: "Close" }));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/pots/1/close")).toBe(true),
    );
    expect(confirm).toHaveBeenCalledTimes(2);
  });
});

describe("emergency withdrawal", () => {
  const PROPOSAL = {
    account_id: 1,
    amount_cents: 50_000,
    lines: [
      {
        pot_id: 1,
        name: "Test TV",
        rank: 1,
        amount_cents: 30_000,
        new_balance_cents: 0,
        target_date: null,
        shifted_target_date: null,
      },
      {
        pot_id: 2,
        name: "Test holiday",
        rank: 2,
        amount_cents: 20_000,
        new_balance_cents: 70_000,
        target_date: "2027-06-01",
        shifted_target_date: "2027-08-01",
      },
    ],
    covered_cents: 50_000,
    shortfall_cents: 0,
  };

  it("proposes in rank order and sends the edited lines", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK6/5b: 500 € → 300 € from TV, 200 € from holiday, the locked fund untouched; holiday target 2027-06-01 moves to 2027-08-01 (2 months, from the API). Edited to 150 + 50 + 300 the request carries exactly those lines.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY, PC, FUND]),
      "GET /api/pots/emergency": PROPOSAL,
      "POST /api/pots/emergency": [TV, HOLIDAY, PC],
    });
    renderPage();
    await screen.findByRole("article", { name: "Test TV" });
    fireEvent.click(
      screen.getByRole("button", { name: "Emergency withdrawal" }),
    );
    fireEvent.change(screen.getByLabelText("Amount needed"), {
      target: { value: "500" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show proposal" }));

    const dialog = screen.getByRole("dialog", { name: "Emergency withdrawal" });
    const rows = await within(dialog).findAllByRole("row");
    const body = rows
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[1]?.textContent);
    // Rank order, the locked fund is not a line.
    expect(body).toEqual(["Test TV", "Test holiday", "Test PC"]);
    expect(
      (
        within(dialog).getByLabelText(
          "Amount to take from Test TV",
        ) as HTMLInputElement
      ).value,
    ).toBe("300.00");
    expect(
      (
        within(dialog).getByLabelText(
          "Amount to take from Test holiday",
        ) as HTMLInputElement
      ).value,
    ).toBe("200.00");
    // The proposal's shifted date shows while the amount is unchanged.
    expect(within(dialog).getByText(/→/)).not.toBeNull();
    expect(
      within(dialog).getByText(/Locked pots are never part/),
    ).not.toBeNull();

    fireEvent.change(
      within(dialog).getByLabelText("Amount to take from Test holiday"),
      { target: { value: "150" } },
    );
    fireEvent.change(
      within(dialog).getByLabelText("Amount to take from Test PC"),
      { target: { value: "50" } },
    );
    expect(within(dialog).queryByText(/→/)).toBeNull();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Confirm withdrawal" }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        account_id: 1,
        lines: [
          { pot_id: 1, amount_cents: 30_000 },
          { pot_id: 2, amount_cents: 15_000 },
          { pot_id: 3, amount_cents: 5_000 },
        ],
      }),
    );
    expect((await screen.findByRole("status")).textContent).toBe(
      "Emergency withdrawal of €500.00 booked.",
    );
  });

  it("names the shortfall when the unlocked pots cannot cover the amount", async () => {
    // Oracle: spec-anchored — UC-ZF-011 5b/5c: unlocked pots hold 300 + 900 + 600 = 1,800 €; 2,000 € leaves 200 € short and the locked fund is not touched.
    fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY, PC, FUND]),
      "GET /api/pots/emergency": {
        ...PROPOSAL,
        amount_cents: 200_000,
        covered_cents: 180_000,
        shortfall_cents: 20_000,
      },
    });
    renderPage();
    await screen.findByRole("article", { name: "Test TV" });
    fireEvent.click(
      screen.getByRole("button", { name: "Emergency withdrawal" }),
    );
    fireEvent.change(screen.getByLabelText("Amount needed"), {
      target: { value: "2000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Show proposal" }));
    expect(
      await screen.findByText(
        "The unlocked pots are €200.00 short of the amount.",
      ),
    ).not.toBeNull();
  });
});

describe("emergency order", () => {
  const ranked = [TV, HOLIDAY, PC];
  const renderList = (onReorder: (ids: number[]) => void) =>
    render(
      <I18nProvider i18n={i18n}>
        <OrderList
          accountId={1}
          ranked={ranked}
          locked={[FUND]}
          onReorder={onReorder}
        />
      </I18nProvider>,
    );

  it("dropping a pot sends the new order", () => {
    // Oracle: spec-anchored — UC-ZF-011 AK7: the PC (rank 3) dragged onto the TV (rank 1) gives the order PC, TV, holiday = [3, 1, 2].
    const onReorder = vi.fn();
    renderList(onReorder);
    const items = screen.getAllByRole("listitem");
    const dataTransfer = {
      setData: vi.fn(),
      effectAllowed: "",
      dropEffect: "",
    };
    fireEvent.dragStart(items[2] as HTMLElement, { dataTransfer });
    fireEvent.dragOver(items[0] as HTMLElement, { dataTransfer });
    fireEvent.drop(items[0] as HTMLElement, { dataTransfer });
    expect(onReorder).toHaveBeenCalledExactlyOnceWith([3, 1, 2]);
  });

  it("ignores a drop that changes nothing and a locked pot is not draggable", () => {
    // Oracle: spec-anchored — UC-ZF-011 AK7/5c: dropping a pot on itself keeps the order; a locked pot has no rank to move.
    const onReorder = vi.fn();
    renderList(onReorder);
    const items = screen.getAllByRole("listitem");
    fireEvent.dragStart(items[0] as HTMLElement);
    fireEvent.drop(items[0] as HTMLElement);
    expect(onReorder).not.toHaveBeenCalled();
    expect(items[3]?.getAttribute("draggable")).toBeNull();
    expect(within(items[3] as HTMLElement).getByText("Locked")).not.toBeNull();
  });

  it("the move buttons send the same order as the drag", () => {
    // Oracle: spec-anchored — UC-ZF-011 AK7 with a keyboard alternative: "up" on the PC gives [1, 3, 2], "down" on the TV gives [2, 1, 3]; the ends have no button that would leave the list.
    const onReorder = vi.fn();
    renderList(onReorder);
    fireEvent.click(screen.getByRole("button", { name: "Move Test PC up" }));
    expect(onReorder).toHaveBeenLastCalledWith([1, 3, 2]);
    fireEvent.click(screen.getByRole("button", { name: "Move Test TV down" }));
    expect(onReorder).toHaveBeenLastCalledWith([2, 1, 3]);
    expect(
      (
        screen.getByRole("button", {
          name: "Move Test TV up",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      (
        screen.getByRole("button", {
          name: "Move Test PC down",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it("the page saves the order of the account with one call", async () => {
    // Oracle: spec-anchored — UC-ZF-011 AK7: the next proposal follows the saved order; the screen sends PUT /api/pots/ranks with every unlocked pot once.
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV, HOLIDAY, PC, FUND]),
      "PUT /api/pots/ranks": overviewOf([PC, TV, HOLIDAY, FUND]),
    });
    renderPage();
    await screen.findByRole("article", { name: "Test TV" });
    fireEvent.click(screen.getByRole("button", { name: "Move Test PC up" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        account_id: 1,
        pot_ids: [1, 3, 2],
      }),
    );
    expect((await screen.findByRole("status")).textContent).toBe(
      "Emergency order updated.",
    );
  });
});

describe("pot form", () => {
  it("creates a pot and puts it at the chosen rank", async () => {
    // Oracle: spec-anchored — UC-ZF-011 step 1: the pot is sent as entered; the API appends it (rank 3), the chosen rank 1 is applied with one PUT /ranks of the account [new, 1, 2].
    const created = pot({ id: 9, name: "Test bike", emergency_rank: 3 });
    let stored = false;
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      // After the create the overview holds the new pot at the end of the order.
      "GET /api/pots": () =>
        overviewOf(stored ? [TV, HOLIDAY, created] : [TV, HOLIDAY]),
      "POST /api/pots": () => {
        stored = true;
        return created;
      },
      "PUT /api/pots/ranks": overviewOf([created, TV, HOLIDAY]),
    });
    renderPage();
    await openNewPot();
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Test bike" },
    });
    fireEvent.change(screen.getByLabelText("Target amount (optional)"), {
      target: { value: "800" },
    });
    fireEvent.change(screen.getByLabelText("Target date (optional)"), {
      target: { value: "2027-04-01" },
    });
    fireEvent.click(
      screen.getByLabelText("Plan the expense on the target date"),
    );
    fireEvent.change(screen.getByLabelText("Emergency rank"), {
      target: { value: "1" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Save" }),
    );
    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT")).toBe(true),
    );
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({
      name: "Test bike",
      account_id: 1,
      area: "private",
      target_amount_cents: 80_000,
      target_date: "2027-04-01",
      plan_expense: true,
      locked: false,
      is_levy_pot: false,
    });
    expect(calls.find((c) => c.method === "PUT")).toMatchObject({
      url: "/api/pots/ranks",
      body: { account_id: 1, pot_ids: [9, 1, 2] },
    });
  });

  it("sends nothing while the form is invalid and names the field", async () => {
    // Oracle: spec-anchored — UC-ZF-011 step 1: a pot needs a name; "plan the expense" needs the target amount and the date (backend rule of task -015).
    const calls = fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV]),
    });
    renderPage();
    await openNewPot();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Enter a name for the pot.",
    );
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Test bike" },
    });
    fireEvent.click(
      screen.getByLabelText("Plan the expense on the target date"),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toBe(
      "To plan the expense, enter the target amount and the target date.",
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("closes a dialog with Escape", async () => {
    // Oracle: spec-anchored — SPEC-T-2026-CU-9130-018 AK12 (dialogs close on Escape).
    fakeApi({
      "GET /api/accounts": ACCOUNTS,
      "GET /api/pots": overviewOf([TV]),
    });
    renderPage();
    await openNewPot();
    expect(screen.getByRole("dialog", { name: "New pot" })).not.toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
