import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { ManualPage } from "../../help";

// The manual view lives in src/help (help framework, task -005); this file only registers it.
export const feature: FeatureRoute = {
  path: "manual",
  Component: ManualPage,
  // Not plain "Manual": that msgid is the backup kind (manual vs automatic), translated otherwise,
  // and oca-checks-po refuses the same msgid twice even with a msgctxt.
  nav: { label: msg`User manual`, order: 1000, group: "settings" },
};
