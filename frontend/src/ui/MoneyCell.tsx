import { useLingui } from "@lingui/react/macro";
import type { ReactNode, TdHTMLAttributes } from "react";

import { formatCents } from "../api/client";
import { cx } from "./cx";

export interface MoneyCellProps extends TdHTMLAttributes<HTMLTableCellElement> {
  /** Signed integer cents. */
  cents: number;
  /** Rendered after the amount (e.g. a badge). */
  children?: ReactNode;
  /**
   * No alert colour for negative amounts — for tables where every amount has the same sign by
   * construction (an expense column), so red would carry no information.
   */
  plain?: boolean;
}

/**
 * Table cell with an amount in the UI locale: right-aligned tabular digits, negative amounts in
 * the alert colour (unless `plain`). The text is exactly `formatCents(cents, locale)`.
 */
export function MoneyCell({
  cents,
  children,
  plain,
  className,
  ...rest
}: MoneyCellProps) {
  const { i18n } = useLingui();
  return (
    <td
      className={cx(
        "num",
        "ui-money",
        !plain && cents < 0 && "ui-money-negative",
        className,
      )}
      {...rest}
    >
      {formatCents(cents, i18n.locale)}
      {children}
    </td>
  );
}
