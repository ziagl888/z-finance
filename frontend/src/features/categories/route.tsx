import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { CategoriesPage } from "./CategoriesPage";

export const feature: FeatureRoute = {
  path: "categories",
  Component: CategoriesPage,
  // Directly after Accounts (20): users look for the categories next to the accounts.
  nav: { label: msg`Categories`, order: 21, group: "settings" },
};
