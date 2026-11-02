import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { RecurringPage } from "./RecurringPage";

export const feature: FeatureRoute = {
  path: "recurring",
  Component: RecurringPage,
  nav: { label: msg`Recurring items`, order: 30, group: "plan" },
};
