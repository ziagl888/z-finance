import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { TaxPage } from "./TaxPage";

export const feature: FeatureRoute = {
  path: "tax",
  Component: TaxPage,
  nav: { label: msg`Taxes & levies`, order: 80, group: "plan" },
};
