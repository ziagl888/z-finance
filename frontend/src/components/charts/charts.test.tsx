// @vitest-environment jsdom
/**
 * Chart component (task -023): the value axis, the bar cap, the flagged months, the table twin and
 * the keyboard readout. Every value is synthetic.
 */
import { i18n } from "@lingui/core";
import { I18nProvider } from "@lingui/react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { activateLocale } from "../../i18n/locale";
import { Chart, type ChartProps } from "./Chart";
import { SERIES_SLOTS, otherColor, seriesColor } from "./palette";
import { SplitBar } from "./SplitBar";
import { MAX_BAR_PX, monthLong, monthShort, niceAxis, niceStep } from "./scale";

beforeEach(async () => {
  await act(() => activateLocale("en"));
});

afterEach(cleanup);

const CATEGORIES = [
  { key: "2026-08", label: "August 2026", tick: "Aug", tickSub: "2026" },
  { key: "2026-09", label: "September 2026", tick: "Sep" },
];

function props(changes: Partial<ChartProps> = {}): ChartProps {
  return {
    title: "Test income",
    categories: CATEGORIES,
    bars: [
      {
        key: "booked",
        label: "Booked",
        color: seriesColor(0),
        values: [300_000, 500_000],
      },
    ],
    lines: [
      {
        key: "need",
        label: "Need",
        color: "var(--ink)",
        values: [461_538, 461_538],
      },
    ],
    flags: { label: "Below need", values: [true, false] },
    yLabel: "Amount (EUR)",
    xLabel: "Month",
    ...changes,
  };
}

const renderChart = (chart: ChartProps) =>
  render(
    <I18nProvider i18n={i18n}>
      <Chart {...chart} />
    </I18nProvider>,
  );

describe("value axis", () => {
  it("starts at the 0 baseline and ends on a round tick above the data", () => {
    // Oracle: spec-anchored — dataviz method (bars grow from 0; round ticks); values by hand:
    // span 500,000 cents / 4 intervals = 125,000 → next step 1/2/5·10^k = 200,000.
    const axis = niceAxis(0, 500_000);
    expect(axis.ticks).toEqual([0, 200_000, 400_000, 600_000]);
    expect(axis.min).toBe(0);
    expect(axis.max).toBe(600_000);
  });

  it("keeps 0 inside the domain when a line goes negative and gives an empty chart an axis", () => {
    // Oracle: spec-anchored — dataviz method (single baseline; never an axis that starts elsewhere).
    expect(niceAxis(-100_000, 100_000).ticks).toContain(0);
    expect(niceAxis(-100_000, 100_000).min).toBeLessThan(0);
    expect(niceAxis(0, 0).ticks).toEqual([0, 500, 1000]);
  });

  it("uses only 1, 2 or 5 times a power of ten as a step", () => {
    // Oracle: spec-anchored — "round numbers" rule of the dataviz method; hand-picked inputs.
    expect(niceStep(0.3)).toBe(0.5);
    expect(niceStep(1)).toBe(1);
    expect(niceStep(1.01)).toBe(2);
    expect(niceStep(4999)).toBe(5000);
    expect(niceStep(5001)).toBe(10_000);
  });
});

describe("months", () => {
  it("names months in the UI locale, in UTC", () => {
    // Oracle: spec-anchored — UC NF6 (bilingual chart labels); "2026-09" is September 2026 in both languages.
    expect(monthLong("2026-09", "en")).toBe("September 2026");
    expect(monthLong("2026-09", "de")).toBe("September 2026");
    expect(monthShort("2026-01", "en")).toBe("Jan");
    expect(monthShort("2026-03", "de")).toMatch(/^Mär/);
  });
});

describe("palette", () => {
  it("hands out seven hues in fixed order and refuses more", () => {
    // Oracle: spec-anchored — dataviz method (fixed order, never cycled; a ninth series folds into Other).
    expect(seriesColor(0)).toBe("var(--chart-1)");
    expect(seriesColor(SERIES_SLOTS - 1)).toBe(`var(--chart-${SERIES_SLOTS})`);
    expect(() => seriesColor(SERIES_SLOTS)).toThrow(RangeError);
    expect(otherColor()).toBe("var(--chart-other)");
  });
});

describe("chart", () => {
  it("caps a bar at 24 px and stacks segments from the baseline", () => {
    // Oracle: spec-anchored — dataviz mark spec (bars ≤ 24 px thick); two categories give a wide band.
    const { container } = renderChart(
      props({
        bars: [
          { key: "a", label: "A", color: seriesColor(0), values: [100_000, 0] },
          { key: "b", label: "B", color: seriesColor(1), values: [100_000, 0] },
        ],
      }),
    );
    const lower = container.querySelector('rect[data-bar="a"]');
    expect(Number(lower?.getAttribute("width"))).toBe(MAX_BAR_PX);
    // the top segment carries the rounded data end (a path), the lower one is square
    expect(container.querySelector('path[data-bar="b"]')).not.toBeNull();
    // a category without a value draws no segment
    expect(container.querySelectorAll("[data-bar]").length).toBe(2);
  });

  it("marks only the flagged month (AK4: 3,000 € under a need of 4,615 €, 5,000 € not)", () => {
    // Oracle: spec-anchored — UC-ZF-015 AK4 (month with 3,000 € marked at need 4,615 €, 5,000 € not).
    const { container } = renderChart(props());
    expect(container.querySelectorAll("[data-flag]").length).toBe(1);
    // the legend names the marker, so colour is not the only cue
    expect(screen.getByText("Below need")).toBeTruthy();
  });

  it("lists every value in the table twin", () => {
    // Oracle: spec-anchored — dataviz method (every chart has a table twin with all values); amounts by hand.
    renderChart(props());
    fireEvent.click(screen.getByRole("button", { name: "Show as table" }));
    const table = screen.getByRole("table");
    expect(within(table).getByText("August 2026")).toBeTruthy();
    expect(within(table).getByText("€3,000.00")).toBeTruthy();
    expect(within(table).getByText("€5,000.00")).toBeTruthy();
    expect(within(table).getAllByText("€4,615.38").length).toBe(2);
    expect(within(table).getByText("Yes")).toBeTruthy();
    expect(within(table).getByText("No")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Show chart" }));
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("reads month by month with the arrow keys", () => {
    // Oracle: spec-anchored — dataviz interaction rule (keyboard focus shows what hover shows); values by hand.
    renderChart(props());
    const surface = screen.getByRole("application");
    fireEvent.focus(surface);
    expect(screen.getAllByText(/August 2026/).length).toBeGreaterThan(0);
    fireEvent.keyDown(surface, { key: "ArrowRight" });
    const readout = document.querySelector(".chart-tip");
    expect(readout?.textContent).toContain("September 2026");
    expect(readout?.textContent).toContain("€5,000.00");
    expect(readout?.textContent).toContain("Booked");
    fireEvent.keyDown(surface, { key: "Escape" });
    expect(document.querySelector(".chart-tip")).toBeNull();
  });

  it("shows the empty text instead of a plot without categories", () => {
    // Oracle: spec-anchored — dataviz component list (empty state); a chart with no months has nothing to draw.
    renderChart(props({ categories: [], empty: "Nothing to show." }));
    expect(screen.getByText("Nothing to show.")).toBeTruthy();
    expect(screen.queryByRole("application")).toBeNull();
  });
});

describe("day selection, marks and dashed lines (task -024)", () => {
  const DAYS = Array.from({ length: 30 }, (_, i) => ({
    key: `2026-10-${String(i + 1).padStart(2, "0")}`,
    label: `Day ${i + 1}`,
    tick: i === 0 ? "Oct" : "",
  }));
  const curve = (extra: Partial<ChartProps> = {}): ChartProps => ({
    title: "Test curve",
    categories: DAYS,
    lines: [
      {
        key: "available",
        label: "Available",
        color: seriesColor(0),
        values: DAYS.map((_, i) => 200_000 - i * 1_000),
      },
      {
        key: "reserve",
        label: "Reserve",
        color: "var(--ink)",
        dashed: true,
        values: DAYS.map(() => 100_000),
      },
    ],
    yLabel: "Amount (EUR)",
    xLabel: "Day",
    ...extra,
  });

  /** A plot 300 px wide: one day is 10 px, so clientX = 10 × index + 5 hits day `index`. */
  function surfaceOf(chart: ChartProps) {
    renderChart(chart);
    const surface = screen.getByRole("application");
    surface.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 300, height: 100 }) as DOMRect;
    return surface;
  }

  it("reports the clicked day and marks it", () => {
    // Oracle: spec-anchored — UC-ZF-006 step 4 (click on a curve point chooses that day); day 12 = index 11 by hand from a 10 px band.
    const onSelect = vi.fn();
    const surface = surfaceOf(curve({ onSelect, selected: 11 }));
    fireEvent.click(surface, { clientX: 115 });
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(11);
    expect(document.querySelector(".chart-selected")).not.toBeNull();
  });

  it("chooses with Enter after the arrow keys, a week with Shift, and reports nothing without a callback", () => {
    // Oracle: spec-anchored — WCAG 2.1.1 (everything a mouse can do works by keyboard); 0 + 7 + 1 = index 8.
    const onSelect = vi.fn();
    const surface = surfaceOf(curve({ onSelect }));
    fireEvent.focus(surface);
    fireEvent.keyDown(surface, { key: "ArrowRight", shiftKey: true });
    fireEvent.keyDown(surface, { key: "ArrowRight" });
    expect(onSelect).not.toHaveBeenCalled();
    fireEvent.keyDown(surface, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledExactlyOnceWith(8);
    cleanup();
    const plain = surfaceOf(curve());
    fireEvent.click(plain, { clientX: 15 });
    fireEvent.keyDown(plain, { key: "Enter" });
    expect(document.querySelector(".chart-selected")).toBeNull();
  });

  it("draws a dashed line with a dashed legend key, a ringed mark with its label and a custom reading hint", () => {
    // Oracle: spec-anchored — task -024 brief (reserve as horizontal line, minimum marked) + dataviz method (colour is never the only cue).
    renderChart(
      curve({
        marks: [
          { index: 29, lineKey: "available", label: "Lowest: €1,710.00" },
        ],
        readHint: "Choose a day",
      }),
    );
    const dashed = document.querySelector(
      '[data-line="reserve"] path:last-of-type',
    );
    expect(dashed?.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(
      document
        .querySelector('[data-line="available"] path:last-of-type')
        ?.getAttribute("stroke-dasharray"),
    ).toBeNull();
    expect(
      document
        .querySelectorAll(".chart-legend .chart-swatch-line")[1]
        ?.getAttribute("style"),
    ).toContain("repeating-linear-gradient");
    expect(document.querySelector("[data-mark]")?.textContent).toBe(
      "Lowest: €1,710.00",
    );
    expect(screen.getByRole("application").getAttribute("aria-label")).toBe(
      "Choose a day",
    );
  });

  it("leaves a mark out when its line or point does not exist", () => {
    // Oracle: spec-anchored — component contract: a mark on an unknown line draws nothing instead of a guess.
    renderChart(curve({ marks: [{ index: 3, lineKey: "nope", label: "X" }] }));
    expect(document.querySelector("[data-mark]")).toBeNull();
  });
});

describe("split bar", () => {
  it("cuts the bar by share and leaves zero blocks out", () => {
    // Oracle: spec-anchored — UC-ZF-015 4a (split of the required revenue by block); shares by hand:
    // 300,000 of 461,538 cents ≈ 65 %, the levy 161,538 ≈ 35 %, the zero block is not drawn.
    const { container } = render(
      <I18nProvider i18n={i18n}>
        <SplitBar
          segments={[
            {
              key: "costs",
              label: "Costs",
              cents: 300_000,
              color: seriesColor(0),
            },
            { key: "none", label: "None", cents: 0, color: seriesColor(1) },
            {
              key: "levy",
              label: "Levy",
              cents: 161_538,
              color: seriesColor(2),
            },
          ]}
        />
      </I18nProvider>,
    );
    const segments = container.querySelectorAll(".chart-split-bar > li");
    expect(segments.length).toBe(2);
    const basis = Array.from(segments).map((segment) =>
      parseFloat((segment as HTMLElement).style.flexBasis),
    );
    expect(Math.round(basis[0] ?? 0)).toBe(65);
    expect(Math.round(basis[1] ?? 0)).toBe(35);
  });
});
