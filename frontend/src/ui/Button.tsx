import type { ButtonHTMLAttributes } from "react";

import { cx } from "./cx";

export type ButtonVariant = "primary" | "secondary" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** primary = the one main action of a form; danger = deletes or discards. Default secondary. */
  variant?: ButtonVariant;
}

/** Action button. `type` defaults to "button"; a form's submit button says `type="submit"`. */
export function Button({
  variant = "secondary",
  type = "button",
  className,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx("ui-button", `ui-button-${variant}`, className)}
      {...rest}
    />
  );
}
