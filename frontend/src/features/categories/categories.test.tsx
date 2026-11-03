// @vitest-environment jsdom
/**
 * Categories screen (T-2026-HAR-9130-032): its own menu item under Settings directly after
 * Accounts, the category management on /categories, and the link from the Accounts page. The API
 * is a fake `fetch`; every value is synthetic.
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
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { features } from "../../app/features";
import { groupNav } from "../../app/Layout";
import { activateLocale } from "../../i18n/locale";
import type { Category } from "../accounts/accountsApi";
import { AccountsPage } from "../accounts/AccountsPage";
import { CategoriesPage } from "./CategoriesPage";
import { feature } from "./route";

vi.mock("../../i18n", () => ({ useLocale: () => ({ locale: "en" }) }));

const HOUSING: Category = {
  id: 1,
  name: "Housing",
  kind: "expense",
  area: "private",
  parent_id: null,
  booking_count: 0,
};

interface Request {
  url: string;
  method: string;
  body?: string;
}

/**
 * Fake backend: the given categories, a created category echoed back; the first-run offer of one
 * default category only when `offer` is true.
 */
function stubApi(categories: Category[], offer = false): Request[] {
  const requests: Request[] = [];
  let stored = [...categories];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      requests.push({ url, method, body: init?.body as string | undefined });
      if (url.startsWith("/api/accounts/setup")) {
        return Response.json({
          offer_default_set: offer,
          language: "en",
          default_categories: offer
            ? [{ name: "Housing", kind: "expense", area: "private" }]
            : [],
        });
      }
      if (url === "/api/categories" && method === "POST") {
        const created = {
          ...JSON.parse(String(init?.body)),
          id: stored.length + 1,
          booking_count: 0,
        } as Category;
        stored = [...stored, created];
        return Response.json(created, { status: 201 });
      }
      if (url === "/api/categories") return Response.json(stored);
      return Response.json([]);
    }),
  );
  return requests;
}

function renderPage(node: ReactNode) {
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

describe("categories menu item", () => {
  it("is a settings menu item directly after Accounts", () => {
    // Oracle: spec-anchored — task AK1: path `categories`, group `settings`, order directly after
    // Accounts (20).
    expect(feature.path).toBe("categories");
    expect(feature.nav?.group).toBe("settings");
    const settings = groupNav(features).find((g) => g.group === "settings");
    const paths = settings?.items.map((f) => f.path) ?? [];
    expect(paths.indexOf("categories")).toBe(paths.indexOf("accounts") + 1);
  });

  it("is labelled Kategorien in German", async () => {
    // Oracle: spec-anchored — task AK1: nav label msg`Categories`, de "Kategorien".
    await act(() => activateLocale("de"));
    const label = feature.nav?.label;
    expect(typeof label === "string" ? label : i18n._(label!)).toBe(
      "Kategorien",
    );
  });
});

describe("categories page", () => {
  it("renders the categories and creates one", async () => {
    // Oracle: spec-anchored — task AK2: the page shows the category management; creating a
    // category sends the form to POST /api/categories and the list shows it.
    const requests = stubApi([HOUSING]);
    renderPage(<CategoriesPage />);

    expect(
      await screen.findByRole("heading", { level: 1, name: "Categories" }),
    ).toBeTruthy();
    const table = await screen.findByRole("table");
    expect(within(table).getByText("Housing")).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Fuel" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Create category" }));

    await waitFor(() =>
      expect(within(screen.getByRole("table")).getByText("Fuel")).toBeTruthy(),
    );
    const posted = requests.find((r) => r.method === "POST");
    expect(posted?.url).toBe("/api/categories");
    expect(JSON.parse(posted?.body ?? "{}")).toEqual({
      name: "Fuel",
      kind: "expense",
      area: null,
      parent_id: null,
    });
  });
});

describe("accounts page", () => {
  it("links to the categories page instead of listing the categories", async () => {
    // Oracle: spec-anchored — task AK3: no second category list on Accounts, a link to
    // /categories in its place.
    const requests = stubApi([HOUSING]);
    renderPage(<AccountsPage />);

    const link = await screen.findByRole("link", { name: "Categories" });
    expect(link.getAttribute("href")).toBe("/categories");
    await waitFor(() =>
      expect(requests.some((r) => r.url.startsWith("/api/accounts"))).toBe(
        true,
      ),
    );
    expect(screen.queryByText("Housing")).toBeNull();
    expect(requests.some((r) => r.url === "/api/categories")).toBe(false);
  });

  it("opens the categories page after the default set is adopted", async () => {
    // Oracle: spec-anchored — task AK3 (review finding): the first-run offer stays on Accounts,
    // and after the adoption the user sees where the categories went.
    stubApi([], true);
    render(
      <I18nProvider i18n={i18n}>
        <MemoryRouter initialEntries={["/accounts"]}>
          <Routes>
            <Route path="/accounts" element={<AccountsPage />} />
            <Route path="/categories" element={<p>categories screen</p>} />
          </Routes>
        </MemoryRouter>
      </I18nProvider>,
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Adopt categories" }),
    );

    expect(await screen.findByText("categories screen")).toBeTruthy();
  });
});
