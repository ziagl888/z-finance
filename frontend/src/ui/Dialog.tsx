import { useEffect, type FormHTMLAttributes, type HTMLAttributes } from "react";

import { cx } from "./cx";

interface DialogBase {
  /** Accessible name (the visible title may say more). */
  label?: string;
  /** Escape calls this while the dialog is shown. */
  onClose?: () => void;
}

export type DialogProps =
  | (DialogBase & { as?: "div" } & HTMLAttributes<HTMLDivElement>)
  | (DialogBase & { as: "form" } & FormHTMLAttributes<HTMLFormElement>);

/**
 * Raised panel with role="dialog", shown in the page flow next to what it belongs to (not modal:
 * the page stays usable). `as="form"` makes the dialog itself the form.
 */
export function Dialog({ label, onClose, className, ...props }: DialogProps) {
  useEffect(() => {
    if (!onClose) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const shared = {
    role: "dialog",
    "aria-label": label,
    className: cx("ui-card", "ui-dialog", className),
  };
  if (props.as === "form") {
    return <form {...withoutAs(props)} {...shared} />;
  }
  return <div {...withoutAs(props)} {...shared} />;
}

function withoutAs<T extends { as?: unknown }>(props: T): Omit<T, "as"> {
  const copy = { ...props };
  delete copy.as;
  return copy;
}
