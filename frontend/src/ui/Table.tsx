import type { TableHTMLAttributes } from "react";

import { cx } from "./cx";

export interface TableProps extends TableHTMLAttributes<HTMLTableElement> {
  /** Subtle alternating row background; on by default. */
  zebra?: boolean;
}

/** Data table: sticky header row, hairline rows, optional zebra. Content is plain thead/tbody. */
export function Table({ zebra = true, className, ...rest }: TableProps) {
  return (
    <table
      className={cx("ui-table", zebra && "ui-table-zebra", className)}
      {...rest}
    />
  );
}
