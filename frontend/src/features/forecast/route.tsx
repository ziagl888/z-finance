import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { ForecastPage } from "./ForecastPage";

export const feature: FeatureRoute = {
  path: "forecast",
  Component: ForecastPage,
  nav: { label: msg`Forecast`, order: 35, group: "analyse" },
};
