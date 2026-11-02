import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { DuePage } from "./DuePage";

export const feature: FeatureRoute = {
  path: "due",
  Component: DuePage,
  nav: { label: msg`Due items`, order: 25, group: "capture" },
};
