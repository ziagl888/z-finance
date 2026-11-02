import type { HTMLAttributes } from "react";

import { cx } from "./cx";

export interface CardProps extends HTMLAttributes<HTMLElement> {
  /** Element to render; `article` for a self-contained item, `section` for a titled part. */
  as?: "div" | "section" | "article" | "aside";
  /** Tighter padding for dense content such as long tables. */
  dense?: boolean;
}

/** Glass surface that groups one part of a screen (dev-front "Frame"). */
export function Card({
  as: Tag = "div",
  dense,
  className,
  ...rest
}: CardProps) {
  return (
    <Tag
      className={cx("ui-card", dense && "ui-card-dense", className)}
      {...rest}
    />
  );
}
