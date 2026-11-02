import type { ReactNode } from "react";

import { cx } from "./cx";

export interface KpiTileProps {
  label: ReactNode;
  value: ReactNode;
  /** Small line under the value (comparison, period). */
  sub?: ReactNode;
  /** `alert` tints tile and value for a figure that needs attention; `ok` for a good one. */
  tone?: "default" | "alert" | "ok";
  className?: string;
}

/** One key figure: small uppercase label, large light number, optional sub line. */
export function KpiTile({
  label,
  value,
  sub,
  tone = "default",
  className,
}: KpiTileProps) {
  return (
    <div
      className={cx(
        "ui-kpi",
        tone !== "default" && `ui-kpi-${tone}`,
        className,
      )}
    >
      <div className="ui-kpi-label">{label}</div>
      <div className="ui-kpi-value">{value}</div>
      {sub !== undefined && <div className="ui-kpi-sub">{sub}</div>}
    </div>
  );
}
