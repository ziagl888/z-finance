import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { ImportPage } from "./ImportPage";

export const feature: FeatureRoute = {
  path: "import",
  Component: ImportPage,
  nav: { label: msg`Import`, order: 40, group: "capture" },
};
