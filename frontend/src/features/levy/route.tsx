import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { LevyPage } from "./LevyPage";

export const feature: FeatureRoute = {
  path: "levy",
  Component: LevyPage,
  nav: { label: msg`Levy & revenue`, order: 85, group: "plan" },
};
