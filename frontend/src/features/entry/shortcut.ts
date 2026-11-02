/**
 * Global quick-entry shortcut: Alt+N from every page opens the entry screen with the amount focused.
 *
 * Alt+N collides with no browser default (Ctrl+N opens a window; the Alt menus of Chrome, Edge and
 * Firefox use other letters). The physical key is matched (`code`), so the layout does not matter;
 * Ctrl or Meta must be up, which also excludes AltGr (reported as Ctrl+Alt on Windows).
 */
import { useEffect } from "react";
import { useNavigate } from "react-router";

/** Path of the entry screen (the feature's route). */
export const ENTRY_PATH = "/entry";
/** Window event the entry screen listens to when the shortcut fires while it is already open. */
export const FOCUS_ENTRY_EVENT = "zfinance:focus-entry";

type KeyInfo = Pick<
  KeyboardEvent,
  "code" | "altKey" | "ctrlKey" | "metaKey" | "shiftKey"
>;

/** True for Alt+N without Ctrl, Meta or Shift. */
export function isQuickEntryShortcut(event: KeyInfo): boolean {
  return (
    event.code === "KeyN" &&
    event.altKey &&
    !event.ctrlKey &&
    !event.metaKey &&
    !event.shiftKey
  );
}

/** Install the shortcut for the whole app; called once in the layout. */
export function useQuickEntryShortcut(): void {
  const navigate = useNavigate();
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!isQuickEntryShortcut(event)) return;
      event.preventDefault();
      void navigate(ENTRY_PATH);
      window.dispatchEvent(new Event(FOCUS_ENTRY_EVENT));
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);
}
