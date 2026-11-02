import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { DebtsPage } from "./DebtsPage";

export const feature: FeatureRoute = {
  path: "debts",
  Component: DebtsPage,
  nav: { label: msg`Debts`, order: 40, group: "plan" },
};
