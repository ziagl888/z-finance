import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { AccountsPage } from "./AccountsPage";

export const feature: FeatureRoute = {
  path: "accounts",
  Component: AccountsPage,
  nav: { label: msg`Accounts`, order: 20, group: "settings" },
};
