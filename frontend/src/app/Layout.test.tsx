// @vitest-environment jsdom
/**
 * App shell (task -032, SPEC AK2): sidebar groups from the feature registry, active link, header
 * breadcrumb and the language switch. Routes and the API are synthetic.
 */
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LocaleProvider } from "../i18n";
import { activateLocale } from "../i18n/locale";
import type { FeatureRoute } from "./features";
import {
  groupNav,
  HEALTH_POLL_INTERVAL_MS,
  HEALTH_RETRY_DELAYS_MS,
  Layout,
} from "./Layout";

const Page = () => <h1>Test page</h1>;

const ROUTES: FeatureRoute[] = [
  {
    path: "b",
    Component: Page,
    nav: { label: "Test B", order: 20, group: "plan" },
  },
  {
    path: "a",
    Component: Page,
    nav: { label: "Test A", order: 10, group: "plan" },
  },
  {
    path: "c",
    Component: Page,
    nav: { label: "Test C", order: 5, group: "capture" },
  },
  { path: "d", Component: Page, nav: { label: "Test D", order: 1 } },
  { path: "hidden", Component: Page },
];

/** Fake backend: health and the stored language (none). */
function stubApi(
  put = vi.fn(() => ({ language: "de", supported: ["en", "de"] })),
) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      let body: unknown;
      if (path === "/api/health") {
        body = { status: "ok", version: "0.0.0", schema_version: 1 };
      } else if (path === "/api/i18n/language") {
        body =
          init?.method === "PUT"
            ? put()
            : { language: null, supported: ["en", "de"] };
      }
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
  return put;
}

function renderShell(path: string) {
  const sorted = [...ROUTES].sort(
    (x, y) => (x.nav?.order ?? Infinity) - (y.nav?.order ?? Infinity),
  );
  const router = createMemoryRouter(
    [
      {
        path: "/",
        element: <Layout routes={sorted} />,
        children: sorted.map((f) => ({ path: f.path, Component: f.Component })),
      },
    ],
    { initialEntries: [path] },
  );
  return render(
    <LocaleProvider>
      <RouterProvider router={router} />
    </LocaleProvider>,
  );
}

beforeEach(async () => {
  window.localStorage.clear();
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("groupNav", () => {
  it("orders groups capture, plan, analyse, settings and drops empty ones", () => {
    /** Oracle: spec-anchored — SPEC -032 AK2 (groups Capture / Plan / Analyse / Settings; default settings). */
    const groups = groupNav(ROUTES);
    expect(groups.map((g) => g.group)).toEqual(["capture", "plan", "settings"]);
    expect(groups[1]!.items.map((f) => f.path)).toEqual(["b", "a"]);
    expect(groups[2]!.items.map((f) => f.path)).toEqual(["d"]);
  });
});

describe("Layout health check", () => {
  /** Backend that refuses the first `failures` health requests, then answers. */
  function stubSlowBackend(failures: number) {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path !== "/api/health") return Response.json({});
        calls += 1;
        if (calls <= failures) throw new TypeError("connection refused");
        return Response.json({
          status: "ok",
          version: "1.2.3",
          schema_version: 7,
        });
      }),
    );
    return () => calls;
  }

  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
  afterEach(() => vi.useRealTimers());

  it("recovers by itself when the backend starts within the retry window", async () => {
    /** Oracle: spec-anchored — task -013 (3): retry with backoff 1/2/4 s before reporting "down". */
    const calls = stubSlowBackend(2);
    renderShell("/a");
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText("Connecting…")).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(HEALTH_RETRY_DELAYS_MS[0]!));
    await act(() => vi.advanceTimersByTimeAsync(HEALTH_RETRY_DELAYS_MS[1]!));
    expect(screen.getByText("v1.2.3 · schema 7")).toBeTruthy();
    expect(calls()).toBe(3);
    expect(screen.queryByText("Backend not reachable")).toBeNull();
  });

  it("reports the backend as down after the retries and keeps polling until it answers", async () => {
    /** Oracle: spec-anchored — task -013 (3): after the retries, poll every 30 s while red. */
    const calls = stubSlowBackend(4);
    renderShell("/a");
    await act(() => vi.advanceTimersByTimeAsync(0));
    for (const delay of HEALTH_RETRY_DELAYS_MS) {
      await act(() => vi.advanceTimersByTimeAsync(delay));
    }
    expect(calls()).toBe(4);
    expect(screen.getByText("Backend not reachable")).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(HEALTH_POLL_INTERVAL_MS));
    expect(screen.getByText("v1.2.3 · schema 7")).toBeTruthy();
    expect(calls()).toBe(5);
  });

  it("stops polling when the shell unmounts", async () => {
    /** Oracle: spec-anchored — task -013 (3): no timer outlives the component. */
    const calls = stubSlowBackend(100);
    const { unmount } = renderShell("/a");
    await act(() => vi.advanceTimersByTimeAsync(0));
    unmount();
    await act(() => vi.advanceTimersByTimeAsync(HEALTH_POLL_INTERVAL_MS * 2));
    expect(calls()).toBe(1);
  });
});

describe("Layout", () => {
  it("shows the grouped sidebar, the active page and the breadcrumb", async () => {
    /** Oracle: spec-anchored — SPEC -032 AK2 (sidebar groups, header with the current page). */
    stubApi();
    renderShell("/a");
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const plan = within(nav).getByRole("list", { name: "Planning" });
    expect(
      within(plan)
        .getAllByRole("link")
        .map((a) => a.textContent),
    ).toEqual(["Test A", "Test B"]);
    expect(within(nav).getByRole("list", { name: "Capture" })).toBeTruthy();
    expect(within(nav).getByRole("list", { name: "Settings" })).toBeTruthy();
    expect(within(nav).queryByRole("list", { name: "Analyse" })).toBeNull();
    expect(
      within(nav).getByRole("link", { name: "Test A" }).className,
    ).toContain("active");
    expect(document.querySelector(".shell-crumb")?.textContent).toBe(
      "Planning / Test A",
    );
    expect(await screen.findByText("v0.0.0 · schema 1")).toBeTruthy();
  });

  it("switches the language from the header", async () => {
    /** Oracle: spec-anchored — SPEC -032 AK2 (header with language switch) + ARCHITECTURE "Language setting" (PUT /api/i18n/language). */
    const put = stubApi();
    renderShell("/c");
    const select = await screen.findByRole("combobox", { name: "Language" });
    fireEvent.change(select, { target: { value: "de" } });
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("list", { name: "Planen" })).toBeTruthy();
  });
});
