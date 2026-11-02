import { msg } from "@lingui/core/macro";

import type { FeatureRoute } from "../../app/features";
import { ReceiptsPage } from "./ReceiptsPage";

export const feature: FeatureRoute = {
  path: "receipts",
  Component: ReceiptsPage,
  nav: { label: msg`Receipts`, order: 50, group: "capture" },
};
