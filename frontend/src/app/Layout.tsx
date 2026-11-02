import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router";

import { api, type Health } from "../api/client";
import { useQuickEntryShortcut } from "../features/entry/shortcut";
import { useFirstRunRedirect } from "../features/first-run/redirect";
import { HelpButton } from "../help";
import {
  features,
  NAV_GROUPS,
  type FeatureRoute,
  type NavGroup,
} from "./features";
import { LanguageSwitch } from "./LanguageSwitch";
import "./shell.css";

type BackendState =
  { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "down" };

/** Waits before the retries of a failed health check (1, 2, 4 s); "down" shows after the last one. */
export const HEALTH_RETRY_DELAYS_MS = [1000, 2000, 4000];
/** Pause between health checks once the retries are used up and the backend is still down. */
export const HEALTH_POLL_INTERVAL_MS = 30_000;

const GROUP_LABELS: Record<NavGroup, MessageDescriptor> = {
  capture: msg`Capture`,
  plan: msg`Planning`,
  analyse: msg`Analyse`,
  settings: msg`Settings`,
};

/** Navigation entries grouped for the sidebar: groups in NAV_GROUPS order, empty groups left out. */
export function groupNav(
  routes: FeatureRoute[],
): { group: NavGroup; items: FeatureRoute[] }[] {
  const withNav = routes.filter((f) => f.nav);
  return NAV_GROUPS.map((group) => ({
    group,
    items: withNav.filter((f) => (f.nav!.group ?? "settings") === group),
  })).filter((entry) => entry.items.length > 0);
}

/**
 * App shell (desktop-first): fixed sidebar with the grouped navigation and the backend status,
 * a header with the current page, language switch and help button, and the page outlet.
 */
export function Layout({ routes = features }: { routes?: FeatureRoute[] }) {
  const { i18n, t } = useLingui();
  const { pathname } = useLocation();
  const [backend, setBackend] = useState<BackendState>({ kind: "loading" });
  useQuickEntryShortcut();
  useFirstRunRedirect();

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const check = (failures: number) => {
      api
        .get<Health>("/api/health")
        .then((health) => !cancelled && setBackend({ kind: "ok", health }))
        .catch(() => {
          if (cancelled) return;
          // A backend that starts slower than the frontend answers a moment later: retry with
          // backoff first, then keep polling while it is down.
          if (failures >= HEALTH_RETRY_DELAYS_MS.length) {
            setBackend({ kind: "down" });
          }
          const delay =
            HEALTH_RETRY_DELAYS_MS[failures] ?? HEALTH_POLL_INTERVAL_MS;
          timer = setTimeout(() => check(failures + 1), delay);
        });
    };
    check(0);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const label = (f: FeatureRoute) =>
    typeof f.nav!.label === "string" ? f.nav!.label : i18n._(f.nav!.label);
  const segment = pathname.split("/")[1] ?? "";
  const current = routes.find((f) => f.nav && f.path === segment);

  return (
    <div className="shell">
      <aside className="shell-sidebar">
        <NavLink to="/" className="shell-brand" end>
          <span className="shell-brand-mark" aria-hidden="true">
            z
          </span>
          <span className="shell-brand-name">z-finance</span>
        </NavLink>
        <nav aria-label={t`Main`} className="shell-nav">
          {groupNav(routes).map(({ group, items }) => (
            <div key={group} className="shell-nav-group">
              <div className="shell-nav-title" id={`nav-group-${group}`}>
                {i18n._(GROUP_LABELS[group])}
              </div>
              <ul aria-labelledby={`nav-group-${group}`}>
                {items.map((f) => (
                  <li key={f.path}>
                    <NavLink to={`/${f.path}`} end={f.path === ""}>
                      {label(f)}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <footer className="shell-footer" role="status">
          {backend.kind === "loading" && <Trans>Connecting…</Trans>}
          {backend.kind === "ok" && <BackendVersion health={backend.health} />}
          {backend.kind === "down" && (
            <span className="error">
              <Trans>Backend not reachable</Trans>
            </span>
          )}
        </footer>
      </aside>
      <div className="shell-body">
        <header className="shell-header">
          <div className="shell-crumb">
            {current && (
              <>
                <span className="shell-crumb-group">
                  {i18n._(GROUP_LABELS[current.nav!.group ?? "settings"])}
                </span>
                <span aria-hidden="true"> / </span>
                <span>{label(current)}</span>
              </>
            )}
          </div>
          <LanguageSwitch />
          <HelpButton />
        </header>
        <main className="shell-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

/** Footer line with app and schema version (macro placeholders must be plain identifiers). */
function BackendVersion({ health }: { health: Health }) {
  const { version, schema_version: schema } = health;
  return (
    <Trans>
      v{version} · schema {schema}
    </Trans>
  );
}
