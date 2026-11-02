import {
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
} from "react";

import { cx } from "./cx";

type Describable = ReactElement<{ "aria-describedby"?: string }>;

export interface FieldProps {
  /** id of the control; the label points at it. */
  htmlFor: string;
  label: ReactNode;
  /** Help line under the control, linked through aria-describedby. */
  hint?: ReactNode;
  /** Error under the control (role="alert"), linked through aria-describedby. */
  error?: ReactNode;
  /** Spans two columns of a form grid. */
  wide?: boolean;
  className?: string;
  /** The control (input, select, textarea, or a group around it). */
  children: ReactNode;
}

/**
 * Label + control + optional hint and error. When the control is a single element without its own
 * `aria-describedby`, the hint and error ids are added to it.
 */
export function Field({
  htmlFor,
  label,
  hint,
  error,
  wide,
  className,
  children,
}: FieldProps) {
  const hintId = hint ? `${htmlFor}-hint` : undefined;
  const errorId = error ? `${htmlFor}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ");
  const control =
    describedBy &&
    isValidElement(children) &&
    !(children as Describable).props["aria-describedby"]
      ? cloneElement(children as Describable, {
          "aria-describedby": describedBy,
        })
      : children;
  return (
    <div className={cx("ui-field", wide && "ui-field-wide", className)}>
      <label htmlFor={htmlFor}>{label}</label>
      {control}
      {hint && (
        <span id={hintId} className="ui-field-hint">
          {hint}
        </span>
      )}
      {error && (
        <span id={errorId} role="alert" className="ui-field-error">
          {error}
        </span>
      )}
    </div>
  );
}
