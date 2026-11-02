import { useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import "./charts.css";

export interface SplitSegment {
  key: string;
  label: string;
  /** Non-negative cents; a segment of 0 is left out of the bar. */
  cents: number;
  /** Any CSS colour, normally `var(--chart-n)`. */
  color: string;
}

/**
 * One horizontal bar cut into blocks whose widths are their shares of the sum (UC-ZF-015 4a: which
 * block drives the required revenue). The bar carries an accessible summary; the exact numbers
 * belong in a table next to it.
 */
export function SplitBar({ segments }: { segments: SplitSegment[] }) {
  const { t, i18n } = useLingui();
  const shown = segments.filter((segment) => segment.cents > 0);
  const total = shown.reduce((sum, segment) => sum + segment.cents, 0);
  const summary = shown
    .map(
      (segment) =>
        `${segment.label} ${formatCents(segment.cents, i18n.locale)}`,
    )
    .join(", ");
  return (
    <div className="chart-split">
      <ul
        className="chart-split-bar"
        role="img"
        aria-label={t`Split by block: ${summary}`}
      >
        {shown.map((segment) => (
          <li
            key={segment.key}
            title={`${segment.label}: ${formatCents(segment.cents, i18n.locale)}`}
            style={{
              flexBasis: `${(segment.cents / total) * 100}%`,
              background: segment.color,
            }}
          />
        ))}
      </ul>
    </div>
  );
}
