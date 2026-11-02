import type { MessageDescriptor } from "@lingui/core";
import type { ComponentType } from "react";

/** Sidebar groups, in display order. */
export const NAV_GROUPS = ["capture", "plan", "analyse", "settings"] as const;
export type NavGroup = (typeof NAV_GROUPS)[number];

/**
 * What a feature contributes to the shell. Each feature exports one of these as `feature` from
 * `src/features/<name>/route.tsx`; the shell discovers them at build time, so adding a feature
 * never means editing a shared file.
 */
export interface FeatureRoute {
  /** URL path without leading slash, e.g. "accounts". Use "" to claim the start page. */
  path: string;
  /** Page component rendered inside the layout. */
  Component: ComponentType;
  /** Navigation entry; omit for pages reachable only by link. */
  nav?: {
    /** Translatable label: msg`Accounts` from "@lingui/core/macro". A plain string is shown untranslated. */
    label: MessageDescriptor | string;
    /** Sort key in the navigation, ascending (within the group). */
    order: number;
    /**
     * Sidebar group: capture (enter data), plan (future items), analyse (reports), settings
     * (master data and app settings). Default: settings.
     */
    group?: NavGroup;
  };
}

interface FeatureModule {
  feature: FeatureRoute;
}

const modules = import.meta.glob<FeatureModule>("../features/*/route.tsx", {
  eager: true,
});

/** All discovered feature routes, sorted by navigation order then path. */
export const features: FeatureRoute[] = Object.entries(modules)
  .map(([file, mod]) => {
    if (!mod.feature) {
      throw new Error(`${file} must export \`feature: FeatureRoute\``);
    }
    return mod.feature;
  })
  .sort(
    (a, b) =>
      (a.nav?.order ?? Infinity) - (b.nav?.order ?? Infinity) ||
      a.path.localeCompare(b.path),
  );
