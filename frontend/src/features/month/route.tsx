import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { MonthPage } from "./MonthPage";

export const feature: FeatureRoute = {
  path: "month",
  Component: MonthPage,
  nav: { label: msg`Month`, order: 40, group: "analyse" },
};
