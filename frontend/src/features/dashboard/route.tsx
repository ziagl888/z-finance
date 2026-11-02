import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { DashboardPage } from "./DashboardPage";

/**
 * The dashboard claims the index route (the empty route below), so the shell's built-in start page
 * no longer applies and the manual chapter of this screen is `index`. It keeps a navigation entry
 * at the top of "Capture": the weekly routine starts here and every other screen leads back to it.
 */
export const feature: FeatureRoute = {
  path: "",
  Component: DashboardPage,
  nav: { label: msg`Dashboard`, order: 5, group: "capture" },
};
