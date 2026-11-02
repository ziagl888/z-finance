// @vitest-environment jsdom
/**
 * First-run wizard (task -027, UC NF10): the four steps, what Finish sends and in which order, and
 * the one redirect the shell performs. The API is a fake `fetch`; every value is synthetic.
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
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { FirstRunWizard } from "./FirstRunWizard";
import { useFirstRunRedirect } from "./redirect";

vi.mock("../../i18n", () => ({
  useLocale: () => ({ locale: "en", setLocale: vi.fn() }),
  isLocale: (value: unknown) => value === "en" || value === "de",
  LOCALES: { en: "English", de: "Deutsch" },
}));

interface Call {
  method: string;
  url: string;
  body: unknown;
}

const DEFAULT_SET = [
  { name: "Housing", kind: "expense", area: "private" },
  { name: "Fees", kind: "income", area: "business" },
];

const LEVY_SETTINGS = {
  levy_rate_bp: 0,
  levy_base: "income",
  due_month_day: "07-01",
  levy_pot_id: null,
  account_id: null,
  settings_missing: true,
};

function answer(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

/** Fake backend; `offer` decides whether the default category set is still on offer. */
function stubApi(calls: Call[], offer = true, levyFailures = 0) {
  const accounts: { id: number; name: string }[] = [];
  let levyFailuresLeft = levyFailures;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      calls.push({
        method,
        url,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (url.startsWith("/api/accounts/setup") && method === "GET") {
        return answer({
          offer_default_set: offer,
          language: "en",
          default_categories: offer ? DEFAULT_SET : [],
        });
      }
      if (
        (url === "/api/accounts" || url.startsWith("/api/accounts?")) &&
        method === "GET"
      ) {
        return answer(accounts);
      }
      if (url === "/api/accounts" && method === "POST") {
        const created = {
          id: accounts.length + 1,
          name: (init?.body
            ? JSON.parse(String(init.body)).name
            : "") as string,
        };
        accounts.push(created);
        return answer(created, 201);
      }
      if (url === "/api/levy/settings" && method === "GET") {
        return answer(LEVY_SETTINGS);
      }
      if (url === "/api/levy/settings" && method === "PUT") {
        if (levyFailuresLeft > 0) {
          levyFailuresLeft -= 1;
          return answer({ detail: "Backend restarting" }, 500);
        }
        return answer({ ...LEVY_SETTINGS, levy_rate_bp: 3500 });
      }
      if (url.startsWith("/api/accounts/setup") && method === "POST") {
        return answer([], 201);
      }
      return answer({});
    }),
  );
}

function renderWizard() {
  const router = createMemoryRouter(
    [
      { path: "/", element: <h1>Start page</h1> },
      { path: "/first-run", element: <FirstRunWizard /> },
    ],
    { initialEntries: ["/first-run"] },
  );
  return render(
    <I18nProvider i18n={i18n}>
      <RouterProvider router={router} />
    </I18nProvider>,
  );
}

/** Leave the language step; it stays disabled until the backend answered with the offered set. */
async function leaveTheLanguageStep() {
  const next = await screen.findByRole("button", { name: "Continue" });
  await waitFor(() => expect(next.hasAttribute("disabled")).toBe(false));
  fireEvent.click(next);
}

/** Walk from the language step to the levy step, filling in the first account on the way. */
async function walkToTheLevyStep(name = "Everyday") {
  await leaveTheLanguageStep();
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: name },
  });
  fireEvent.change(screen.getByLabelText("Balance today"), {
    target: { value: "1200,00" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Categories to start with" });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { name: "Taxes and contributions" });
}

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the wizard", () => {
  it("walks through the four steps", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6b: language, account, categories, levy. */
    stubApi([]);
    renderWizard();
    const steps = await screen.findByRole("list");
    expect(
      Array.from(steps.querySelectorAll("li")).map((item) => item.textContent),
    ).toEqual(["Language", "First account", "Categories", "Taxes"]);
    await walkToTheLevyStep();
    expect(screen.getByRole("button", { name: "Finish" })).toBeTruthy();
  });

  it("sends the levy rate when one is entered", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6b: 35 % goes out as 3500 basis points. */
    const calls: Call[] = [];
    stubApi(calls);
    renderWizard();
    await walkToTheLevyStep();
    fireEvent.change(screen.getByLabelText("Set aside (%)"), {
      target: { value: "35" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await screen.findByRole("heading", { name: "Start page" });

    const put = calls.find((call) => call.method === "PUT");
    expect(put?.url).toBe("/api/levy/settings");
    expect((put?.body as { levy_rate_bp: number }).levy_rate_bp).toBe(3500);
  });

  it("skips the levy rate when none is entered", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6b: an empty levy step sends no request. */
    const calls: Call[] = [];
    stubApi(calls);
    renderWizard();
    await walkToTheLevyStep();
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await screen.findByRole("heading", { name: "Start page" });

    expect(calls.filter((call) => call.url === "/api/levy/settings")).toEqual(
      [],
    );
  });

  it("creates the account, then adopts the kept categories", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK6: account first, the set that ends the run last. */
    const calls: Call[] = [];
    stubApi(calls);
    renderWizard();
    await leaveTheLanguageStep();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Everyday" },
    });
    fireEvent.change(screen.getByLabelText("Balance today"), {
      target: { value: "1200,00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Categories to start with" });
    fireEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]!);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { name: "Taxes and contributions" });
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await screen.findByRole("heading", { name: "Start page" });

    const writes = calls.filter((call) => call.method === "POST");
    expect(writes.map((call) => call.url)).toEqual([
      "/api/accounts",
      "/api/accounts/setup",
    ]);
    expect(writes[0]!.body).toMatchObject({
      name: "Everyday",
      opening_balance_cents: 120_000,
    });
    expect(writes[1]!.body).toEqual({ categories: [DEFAULT_SET[0]] });
  });

  it("creates the account once when Finish is retried after a failure", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-HAR-9130-018 AK1: a second Finish click creates no second account. */
    const calls: Call[] = [];
    stubApi(calls, true, 1);
    renderWizard();
    await walkToTheLevyStep();
    fireEvent.change(screen.getByLabelText("Set aside (%)"), {
      target: { value: "35" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await screen.findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await screen.findByRole("heading", { name: "Start page" });

    const created = calls.filter(
      (call) => call.method === "POST" && call.url === "/api/accounts",
    );
    expect(created).toHaveLength(1);
    expect(calls.filter((call) => call.method === "PUT")).toHaveLength(2);
  });

  it("takes over an account of the same name that already exists", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-HAR-9130-018 AK2: an existing account of that name is reused, not duplicated. */
    const calls: Call[] = [];
    stubApi(calls);
    const fetchStub = globalThis.fetch;
    vi.stubGlobal("fetch", (url: string, init?: RequestInit) =>
      url.startsWith("/api/accounts?") && (init?.method ?? "GET") === "GET"
        ? // The lookup asks for archived accounts too and matches regardless of case, like the backend.
          Promise.resolve(answer([{ id: 7, name: "EVERYDAY", archived: true }]))
        : fetchStub(url, init),
    );
    renderWizard();
    await walkToTheLevyStep();
    fireEvent.click(screen.getByRole("button", { name: "Finish" }));
    await screen.findByRole("heading", { name: "Start page" });

    expect(
      calls.filter(
        (call) => call.method === "POST" && call.url === "/api/accounts",
      ),
    ).toEqual([]);
  });

  it("refuses a balance that is not a plain amount", async () => {
    /** Oracle: spec-anchored — ARCHITECTURE "Money": no float ever carries an amount. */
    stubApi([]);
    renderWizard();
    await leaveTheLanguageStep();
    fireEvent.change(await screen.findByLabelText("Name"), {
      target: { value: "Everyday" },
    });
    fireEvent.change(screen.getByLabelText("Balance today"), {
      target: { value: "1.200,50" },
    });
    expect(
      screen.getByRole("button", { name: "Continue" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("refuses a levy rate outside 0 to 90", async () => {
    /** Oracle: spec-anchored — /api/levy/settings contract: at most 9000 basis points. */
    stubApi([]);
    renderWizard();
    await walkToTheLevyStep();
    fireEvent.change(screen.getByLabelText("Set aside (%)"), {
      target: { value: "120" },
    });
    expect(
      screen.getByRole("button", { name: "Finish" }).hasAttribute("disabled"),
    ).toBe(true);
  });

  it("says so when there is nothing left to set up", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK7: the screen is reachable by its URL. */
    stubApi([], false);
    renderWizard();
    expect(
      await screen.findByRole("heading", { name: "Everything is set up" }),
    ).toBeTruthy();
  });
});

function Harness() {
  useFirstRunRedirect();
  return <h1>Some screen</h1>;
}

function renderShell() {
  const router = createMemoryRouter(
    [
      { path: "/", element: <Harness /> },
      { path: "/first-run", element: <h1>Wizard</h1> },
    ],
    { initialEntries: ["/"] },
  );
  return render(
    <I18nProvider i18n={i18n}>
      <RouterProvider router={router} />
    </I18nProvider>,
  );
}

describe("the first-run redirect", () => {
  it("sends a first-time user to the wizard", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK7 / UC NF10: the first start leads through it. */
    stubApi([]);
    renderShell();
    expect(await screen.findByRole("heading", { name: "Wizard" })).toBeTruthy();
  });

  it("leaves a configured user alone", async () => {
    /** Oracle: spec-anchored — SPEC-T-2026-CU-9130-027 AK7: no redirect once the setup is done. */
    const calls: Call[] = [];
    stubApi(calls, false);
    renderShell();
    await waitFor(() => expect(calls.length).toBeGreaterThan(0));
    expect(screen.getByRole("heading", { name: "Some screen" })).toBeTruthy();
  });
});
