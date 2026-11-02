import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router";

import { useLocale } from "../../i18n";
import { getSetup } from "../accounts/accountsApi";

/** URL of the wizard; it has no navigation entry — the shell sends the user there once. */
export const FIRST_RUN_PATH = "first-run";

/**
 * Send a first-time user to the wizard (UC NF10: "Der erste Start führt durch …").
 *
 * The one hook the shell needs for this feature, in the shape of `useQuickEntryShortcut`. It asks
 * the backend once per page load whether the default category set is still on offer — the same
 * signal the Accounts page uses — and navigates to the wizard if it is. A backend that cannot
 * answer changes nothing: the status line in the sidebar already says so.
 */
export function useFirstRunRedirect(): void {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { locale } = useLocale();
  const asked = useRef(false);

  useEffect(() => {
    if (asked.current) return;
    asked.current = true;
    let cancelled = false;
    getSetup(locale)
      .then((state) => {
        if (cancelled || !state.offer_default_set) return;
        if (pathname.startsWith(`/${FIRST_RUN_PATH}`)) return;
        void navigate(`/${FIRST_RUN_PATH}`, { replace: true });
      })
      .catch(() => {
        // Nothing to do: without an answer we cannot know, and guessing would trap the user.
      });
    return () => {
      cancelled = true;
    };
  }, [locale, navigate, pathname]);
}
