// @vitest-environment jsdom
/**
 * Taxes & levies (task -014, UC-ZF-008): the draft rules, what the form sends and the hint while
 * nothing is entered. The API is a fake `fetch`; every value is synthetic.
 */
import { I18nProvider } from "@lingui/react";
import { i18n } from "@lingui/core";
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
import type { Account } from "../accounts/accountsApi";
import { draftFrom, toInput, type TaxDraft } from "./draft";
import { TaxPage } from "./TaxPage";
import type { TaxSettings } from "./taxApi";

const BUSINESS: Account = {
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

const EMPTY: TaxSettings = {
  business_account_id: null,
  est_vz_quarter_cents: null,
  svs_quarter_cents: null,
  est_due_dates: ["02-15", "05-15", "08-15", "11-15"],
  svs_due_dates: ["02-31", "05-31", "08-31", "11-31"],
  plan_missing: true,
  terms: [],
};

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const draft = (changes: Partial<TaxDraft> = {}): TaxDraft => ({
  ...draftFrom(EMPTY, "en", "7"),
  ...changes,
});

describe("draft rules", () => {
  it("builds the API body: cents without floats, empty means not entered", () => {
    // Oracle: spec-anchored — UC-ZF-008 AK1 (1,500 € per quarter → 150,000 cents) and 1a (empty = not entered).
    expect(toInput(draft({ est: "1500", svs: "" }))).toEqual({
      input: {
        business_account_id: 7,
        est_vz_quarter_cents: 150_000,
        svs_quarter_cents: null,
        est_due_dates: EMPTY.est_due_dates,
        svs_due_dates: EMPTY.svs_due_dates,
      },
    });
    expect(toInput(draft({ est: "1.500,50" }))).toEqual({ invalid: "est" });
    expect(toInput(draft({ svs: "0" }))).toEqual({ invalid: "svs" });
    expect(toInput(draft({ svs: "-5" }))).toEqual({ invalid: "svs" });
  });

  it("needs the account for an amount and distinct due dates", () => {
    // Oracle: spec-anchored — UC-ZF-008 step 1 (amounts and business account) and orchestrator note (dates distinct).
    expect(toInput(draft({ est: "1500", accountId: "" }))).toEqual({
      invalid: "account",
    });
    expect(toInput(draft({ accountId: "" }))).toMatchObject({
      input: { business_account_id: null },
    });
    expect(toInput(draft({ estDates: ["02-15", "02-15"] }))).toEqual({
      invalid: "estDates",
    });
    expect(toInput(draft({ svsDates: [] }))).toEqual({ invalid: "svsDates" });
  });
});

describe("tax page", () => {
  it("shows the hint while empty and sends the entered amount", async () => {
    // Oracle: spec-anchored — UC-ZF-008 1a (hint "not set") and step 1 (1,500 € ESt-VZ, business account).
    const saved: TaxSettings = {
      ...EMPTY,
      business_account_id: 7,
      est_vz_quarter_cents: 150_000,
      plan_missing: false,
    };
    const calls: { method: string; url: string; body: unknown }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? "GET";
        calls.push({
          method,
          url,
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        const payload =
          url === "/api/accounts?include_archived=false" ||
          url.startsWith("/api/accounts")
            ? [BUSINESS]
            : method === "PUT"
              ? saved
              : EMPTY;
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }),
    );

    render(
      <I18nProvider i18n={i18n}>
        <MemoryRouter>
          <TaxPage />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText(/not set yet/)).toBeTruthy();
    fireEvent.change(
      screen.getByLabelText("Amount per due date", { selector: "#tax-est" }),
      {
        target: { value: "1500" },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(calls.some((c) => c.method === "PUT")).toBe(true),
    );
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/settings/tax");
    expect(put?.body).toMatchObject({
      business_account_id: 7,
      est_vz_quarter_cents: 150_000,
      svs_quarter_cents: null,
    });
    expect(await screen.findByText("Saved.")).toBeTruthy();
    expect(screen.queryByText(/not set yet/)).toBeNull();
  });
});
