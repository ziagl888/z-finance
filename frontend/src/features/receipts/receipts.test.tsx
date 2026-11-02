// @vitest-environment jsdom
/**
 * Receipts screen (task -016, UC-ZF-013 step 7): review list with photo and reason, correction
 * form (validation, book), discard, agent runs, settings. The API is a fake `fetch`; every value
 * is synthetic.
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

import { activateLocale } from "../../i18n/locale";
import type { Account, Category } from "../accounts/accountsApi";
import type { AgentRun, Receipt } from "./receiptsApi";
import { ReceiptsPage } from "./ReceiptsPage";
import { initialDraft, toBookInput } from "./ReviewCard";

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
    balance_cents: 0,
    balance_date: "2026-09-19",
    booking_count: 0,
  },
  {
    id: 2,
    name: "Test wallet",
    area: "private",
    type: "cash",
    opening_balance_cents: 0,
    opening_date: "2026-01-01",
    reserve_floor_cents: 0,
    archived_at: null,
    balance_cents: 0,
    balance_date: "2026-09-19",
    booking_count: 0,
  },
];

const CATEGORIES: Category[] = [
  {
    id: 7,
    name: "Test groceries",
    kind: "expense",
    area: null,
    parent_id: null,
    booking_count: 0,
  },
];

const REVIEW: Receipt = {
  id: 11,
  status: "review",
  reason: "missing_fields",
  transaction_id: null,
  confidence: 0.9,
  extracted: {
    date: "2026-09-10",
    amount_cents: null,
    merchant: "Test shop",
    category_suggestion: "test groceries",
    confidence: 0.9,
    currency: "EUR",
    hints: "bar",
  },
  duplicate_of: null,
  message_ref: null,
  run_id: 3,
  created_at: "2026-09-19T08:00:00+00:00",
  booked_at: null,
  booking_date: null,
  booking_amount_cents: null,
  booking_text: null,
  booking_account_id: null,
  photo_url: "/api/receipts/11/photo",
};

const RUN: AgentRun = {
  id: 3,
  started_at: "2026-09-19T08:00:00+00:00",
  finished_at: "2026-09-19T08:01:00+00:00",
  counts: {},
  note: "Test run",
  booked: 2,
  review: 1,
  duplicate: 0,
};

interface Call {
  url: string;
  method: string;
  body?: unknown;
}

function stubApi(review: Receipt[] = [REVIEW]): Call[] {
  const calls: Call[] = [];
  let pending = review;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ url, method, body });
      if (url.startsWith("/api/accounts")) return Response.json(ACCOUNTS);
      if (url === "/api/categories") return Response.json(CATEGORIES);
      if (url === "/api/receipts?status=review&status=duplicate")
        return Response.json(pending);
      if (url.startsWith("/api/receipts?status=booked"))
        return Response.json([]);
      if (url === "/api/receipts/runs") return Response.json([RUN]);
      if (url === "/api/receipts/settings" && method === "GET")
        return Response.json({
          confidence_threshold: 0.8,
          accounts: {
            private: { cash: null, card: null },
            business: { cash: null, card: null },
          },
        });
      if (url === "/api/receipts/settings" && method === "PUT")
        return Response.json(body);
      if (url === "/api/receipts/11/book" && method === "POST") {
        pending = [];
        return Response.json({
          ...REVIEW,
          status: "booked",
          transaction_id: 5,
        });
      }
      if (url === "/api/receipts/11" && method === "DELETE") {
        pending = [];
        return new Response(null, { status: 204 });
      }
      return Response.json(
        { detail: `unexpected ${method} ${url}` },
        { status: 500 },
      );
    }),
  );
  return calls;
}

function renderPage() {
  render(
    <I18nProvider i18n={i18n}>
      <ReceiptsPage />
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

describe("correction form helpers", () => {
  it("prefills from what the agent read and refuses a missing total", () => {
    // Oracle: spec-anchored — UC-ZF-013 AK3 (no amount → review item; correction books it).
    const draft = initialDraft(REVIEW, CATEGORIES, "2026-09-19", "en");
    expect(draft).toEqual({
      date: "2026-09-10",
      amount: "",
      merchant: "Test shop",
      accountId: "",
      categoryId: "7",
    });
    expect(toBookInput(draft)).toEqual({ missing: "amount" });
    expect(toBookInput({ ...draft, amount: "12,34" })).toEqual({
      missing: "account",
    });
    expect(toBookInput({ ...draft, amount: "12,34", accountId: "2" })).toEqual({
      input: {
        date: "2026-09-10",
        amount_cents: 1_234,
        merchant: "Test shop",
        account_id: 2,
        category_id: 7,
      },
    });
  });
});

describe("receipts screen", () => {
  it("shows a review item with photo and reason and books the correction", async () => {
    // Oracle: spec-anchored — UC-ZF-013 step 7 ("Liste Belege prüfen mit Foto ... mit einem Klick
    // korrigiert und gebucht") and AK3.
    const calls = stubApi();
    renderPage();

    const card = (
      await screen.findByRole("heading", {
        name: "Date, total or merchant missing",
      })
    ).closest("article") as HTMLElement;
    const photo = within(card).getByRole("img") as HTMLImageElement;
    expect(photo.getAttribute("src")).toBe("/api/receipts/11/photo");

    fireEvent.click(within(card).getByRole("button", { name: "Book" }));
    expect((await within(card).findByRole("alert")).textContent).toBe(
      "Enter the total as an amount greater than 0.",
    );
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    fireEvent.change(within(card).getByLabelText("Total (€)"), {
      target: { value: "12.34" },
    });
    fireEvent.change(within(card).getByLabelText("Account"), {
      target: { value: "2" },
    });
    fireEvent.click(within(card).getByRole("button", { name: "Book" }));

    expect(await screen.findByText("Nothing to check.")).toBeTruthy();
    const post = calls.find((c) => c.method === "POST");
    expect(post).toEqual({
      url: "/api/receipts/11/book",
      method: "POST",
      body: {
        date: "2026-09-10",
        amount_cents: 1_234,
        merchant: "Test shop",
        account_id: 2,
        category_id: 7,
      },
    });
    expect(screen.getByText("Booked: Test shop.")).toBeTruthy();
  });

  it("discards a receipt after confirmation and lists the agent runs", async () => {
    // Oracle: spec-anchored — UC-ZF-013 6a ("Agent-Läufe") and SPEC-T-2026-CU-9130-016 (discard).
    const calls = stubApi();
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    renderPage();

    const runsTable = (await screen.findByText("Test run")).closest(
      "tr",
    ) as HTMLElement;
    expect(
      within(runsTable)
        .getAllByRole("cell")
        .slice(1, 4)
        .map((cell) => cell.textContent),
    ).toEqual(["2", "1", "0"]);

    fireEvent.click(await screen.findByRole("button", { name: "Discard" }));
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === "DELETE" && c.url === "/api/receipts/11",
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("Nothing to check.")).toBeTruthy();
  });

  it("saves threshold and account mapping", async () => {
    // Oracle: spec-anchored — task: confidence threshold setting (default 0.8), payment-method mapping.
    const calls = stubApi([]);
    renderPage();

    const threshold = (await screen.findByLabelText(
      "Check receipts read with less than (%)",
    )) as HTMLInputElement;
    await waitFor(() => expect(threshold.value).toBe("80"));
    fireEvent.change(threshold, { target: { value: "70" } });
    fireEvent.change(screen.getByLabelText("Cash payments, private"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        confidence_threshold: 0.7,
        accounts: {
          private: { cash: 2, card: null },
          business: { cash: null, card: null },
        },
      }),
    );
    fireEvent.change(threshold, { target: { value: "150" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(
      await screen.findByText("Enter a percentage between 0 and 100."),
    ).toBeTruthy();
  });
});
