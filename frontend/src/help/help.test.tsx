// @vitest-environment jsdom
/**
 * Help framework (task -005, SPEC AK21, UC NF11): chapter parsing and lookup, search, the language
 * switch of help panel and manual view. Chapters here are synthetic; one test reads the real
 * docs/manual tree through the embedded `manual`.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { activateLocale } from "../i18n/locale";
import { Handbook } from "./Handbook";
import { HelpButton } from "./HelpButton";
import {
  chapterForPath,
  chapterLinkTarget,
  manual,
  manualFromSources,
  renderChapter,
  resolveImage,
} from "./manual";

const synthetic = manualFromSources({
  "/docs/manual/en/index.md":
    "---\norder: 0\n---\n\n# Getting started\n\nStart here. See [Pots](pots.md).",
  "/docs/manual/de/index.md":
    "---\norder: 0\n---\n\n# Erste Schritte\n\nHier beginnen. Siehe [Töpfe](pots.md).",
  "/docs/manual/en/pots.md":
    "---\norder: 20\n---\n\n# Savings pots\n\nA pot reserves money for a goal.",
  "/docs/manual/de/pots.md":
    "---\norder: 20\n---\n\n# Spartöpfe\n\nEin Topf reserviert Geld für ein Ziel. Übersicht der Töpfe.",
  "/docs/manual/en/accounts.md":
    "---\norder: 10\n---\n\n# Accounts\n\nOpening balance per account.",
});

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(cleanup);

describe("chapter lookup", () => {
  it("maps a route to its chapter, the start page to index", () => {
    // Oracle: spec-anchored — task -005: chapter = first path segment, "" -> index (same rule as
    // tools/check_manual_coverage.py chapter_for_path).
    expect(chapterForPath("/")).toBe("index");
    expect(chapterForPath("/backup")).toBe("backup");
    expect(chapterForPath("/accounts/7")).toBe("accounts");
  });

  it("falls back to English only where a German chapter is missing", () => {
    // Oracle: spec-anchored — orchestrator contract 2026-09-19: German falls back to English only
    // if the German chapter is missing.
    expect(synthetic.chapter("de", "pots")?.title).toBe("Spartöpfe");
    expect(synthetic.chapter("de", "accounts")).toMatchObject({
      lang: "en",
      title: "Accounts",
    });
    expect(synthetic.chapter("de", "missing")).toBeUndefined();
  });

  it("orders index first, then by front-matter order", () => {
    // Oracle: spec-anchored — chapter format (ARCHITECTURE.md "Help and manual"): index first, `order` ascending.
    expect(synthetic.chapters("en").map((c) => c.slug)).toEqual([
      "index",
      "accounts",
      "pots",
    ]);
  });

  it("renders no raw HTML and resolves chapter links", () => {
    // Oracle: spec-anchored — task -005: markdown-it with html: false, no raw HTML into the DOM.
    const [chapter] = manualFromSources({
      "/m/en/x.md":
        '# X\n\n<script>alert(1)</script> <img src=x onerror="alert(1)">\n\n[bad](javascript:alert(1))',
    }).chapters("en");
    const html = renderChapter(chapter!);
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain('href="javascript:');
    expect(chapterLinkTarget("settings.md")).toBe("settings");
    expect(chapterLinkTarget("./backup.md#restore")).toBe("backup");
    expect(chapterLinkTarget("https://example.org/a.md")).toBeNull();
  });

  it("shows a bundled screenshot as a linked image and an unknown one as its description", () => {
    // Oracle: spec-anchored — task -028 AK12 / UC NF11: chapters embed screenshots from
    // docs/assets/screenshots, offline; a reference that is not bundled must not become a broken image.
    const urls = {
      "/repo/docs/assets/screenshots/en/entry.png": "/assets/entry-abc.png",
    };
    expect(resolveImage("../../assets/screenshots/en/entry.png", urls)).toBe(
      "/assets/entry-abc.png",
    );
    expect(
      resolveImage("../../assets/screenshots/en/nope.png", urls),
    ).toBeNull();
    expect(resolveImage("photo.png", urls)).toBeNull();

    const [chapter] = manualFromSources({
      "/m/en/x.md":
        "# X\n\n![Quick entry form](../../assets/screenshots/en/entry.png)\n\n![Missing](../../assets/screenshots/en/nope.png)",
    }).chapters("en");
    const html = renderChapter(chapter!);
    expect(html).toMatch(
      /<a class="manual-figure" href="[^"]+\.png"[^>]*><img src="[^"]+\.png" alt="Quick entry form"/,
    );
    expect(html).toContain("<em>Missing</em>");
  });
});

describe("search", () => {
  it("finds chapters containing every word, ignoring case and accents", () => {
    // Oracle: spec-anchored — UC NF11 "durchsuchbar"; chapter manual.md: "ubersicht" finds "Übersicht".
    expect(
      synthetic.search("de", "ubersicht").map((h) => h.chapter.slug),
    ).toEqual(["pots"]);
    expect(
      synthetic.search("en", "RESERVES goal").map((h) => h.chapter.slug),
    ).toEqual(["pots"]);
    expect(synthetic.search("en", "reserves nowhere")).toEqual([]);
    expect(synthetic.search("en", "   ")).toEqual([]);
    expect(synthetic.search("en", "goal")[0]?.snippet).toContain(
      "reserves money for a goal",
    );
  });
});

describe("embedded manual", () => {
  it("embeds every chapter of docs/manual in both languages", () => {
    // Oracle: spec-anchored — SPEC AK21 "zweisprachig, offline eingebettet"; the chapters exist in the repo.
    for (const slug of ["index", "settings", "backup", "manual"]) {
      expect(manual.chapter("en", slug)?.lang).toBe("en");
      expect(manual.chapter("de", slug)?.lang).toBe("de");
    }
    expect(manual.chapter("de", "backup")?.title).toBe(
      "Sichern und Wiederherstellen",
    );
  });
});

describe("language switch", () => {
  function renderAt(path: string, element: React.ReactNode) {
    const router = createMemoryRouter([{ path: "*", element }], {
      initialEntries: [path],
    });
    return render(
      <I18nProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nProvider>,
    );
  }

  it("switches the open help panel to German without a reload", async () => {
    // Oracle: spec-anchored — orchestrator contract 2026-09-19: switching the language switches the
    // help panel immediately (no reload), chapter content included.
    renderAt("/pots", <HelpButton manual={synthetic} />);
    fireEvent.click(screen.getByRole("button", { name: "Help for this page" }));
    expect(screen.getByRole("heading", { name: "Savings pots" })).toBeTruthy();

    await act(() => activateLocale("de"));

    expect(screen.getByRole("heading", { name: "Spartöpfe" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Savings pots" })).toBeNull();
    // The panel's own labels come from the German catalog.
    expect(
      screen.getByRole("button", { name: "Hilfe zu dieser Seite" }),
    ).toBeTruthy();
  });

  it("switches the manual view (chapter, navigation, search) to German without a reload", async () => {
    // Oracle: spec-anchored — orchestrator contract 2026-09-19: manual view content, navigation
    // and search follow the language switch immediately.
    renderAt(
      "/manual",
      <Handbook manual={synthetic} slug="pots" onSelect={() => undefined} />,
    );
    const nav = () => screen.getByRole("navigation");
    expect(
      screen.getByRole("heading", { level: 1, name: "Savings pots" }),
    ).toBeTruthy();
    expect(nav().textContent).toContain("Getting started");

    await act(() => activateLocale("de"));

    expect(
      screen.getByRole("heading", { level: 1, name: "Spartöpfe" }),
    ).toBeTruthy();
    expect(nav().textContent).toContain("Erste Schritte");
    expect(nav().textContent).not.toContain("Getting started");
    fireEvent.change(screen.getByRole("searchbox"), {
      target: { value: "ziel" },
    });
    expect(nav().textContent).toContain("Spartöpfe");
    expect(nav().textContent).toContain(
      "Ein Topf reserviert Geld für ein Ziel",
    );
  });
});
