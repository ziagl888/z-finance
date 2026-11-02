import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { BackupPage } from "./BackupPage";

export const feature: FeatureRoute = {
  path: "backup",
  Component: BackupPage,
  nav: { label: msg`Backup`, order: 900, group: "settings" },
};
