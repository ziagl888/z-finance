import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { SettingsPage } from "./SettingsPage";

export const feature: FeatureRoute = {
  path: "settings",
  Component: SettingsPage,
  nav: { label: msg`Settings`, order: 90, group: "settings" },
};
