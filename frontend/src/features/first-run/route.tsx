import type { FeatureRoute } from "../../app/features";
import { FirstRunWizard } from "./FirstRunWizard";

/**
 * The first-run wizard has no navigation entry: the shell sends a first-time user here once
 * (`redirect.ts`), and afterwards the screen has nothing left to do. Its manual chapter is
 * `first-run.md`, reachable from the manual like any other.
 */
export const feature: FeatureRoute = {
  path: "first-run",
  Component: FirstRunWizard,
};
