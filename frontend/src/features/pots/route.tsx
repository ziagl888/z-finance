import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { PotsPage } from "./PotsPage";

export const feature: FeatureRoute = {
  path: "pots",
  Component: PotsPage,
  nav: { label: msg`Savings pots`, order: 35, group: "plan" },
};
