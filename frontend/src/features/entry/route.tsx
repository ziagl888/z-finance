import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { EntryPage } from "./EntryPage";

export const feature: FeatureRoute = {
  path: "entry",
  Component: EntryPage,
  nav: { label: msg`Quick entry`, order: 10, group: "capture" },
};
