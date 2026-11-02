import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { AllocationPage } from "./AllocationPage";

export const feature: FeatureRoute = {
  path: "allocation",
  Component: AllocationPage,
  nav: { label: msg`Allocation`, order: 60, group: "plan" },
};
