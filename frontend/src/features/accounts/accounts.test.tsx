// @vitest-environment jsdom
/**
 * Accounts feature (task -007, UC-ZF-001): money input without floats, and the first-run panel
 * that offers the default category set for editing before adoption (AK3). The API is a fake
 * `fetch`; every value is synthetic.
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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { centsToInput, parseEuroInput } from "./accountsApi";
import { FirstRunPanel } from "./FirstRunPanel";

vi.mock("../../i18n", () => ({ useLocale: () => ({ locale: "de" }) }));

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("euro input", () => {
  it("turns typed amounts into integer cents", () => {
    // Oracle: spec-anchored — ARCHITECTURE "Money": integer cents, no floats; hand-computed.
    expect(parseEuroInput("1000")).toBe(100_000);
    expect(parseEuroInput("-200")).toBe(-20_000);
    expect(parseEuroInput("12,5")).toBe(1_250);
    expect(parseEuroInput(" 0.07 ")).toBe(7);
    expect(parseEuroInput("-0")).toBe(0);
  });

  it("refuses what is not a plain amount", () => {
    // Oracle: spec-anchored — accountsApi.parseEuroInput contract: thousands separators, three
    // decimals, letters and empty input are refused, not guessed.
    for (const text of ["", "1.234,56", "1.005", "12€", "abc", "--1"]) {
      expect(parseEuroInput(text)).toBeNull();
    }
  });

  it("formats cents for editing and parses them back", () => {
    // Oracle: spec-anchored — round trip of the edit form; "," for German.
    expect(centsToInput(-123_456, "de")).toBe("-1234,56");
    expect(centsToInput(5, "en")).toBe("0.05");
    expect(parseEuroInput(centsToInput(-123_456, "de"))).toBe(-123_456);
  });
});

describe("first-run panel", () => {
  it("sends the edited default set", async () => {
    // Oracle: spec-anchored — UC-ZF-001 AK3: the default set is offered in the UI language and can
    // be edited before adoption; what is sent is exactly the edited list.
    const requests: { url: string; method: string; body?: string }[] = [];
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({
        url,
        method: init?.method ?? "GET",
        body: init?.body as string | undefined,
      });
      if (url.startsWith("/api/accounts/setup?")) {
        return Response.json({
          offer_default_set: true,
          language: "de",
          default_categories: [
            { name: "Wohnen", kind: "expense", area: "private" },
            { name: "Lebensmittel", kind: "expense", area: "private" },
            { name: "Honorar", kind: "income", area: "business" },
          ],
        });
      }
      return Response.json([], { status: 201 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const onDone = vi.fn(async () => {});

    render(
      <I18nProvider i18n={i18n}>
        <FirstRunPanel onDone={onDone} onError={vi.fn()} />
      </I18nProvider>,
    );

    const names = await screen.findAllByRole("textbox", { name: "Name" });
    expect(names.map((input) => (input as HTMLInputElement).value)).toEqual([
      "Wohnen",
      "Lebensmittel",
      "Honorar",
    ]);
    fireEvent.change(names[0] as HTMLElement, { target: { value: "Miete" } });
    fireEvent.click(
      screen.getAllByRole("button", { name: "Remove" })[1] as HTMLElement,
    );
    fireEvent.click(screen.getByRole("button", { name: "Adopt categories" }));

    await waitFor(() => expect(onDone).toHaveBeenCalled());
    expect(requests[0]?.url).toBe("/api/accounts/setup?language=de");
    const posted = requests.find((r) => r.method === "POST");
    expect(JSON.parse(posted?.body ?? "{}")).toEqual({
      categories: [
        { name: "Miete", kind: "expense", area: "private" },
        { name: "Honorar", kind: "income", area: "business" },
      ],
    });
    expect(screen.queryByRole("region")).toBeNull();
  });

  it("stays hidden when the set is not offered", async () => {
    // Oracle: spec-anchored — UC-ZF-001 AK3: offered at the first start only.
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({
          offer_default_set: false,
          language: "de",
          default_categories: [],
        }),
      ),
    );

    const { container } = render(
      <I18nProvider i18n={i18n}>
        <FirstRunPanel onDone={vi.fn()} onError={vi.fn()} />
      </I18nProvider>,
    );

    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });
});
