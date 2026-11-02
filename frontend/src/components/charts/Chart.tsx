import { useLingui } from "@lingui/react/macro";
import {
  useId,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
} from "react";

import { formatCents } from "../../api/client";
import { Button, Table, cx } from "../../ui";
import "./charts.css";
import { MAX_BAR_PX, formatAxisCents, niceAxis } from "./scale";

/** One x position (a month): `label` for tooltip and table, `tick` (+ `tickSub`) on the axis. */
export interface ChartCategory {
  key: string;
  label: string;
  tick: string;
  /** Second axis line, e.g. the year under a January tick. */
  tickSub?: string;
  /** Extra line in tooltip and table, e.g. "Planned". */
  note?: string;
}

/** A stacked bar series; `values` are non-negative cents per category (negatives draw as 0). */
export interface ChartBar {
  key: string;
  label: string;
  /** Any CSS colour, normally `var(--chart-n)`. */
  color: string;
  values: number[];
  /** Fill opacity 0–1 (a "planned" twin of a booked series). Default 1. */
  opacity?: number;
}

/** A line series over the same categories; `null` leaves a gap. `area` adds a light wash. */
export interface ChartLine {
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
  area?: boolean;
  /** Dashed stroke (and legend key) for a reference such as a reserve; colour is never the only cue. */
  dashed?: boolean;
}

/** A labelled ring on one line at one category, e.g. the lowest point of a curve. */
export interface ChartMark {
  /** Index into `categories`. */
  index: number;
  /** `key` of the line the ring sits on. */
  lineKey: string;
  label: string;
}

/** Categories that need attention (e.g. income below the need): a marker, a band and a table column. */
export interface ChartFlags {
  label: string;
  values: boolean[];
}

export interface ChartProps {
  /** Visible heading; also the accessible name of the chart and the table caption. */
  title: string;
  /** One sentence under the heading: what the chart shows and how to read it. */
  description?: ReactNode;
  categories: ChartCategory[];
  bars?: ChartBar[];
  lines?: ChartLine[];
  flags?: ChartFlags;
  marks?: ChartMark[];
  /** Turns a click on the plot, or Enter on the focused plot, into "this category was chosen". */
  onSelect?: (index: number) => void;
  /** The chosen category: a vertical line marks it. */
  selected?: number | null;
  /** Accessible name of the plot surface; default: how to read each month with the arrow keys. */
  readHint?: string;
  /** Hairline before category `index` with a label (e.g. where the planned months start). */
  divider?: { index: number; label: string };
  /** Title of the value axis, including the unit ("Amount (EUR)"). */
  yLabel: string;
  /** Title of the category axis ("Month"). */
  xLabel: string;
  /** Height of the plot area in px, without the axis bands. Default 260. */
  plotHeight?: number;
  /** Data is being reloaded: the last render stays, dimmed. */
  busy?: boolean;
  /** Shown instead of the plot when there are no categories. */
  empty?: ReactNode;
  className?: string;
}

const WIDTH = 1000;
const MARGIN = { left: 88, right: 16, top: 46, bottom: 60 };
const GAP = 2; // surface gap between stacked segments (dataviz spacer)
const CORNER = 4; // rounded data end

/** Legend key of a line: solid, or dashes for a dashed line. */
function lineKeyBackground(line: ChartLine): string {
  return line.dashed
    ? `repeating-linear-gradient(90deg, ${line.color} 0 5px, transparent 5px 8px)`
    : line.color;
}

/** Path of a rectangle with the two upper corners rounded (the data end of a bar). */
function roundedTop(x: number, y: number, w: number, h: number): string {
  const r = Math.min(CORNER, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/**
 * Reusable chart (stacked bars and/or lines on one value axis, 0 as the baseline), drawn as SVG:
 * no library, no canvas, works offline and in tests. Marks follow the dataviz method: thin bars
 * with a rounded data end and 2 px gaps, 2 px lines, hairline grid, text only in text tokens, a
 * legend for two or more series, a tooltip on hover *and* keyboard focus (arrow keys, Home/End,
 * Escape), and a table twin that lists every value. Colours come from `--chart-n` and the design
 * tokens. Two measures of different scale belong into two charts, never onto a second axis.
 */
export function Chart({
  title,
  description,
  categories,
  bars = [],
  lines = [],
  flags,
  marks = [],
  onSelect,
  selected = null,
  readHint,
  divider,
  yLabel,
  xLabel,
  plotHeight = 260,
  busy,
  empty,
  className,
}: ChartProps) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const titleId = useId();
  const [active, setActive] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const count = categories.length;
  const money = (cents: number) => formatCents(cents, locale);

  if (count === 0) {
    return (
      <figure className={cx("chart", className)}>
        <figcaption>
          <h3 className="chart-title">{title}</h3>
        </figcaption>
        <p className="ui-hint">{empty}</p>
      </figure>
    );
  }

  // --- geometry ------------------------------------------------------------------------------
  const height = MARGIN.top + plotHeight + MARGIN.bottom;
  const plotWidth = WIDTH - MARGIN.left - MARGIN.right;
  const band = plotWidth / count;
  const barWidth = Math.min(MAX_BAR_PX, band * 0.6);

  const stackTotal = (i: number) =>
    bars.reduce((sum, bar) => sum + Math.max(0, bar.values[i] ?? 0), 0);
  const lineValues = lines.flatMap((line) =>
    line.values.filter((v): v is number => v !== null),
  );
  const totals = categories.map((_, i) => stackTotal(i));
  const axis = niceAxis(
    Math.min(0, ...lineValues),
    Math.max(0, ...totals, ...lineValues),
  );
  const yOf = (value: number) =>
    MARGIN.top + plotHeight * (1 - (value - axis.min) / (axis.max - axis.min));
  const xCenter = (i: number) => MARGIN.left + band * (i + 0.5);
  const baseline = yOf(0);

  // --- interaction ---------------------------------------------------------------------------
  function onPointerMove(event: PointerEvent<SVGRectElement>) {
    const index = indexAt(event);
    if (index !== null) setActive(index);
  }

  /** Category under the pointer of a mouse event on the plot surface (`null` when it has no width). */
  function indexAt(
    event: PointerEvent<SVGRectElement> | MouseEvent<SVGRectElement>,
  ) {
    const box = event.currentTarget.getBoundingClientRect();
    if (box.width === 0) return null;
    const index = Math.floor(((event.clientX - box.left) / box.width) * count);
    return Math.min(count - 1, Math.max(0, index));
  }

  function onClick(event: MouseEvent<SVGRectElement>) {
    const index = indexAt(event);
    if (onSelect && index !== null) {
      setActive(index);
      onSelect(index);
    }
  }

  function onKeyDown(event: KeyboardEvent<SVGRectElement>) {
    const at = active ?? 0;
    // Shift jumps a week of daily categories (a year has 365 of them).
    const step = event.shiftKey ? 7 : 1;
    const next =
      event.key === "ArrowRight"
        ? Math.min(count - 1, at + step)
        : event.key === "ArrowLeft"
          ? Math.max(0, at - step)
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? count - 1
              : null;
    if (next !== null) {
      event.preventDefault();
      setActive(next);
    } else if (event.key === "Escape") {
      setActive(null);
    } else if (onSelect && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      onSelect(at);
    }
  }

  // --- readout of the active category (tooltip and screen reader) ----------------------------
  const readout = (index: number) => {
    const rows = [
      ...[...bars].reverse().map((bar) => ({
        key: bar.key,
        label: bar.label,
        color: bar.color,
        opacity: bar.opacity,
        kind: "bar" as const,
        cents: Math.max(0, bar.values[index] ?? 0),
      })),
      ...lines.map((line) => ({
        key: line.key,
        label: line.label,
        color: line.color,
        opacity: 1,
        kind: "line" as const,
        cents: line.values[index] ?? null,
      })),
    ].filter(
      (row) => row.cents !== null && (row.kind === "line" || row.cents !== 0),
    );
    return rows;
  };

  const activeCategory = active === null ? null : (categories[active] ?? null);
  const activeSummary =
    active === null || !activeCategory
      ? ""
      : [
          activeCategory.label,
          activeCategory.note,
          ...readout(active).map(
            (row) => `${row.label}: ${money(row.cents ?? 0)}`,
          ),
          flags?.values[active] ? flags.label : null,
        ]
          .filter(Boolean)
          .join(", ");

  const showLegend = bars.length + lines.length + (flags ? 1 : 0) >= 2;

  return (
    <figure
      className={cx("chart", busy && "chart-busy", className)}
      aria-labelledby={titleId}
      aria-busy={busy || undefined}
    >
      <figcaption className="chart-head">
        <div>
          <h3 className="chart-title" id={titleId}>
            {title}
          </h3>
          {description && <p className="chart-description">{description}</p>}
        </div>
        <Button aria-pressed={asTable} onClick={() => setAsTable((on) => !on)}>
          {asTable ? t`Show chart` : t`Show as table`}
        </Button>
      </figcaption>

      {showLegend && (
        <ul className="chart-legend" aria-label={t`Legend`}>
          {bars.map((bar) => (
            <li key={bar.key}>
              <span
                className="chart-swatch chart-swatch-bar"
                style={{ background: bar.color, opacity: bar.opacity ?? 1 }}
              />
              {bar.label}
            </li>
          ))}
          {lines.map((line) => (
            <li key={line.key}>
              <span
                className="chart-swatch chart-swatch-line"
                style={{ background: lineKeyBackground(line) }}
              />
              {line.label}
            </li>
          ))}
          {flags && (
            <li>
              <svg
                className="chart-flag-icon"
                viewBox="0 0 12 12"
                aria-hidden="true"
              >
                <path d="M1 2H11L6 11Z" />
              </svg>
              {flags.label}
            </li>
          )}
        </ul>
      )}

      {asTable ? (
        <Table className="chart-table">
          <caption className="sr-only">{title}</caption>
          <thead>
            <tr>
              <th scope="col">{xLabel}</th>
              {bars.map((bar) => (
                <th key={bar.key} scope="col" className="num">
                  {bar.label}
                </th>
              ))}
              {lines.map((line) => (
                <th key={line.key} scope="col" className="num">
                  {line.label}
                </th>
              ))}
              {flags && <th scope="col">{flags.label}</th>}
            </tr>
          </thead>
          <tbody>
            {categories.map((category, i) => (
              <tr key={category.key}>
                <th scope="row">
                  {category.label}
                  {category.note && (
                    <span className="chart-note"> ({category.note})</span>
                  )}
                </th>
                {bars.map((bar) => (
                  <td key={bar.key} className="num">
                    {money(Math.max(0, bar.values[i] ?? 0))}
                  </td>
                ))}
                {lines.map((line) => {
                  const value = line.values[i] ?? null;
                  return (
                    <td key={line.key} className="num">
                      {value === null ? "–" : money(value)}
                    </td>
                  );
                })}
                {flags && <td>{flags.values[i] ? t`Yes` : t`No`}</td>}
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <div className="chart-plotwrap">
          <svg
            className="chart-svg"
            viewBox={`0 0 ${WIDTH} ${height}`}
            role="group"
            aria-labelledby={titleId}
          >
            {/* value axis: gridlines, tick labels, title */}
            {axis.ticks.map((tick) => (
              <g key={tick}>
                <line
                  className={tick === 0 ? "chart-baseline" : "chart-grid"}
                  x1={MARGIN.left}
                  x2={WIDTH - MARGIN.right}
                  y1={yOf(tick)}
                  y2={yOf(tick)}
                />
                <text
                  className="chart-tick"
                  x={MARGIN.left - 8}
                  y={yOf(tick)}
                  textAnchor="end"
                  dominantBaseline="central"
                >
                  {formatAxisCents(tick, locale)}
                </text>
              </g>
            ))}
            <text
              className="chart-axis-title"
              transform={`translate(16 ${MARGIN.top + plotHeight / 2}) rotate(-90)`}
              textAnchor="middle"
            >
              {yLabel}
            </text>

            {/* category axis */}
            {categories.map((category, i) => (
              <text
                key={category.key}
                className={cx(
                  "chart-tick",
                  flags?.values[i] && "chart-tick-flag",
                )}
                x={xCenter(i)}
                y={baseline + 16}
                textAnchor="middle"
              >
                {category.tick}
                {category.tickSub && (
                  <tspan x={xCenter(i)} dy="13">
                    {category.tickSub}
                  </tspan>
                )}
              </text>
            ))}
            <text
              className="chart-axis-title"
              x={MARGIN.left + plotWidth / 2}
              y={height - 6}
              textAnchor="middle"
            >
              {xLabel}
            </text>

            {divider && divider.index > 0 && divider.index < count && (
              <g>
                <line
                  className="chart-divider"
                  x1={MARGIN.left + band * divider.index}
                  x2={MARGIN.left + band * divider.index}
                  y1={MARGIN.top - 34}
                  y2={baseline}
                />
                <text
                  className="chart-tick"
                  x={MARGIN.left + band * divider.index + 5}
                  y={MARGIN.top - 26}
                >
                  {divider.label}
                </text>
              </g>
            )}

            {/* flagged categories: a band and a marker (colour is never the only cue) */}
            {flags?.values.map(
              (flagged, i) =>
                flagged && (
                  <g
                    key={categories[i]?.key ?? i}
                    data-flag={categories[i]?.key}
                  >
                    <rect
                      className="chart-flag-band"
                      x={MARGIN.left + band * i}
                      y={MARGIN.top}
                      width={band}
                      height={plotHeight}
                    />
                    <path
                      className="chart-flag-mark"
                      d={`M${xCenter(i) - 6},${MARGIN.top - 12}H${xCenter(i) + 6}L${xCenter(i)},${MARGIN.top - 1}Z`}
                    />
                  </g>
                ),
            )}

            {active !== null && (
              <rect
                className="chart-active-band"
                x={MARGIN.left + band * active}
                y={MARGIN.top}
                width={band}
                height={plotHeight}
              />
            )}

            {selected !== null && selected >= 0 && selected < count && (
              <line
                className="chart-selected"
                x1={xCenter(selected)}
                x2={xCenter(selected)}
                y1={MARGIN.top}
                y2={MARGIN.top + plotHeight}
              />
            )}

            {/* bars, stacked from the baseline */}
            {categories.map((category, i) => {
              let cursor = baseline;
              const segments = bars
                .map((bar) => ({ bar, value: Math.max(0, bar.values[i] ?? 0) }))
                .filter((segment) => segment.value > 0);
              return (
                <g key={category.key} data-category={category.key}>
                  {segments.map(({ bar, value }, s) => {
                    const end = cursor - (baseline - yOf(value));
                    const isTop = s === segments.length - 1;
                    const y = end + (isTop ? 0 : GAP / 2);
                    const bottom = cursor - (s === 0 ? 0 : GAP / 2);
                    const h = bottom - y;
                    cursor = end;
                    if (h < 1) return null;
                    const x = xCenter(i) - barWidth / 2;
                    return isTop ? (
                      <path
                        key={bar.key}
                        d={roundedTop(x, y, barWidth, h)}
                        style={{ fill: bar.color, opacity: bar.opacity ?? 1 }}
                        data-bar={bar.key}
                        data-cents={value}
                      />
                    ) : (
                      <rect
                        key={bar.key}
                        x={x}
                        y={y}
                        width={barWidth}
                        height={h}
                        style={{ fill: bar.color, opacity: bar.opacity ?? 1 }}
                        data-bar={bar.key}
                        data-cents={value}
                      />
                    );
                  })}
                </g>
              );
            })}

            {/* lines: 2 px, round joins; a ringed end dot and a direct label at the last point */}
            {lines.map((line) => {
              const points = line.values
                .map((value, i) =>
                  value === null ? null : ([xCenter(i), yOf(value)] as const),
                )
                .map((point, i) => ({ point, i }));
              let path = "";
              let area = "";
              let run: (readonly [number, number])[] = [];
              const flush = () => {
                if (run.length === 0) return;
                path += run
                  .map(([x, y], n) => `${n === 0 ? "M" : "L"}${x},${y}`)
                  .join("");
                if (line.area) {
                  const first = run[0];
                  const last = run[run.length - 1];
                  if (first && last) {
                    area += `M${first[0]},${baseline}${run.map(([x, y]) => `L${x},${y}`).join("")}L${last[0]},${baseline}Z`;
                  }
                }
                run = [];
              };
              for (const { point } of points) {
                if (point === null) flush();
                else run.push(point);
              }
              flush();
              const last = [...points]
                .reverse()
                .find((entry) => entry.point !== null);
              const activePoint =
                active !== null ? (points[active]?.point ?? null) : null;
              return (
                <g key={line.key} data-line={line.key}>
                  {line.area && (
                    <path d={area} style={{ fill: line.color, opacity: 0.1 }} />
                  )}
                  <path
                    d={path}
                    style={{ fill: "none", stroke: line.color }}
                    strokeWidth={2}
                    strokeLinejoin="round"
                    strokeLinecap="round"
                    strokeDasharray={line.dashed ? "7 5" : undefined}
                  />
                  {last?.point && (
                    <circle
                      className="chart-dot"
                      cx={last.point[0]}
                      cy={last.point[1]}
                      r={4}
                      style={{ fill: line.color }}
                    />
                  )}
                  {activePoint && (
                    <circle
                      className="chart-dot"
                      cx={activePoint[0]}
                      cy={activePoint[1]}
                      r={4}
                      style={{ fill: line.color }}
                    />
                  )}
                </g>
              );
            })}

            {/* marks: a ring and a label on a line (the lowest point) */}
            {marks.map((mark) => {
              const value = lines.find((line) => line.key === mark.lineKey)
                ?.values[mark.index];
              if (value === null || value === undefined) return null;
              const x = xCenter(mark.index);
              const y = yOf(value);
              // The label sits above the ring, or below it near the top edge; it turns left near the right edge.
              const below = y - MARGIN.top < 24;
              return (
                <g key={`${mark.lineKey}-${mark.index}`} data-mark={mark.index}>
                  <circle className="chart-mark" cx={x} cy={y} r={7} />
                  <text
                    className="chart-mark-label"
                    x={x + (x > WIDTH * 0.7 ? -12 : 12)}
                    y={below ? y + 20 : y - 12}
                    textAnchor={x > WIDTH * 0.7 ? "end" : "start"}
                  >
                    {mark.label}
                  </text>
                </g>
              );
            })}

            {/* one focusable, pointer-aware surface over the plot */}
            <rect
              className="chart-hit"
              x={MARGIN.left}
              y={MARGIN.top}
              width={plotWidth}
              height={plotHeight}
              tabIndex={0}
              role="application"
              aria-label={
                readHint ??
                t`${title}: use the left and right arrow keys to read each month`
              }
              onClick={onClick}
              onPointerMove={onPointerMove}
              onPointerLeave={() => setActive(null)}
              onKeyDown={onKeyDown}
              onFocus={() => setActive((at) => at ?? 0)}
              onBlur={() => setActive(null)}
            />
          </svg>

          {activeCategory && active !== null && (
            <div
              className={cx(
                "chart-tip",
                xCenter(active) > WIDTH * 0.6 && "chart-tip-left",
              )}
              style={{
                left: `${(xCenter(active) / WIDTH) * 100}%`,
                top: `${(MARGIN.top / height) * 100}%`,
              }}
              aria-hidden="true"
            >
              <div className="chart-tip-title">
                {activeCategory.label}
                {activeCategory.note && (
                  <span className="chart-note"> ({activeCategory.note})</span>
                )}
              </div>
              {readout(active).map((row) => (
                <div className="chart-tip-row" key={row.key}>
                  <span
                    className={cx(
                      "chart-swatch",
                      row.kind === "bar"
                        ? "chart-swatch-bar"
                        : "chart-swatch-line",
                    )}
                    style={{ background: row.color, opacity: row.opacity }}
                  />
                  <strong className="num">{money(row.cents ?? 0)}</strong>
                  <span>{row.label}</span>
                </div>
              ))}
              {flags?.values[active] && (
                <div className="chart-tip-flag">{flags.label}</div>
              )}
            </div>
          )}
          <p className="sr-only" aria-live="polite">
            {activeSummary}
          </p>
        </div>
      )}
    </figure>
  );
}
