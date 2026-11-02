import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { PayoffPage } from "./PayoffPage";

export const feature: FeatureRoute = {
  path: "payoff",
  Component: PayoffPage,
  nav: { label: msg`Payoff plan`, order: 45, group: "plan" },
};
