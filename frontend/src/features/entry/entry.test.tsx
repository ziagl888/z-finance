// @vitest-environment jsdom
/**
 * Quick entry (task -008, UC-ZF-002): the form's keyboard flow, what it keeps after saving and
 * after a failure, refusals, the planned-item hint, suggestions, the kind warning and the global
 * shortcut. The API is a fake `fetch`; every value is synthetic.
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
import { useEffect, useRef, useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import type { Account, Category } from "../accounts/accountsApi";
import { afterSave, emptyDraft, signedCents, type Draft } from "./draft";
import { EntryForm } from "./EntryForm";
import type { Transaction } from "./entryApi";
import { isQuickEntryShortcut } from "./shortcut";

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
    balance_cents: 10_000,
    balance_date: "2026-09-19",
    booking_count: 0,
  },
  {
    id: 2,
    name: "Test cash",
    area: "private",
    type: "cash",
    opening_balance_cents: 0,
    opening_date: "2026-01-01",
    reserve_floor_cents: 0,
    archived_at: null,
    balance_cents: 500,
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
    name: "Test fees",
    kind: "income",
    area: null,
    parent_id: null,
    booking_count: 0,
  },
];

interface Request {
  url: string;
  method: string;
  body?: unknown;
}

/** Fake API: records requests; `answer` decides the response of a POST/PUT. */
function stubApi(
  answer: (request: Request) => Response,
  suggestions: unknown[] = [],
): Request[] {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const request: Request = {
        url,
        method: init?.method ?? "GET",
        body: init?.body ? JSON.parse(init.body as string) : undefined,
      };
      requests.push(request);
      if (url.startsWith("/api/transactions/suggest")) {
        return Response.json(suggestions);
      }
      return answer(request);
    }),
  );
  return requests;
}

function saved(status = "booked"): Response {
  return Response.json({ id: 1, status }, { status: 201 });
}

function renderForm(
  onSaved = vi.fn(async () => {}),
  waitAccountsReady: () => Promise<void> = () => Promise.resolve(),
) {
  render(
    <I18nProvider i18n={i18n}>
      <EntryForm
        accounts={ACCOUNTS}
        categories={CATEGORIES}
        waitAccountsReady={waitAccountsReady}
        editing={null}
        onSaved={onSaved}
        onCancelEdit={vi.fn()}
      />
    </I18nProvider>,
  );
  const field = (label: string) =>
    screen.getByLabelText(label) as HTMLInputElement | HTMLSelectElement;
  return { onSaved, field };
}

const posts = (requests: Request[]) =>
  requests.filter((r) => r.method === "POST");

/** Mirrors `EntryPage`'s own readiness plumbing (not just its externally visible shape): `accounts`
 * starts empty, and settling is a state flip resolved from an *effect* rather than inline in the
 * event that triggers it — the same ordering `EntryPage.tsx` relies on so a submit that resumes
 * after `waitAccountsReady()` sees the already-committed `accounts` (T-2026-CU-9130-043). A test
 * calls `arriveRef.current()` to flip that state, in place of the real fetch resolving. */
function DeferredAccountsHarness({
  onSaved,
  arriveRef,
}: {
  onSaved: () => Promise<void>;
  arriveRef: { current: (() => void) | null };
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [settled, setSettled] = useState(false);
  const waitersRef = useRef<(() => void)[]>([]);
  useEffect(() => {
    arriveRef.current = () => {
      setAccounts(ACCOUNTS);
      setSettled(true);
    };
  });
  useEffect(() => {
    if (!settled) return;
    const waiters = waitersRef.current;
    waitersRef.current = [];
    waiters.forEach((resolve) => resolve());
  }, [settled]);
  const waitAccountsReady = () =>
    settled
      ? Promise.resolve()
      : new Promise<void>((resolve) => waitersRef.current.push(resolve));
  return (
    <EntryForm
      accounts={accounts}
      categories={CATEGORIES}
      waitAccountsReady={waitAccountsReady}
      editing={null}
      onSaved={onSaved}
      onCancelEdit={vi.fn()}
    />
  );
}

beforeEach(async () => {
  await act(() => activateLocale("en"));
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("draft rules", () => {
  it("clears everything except date and account after saving", () => {
    // Oracle: spec-anchored — UC-ZF-002 AK2: after saving, date and account are filled, all else empty.
    const full: Draft = {
      kind: "income",
      date: "2026-09-10",
      accountId: "2",
      toAccountId: "1",
      amount: "12,50",
      text: "x",
      categoryId: "7",
      counterparty: "y",
      memo: "z",
    };
    expect(afterSave(full)).toEqual(emptyDraft("2026-09-10", "2"));
  });

  it("signs the amount by kind; a typed minus means expense", () => {
    // Oracle: spec-anchored — UC-ZF-002 step 3: negative amount or the expense/income switch; hand-computed cents.
    const draft = emptyDraft("2026-09-10", "1");
    expect(signedCents({ ...draft, amount: "12,50" })).toBe(-1_250);
    expect(signedCents({ ...draft, kind: "income", amount: "12,50" })).toBe(
      1_250,
    );
    expect(signedCents({ ...draft, kind: "income", amount: "-3" })).toBe(-300);
    expect(signedCents({ ...draft, amount: "0,00" })).toBeNull();
  });
});

describe("shortcut", () => {
  it("recognizes the shortcut", () => {
    // Oracle: spec-anchored — SPEC "Keyboard shortcut": Alt+N without Ctrl/Meta/Shift (AltGr = Ctrl+Alt excluded).
    const key = {
      code: "KeyN",
      altKey: true,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
    };
    expect(isQuickEntryShortcut(key)).toBe(true);
    expect(isQuickEntryShortcut({ ...key, ctrlKey: true })).toBe(false);
    expect(isQuickEntryShortcut({ ...key, metaKey: true })).toBe(false);
    expect(isQuickEntryShortcut({ ...key, shiftKey: true })).toBe(false);
    expect(isQuickEntryShortcut({ ...key, altKey: false })).toBe(false);
    expect(isQuickEntryShortcut({ ...key, code: "KeyM" })).toBe(false);
  });
});

describe("quick entry form", () => {
  it("keeps date and account after saving", async () => {
    // Oracle: spec-anchored — UC-ZF-002 AK2 + step 5/6: Enter saves; date and account stay, the rest is empty.
    const requests = stubApi(() => saved());
    const { onSaved, field } = renderForm();
    fireEvent.change(field("Date"), { target: { value: "2026-09-10" } });
    fireEvent.change(field("Account"), { target: { value: "2" } });
    fireEvent.change(field("Amount (€)"), { target: { value: "12,50" } });
    fireEvent.change(field("Text"), { target: { value: "Test kiosk" } });
    fireEvent.change(field("Category"), { target: { value: "7" } });
    fireEvent.change(field("Counterparty (optional)"), {
      target: { value: "Kiosk" },
    });

    // Enter on the category select saves like Enter in a text field.
    fireEvent.keyDown(field("Category"), { key: "Enter" });

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(posts(requests)[0]).toEqual({
      url: "/api/transactions",
      method: "POST",
      body: {
        date: "2026-09-10",
        amount_cents: -1_250,
        account_id: 2,
        category_id: 7,
        text: "Test kiosk",
        counterparty: "Kiosk",
        memo: null,
      },
    });
    expect(field("Date").value).toBe("2026-09-10");
    expect(field("Account").value).toBe("2");
    for (const label of [
      "Amount (€)",
      "Text",
      "Category",
      "Counterparty (optional)",
      "Note (optional)",
    ]) {
      expect(field(label).value).toBe("");
    }
    expect(document.activeElement).toBe(field("Amount (€)"));
    expect(screen.getByText("Saved.")).toBeTruthy();
  });

  it("keeps the input when saving fails", async () => {
    // Oracle: spec-anchored — UC-ZF-002 6a: the input stays in the form, the message names the cause.
    stubApi(() =>
      Response.json(
        { detail: "The account Test giro is archived." },
        { status: 409 },
      ),
    );
    const { onSaved, field } = renderForm();
    fireEvent.change(field("Amount (€)"), { target: { value: "5" } });
    fireEvent.change(field("Text"), { target: { value: "Test bakery" } });
    fireEvent.change(field("Category"), { target: { value: "7" } });

    fireEvent.submit(screen.getByRole("form", { name: "Quick entry" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "The account Test giro is archived.",
    );
    expect(onSaved).not.toHaveBeenCalled();
    expect(field("Amount (€)").value).toBe("5");
    expect(field("Text").value).toBe("Test bakery");
    expect(field("Category").value).toBe("7");
  });

  it("refuses an empty or zero amount", async () => {
    // Oracle: spec-anchored — UC-ZF-002 3b: saving is refused and the field is marked.
    const requests = stubApi(() => saved());
    const { field } = renderForm();
    fireEvent.change(field("Category"), { target: { value: "7" } });

    for (const amount of ["", "0", "0,00"]) {
      fireEvent.change(field("Amount (€)"), { target: { value: amount } });
      fireEvent.submit(screen.getByRole("form", { name: "Quick entry" }));
      await screen.findByRole("alert");
      expect(field("Amount (€)").getAttribute("aria-invalid")).toBe("true");
    }
    expect(posts(requests)).toEqual([]);
  });

  it("requires a category for income and expense", async () => {
    // Oracle: spec-anchored — SPEC AK2b: the form asks for a category (UC postcondition "…, Kategorie und Text").
    const requests = stubApi(() => saved());
    const { field } = renderForm();
    fireEvent.change(field("Amount (€)"), { target: { value: "3" } });

    fireEvent.submit(screen.getByRole("form", { name: "Quick entry" }));

    expect((await screen.findByRole("alert")).textContent).toBe(
      "Choose a category.",
    );
    expect(field("Category").getAttribute("aria-invalid")).toBe("true");
    expect(posts(requests)).toEqual([]);
  });

  it("announces planned items", () => {
    // Oracle: spec-anchored — UC-ZF-002 3c: a future date is saved as planned "und sagt das im Formular".
    stubApi(() => saved("planned"));
    const { field } = renderForm();
    expect(screen.queryByText(/saved as a planned item/)).toBeNull();

    fireEvent.change(field("Date"), { target: { value: "2999-01-01" } });

    expect(screen.getByText(/saved as a planned item/)).toBeTruthy();
  });

  it("applies the suggestion", async () => {
    // Oracle: spec-anchored — UC-ZF-002 AK3/step 4: a known text brings its last category and counterparty.
    stubApi(
      () => saved(),
      [
        {
          text: "Billa",
          category_id: 7,
          counterparty: "Billa AG",
          last_date: "2026-09-01",
        },
      ],
    );
    const { field } = renderForm();

    fireEvent.change(field("Text"), { target: { value: "billa" } });

    await waitFor(() => expect(field("Category").value).toBe("7"));
    expect(field("Counterparty (optional)").value).toBe("Billa AG");
    expect(screen.getByText("Suggested from earlier bookings.")).toBeTruthy();
  });

  it("does not fail a submit started before the accounts fetch resolved", async () => {
    // Oracle: spec-anchored — SPEC-T-2026-CU-9130-043 AK2/reproduction: a booking typed and
    // submitted while `accounts` is still empty (the fetch has not resolved yet — the production
    // race, reproduced by hand by delaying GET /api/accounts, see the SPEC) must not be rejected
    // with "Choose an account." nor send no POST; it must wait and then save with the account the
    // list resolves to (the same first-active-account default the render already applies once
    // `accounts` is non-empty).
    const requests = stubApi(() => saved());
    const onSaved = vi.fn(async () => {});
    const arriveRef: { current: (() => void) | null } = { current: null };
    render(
      <I18nProvider i18n={i18n}>
        <DeferredAccountsHarness onSaved={onSaved} arriveRef={arriveRef} />
      </I18nProvider>,
    );
    const field = (label: string) =>
      screen.getByLabelText(label) as HTMLInputElement | HTMLSelectElement;

    fireEvent.change(field("Amount (€)"), { target: { value: "10,50" } });
    fireEvent.change(field("Text"), { target: { value: "Race booking" } });
    fireEvent.change(field("Category"), { target: { value: "7" } });
    fireEvent.submit(screen.getByRole("form", { name: "Quick entry" }));

    // Still pending: neither a premature error nor a POST with a missing account.
    expect(posts(requests)).toEqual([]);
    expect(screen.queryByRole("alert")).toBeNull();

    // The accounts fetch "arrives".
    await act(async () => arriveRef.current?.());

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(posts(requests)[0]?.body).toMatchObject({
      account_id: 1, // ACCOUNTS' first entry: "Test giro"
      text: "Race booking",
    });
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("warns on kind mismatch", async () => {
    // Oracle: spec-anchored — UC-ZF-001 3b: income category with a negative amount warns but saves.
    const requests = stubApi(() => saved());
    const { onSaved, field } = renderForm();
    fireEvent.change(field("Amount (€)"), { target: { value: "20" } });
    fireEvent.change(field("Category"), { target: { value: "8" } });

    expect(screen.getByText(/category kind does not match/)).toBeTruthy();
    fireEvent.submit(screen.getByRole("form", { name: "Quick entry" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(posts(requests)[0]?.body).toMatchObject({
      amount_cents: -2_000,
      category_id: 8,
    });
  });
});

describe("editing a booking on an archived account", () => {
  const ARCHIVED: Account = {
    ...ACCOUNTS[0]!,
    id: 3,
    name: "Test old giro",
    archived_at: "2026-08-01T00:00:00",
  };
  const BOOKING: Transaction = {
    id: 41,
    date: "2026-07-15",
    amount_cents: -1_250,
    account_id: 3,
    category_id: 7,
    text: "Test kiosk",
    counterparty: null,
    memo: null,
    status: "booked",
    source: "manual",
    transfer_group_id: null,
    transfer_account_id: null,
    recurring_id: null,
    created_at: "2026-07-15T10:00:00",
  };

  it("keeps the booking on its archived account and marks it as archived", async () => {
    // Oracle: spec-anchored — T-2026-HAR-9130-011: an edit must not move a booking to another account.
    const requests = stubApi(() => Response.json({ id: 41, status: "booked" }));
    render(
      <I18nProvider i18n={i18n}>
        <EntryForm
          accounts={ACCOUNTS}
          allAccounts={[...ACCOUNTS, ARCHIVED]}
          categories={CATEGORIES}
          waitAccountsReady={() => Promise.resolve()}
          editing={BOOKING}
          onSaved={vi.fn(async () => {})}
          onCancelEdit={vi.fn()}
        />
      </I18nProvider>,
    );
    const account = screen.getByLabelText("Account") as HTMLSelectElement;

    expect(account.value).toBe("3");
    expect(
      Array.from(account.options).map((option) => option.textContent),
    ).toContain("Test old giro (archived)");

    fireEvent.change(screen.getByLabelText("Amount (€)"), {
      target: { value: "13" },
    });
    fireEvent.submit(screen.getByRole("form", { name: "Quick entry" }));

    await waitFor(() =>
      expect(requests.some((r) => r.method === "PUT")).toBe(true),
    );
    expect(requests.find((r) => r.method === "PUT")).toMatchObject({
      url: "/api/transactions/41",
      body: { account_id: 3, amount_cents: -1_300 },
    });
  });
});
