import { Trans } from "@lingui/react/macro";
import { useCallback, useEffect, useState } from "react";

import { listCategories, type Category } from "../accounts/accountsApi";
import { FirstRunPanel } from "../accounts/FirstRunPanel";
import { CategoriesSection } from "./CategoriesSection";
import "../accounts/accounts.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Categories (UC-ZF-001): the income, expense and transfer categories on a screen of their own,
 * with the first-run offer of the default set while the backend still offers it. Server messages
 * (already in the UI language) are shown in one alert line.
 */
export function CategoriesPage() {
  const [categories, setCategories] = useState<Category[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reportError = useCallback(
    (reason: unknown) => setError(message(reason)),
    [],
  );

  const refresh = useCallback(async () => {
    setCategories(await listCategories());
  }, []);

  useEffect(() => {
    let cancelled = false;
    listCategories()
      .then((loaded) => !cancelled && setCategories(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  /** Run a change, then reload; any failure lands in the alert line. */
  const run = useCallback(
    async (change: () => Promise<unknown>) => {
      setError(null);
      try {
        await change();
        await refresh();
        return true;
      } catch (reason) {
        reportError(reason);
        return false;
      }
    },
    [refresh, reportError],
  );

  return (
    <section className="accounts">
      <h1>
        <Trans>Categories</Trans>
      </h1>
      <p>
        <Trans>
          Categories say what money was for. Every booking belongs to one
          category.
        </Trans>
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <FirstRunPanel onDone={refresh} onError={reportError} />
      <CategoriesSection categories={categories} run={run} />
    </section>
  );
}
