import type { HTMLAttributes } from "react";

import { cx } from "./cx";

export type BadgeTone =
  "neutral" | "planned" | "skipped" | "overdue" | "ok" | "warn";

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

/** Small status pill next to a text or amount (planned, skipped, overdue, …). */
export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  return (
    <span className={cx("ui-badge", `ui-badge-${tone}`, className)} {...rest} />
  );
}
