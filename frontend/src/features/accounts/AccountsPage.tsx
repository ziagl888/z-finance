import { Trans } from "@lingui/react/macro";
import { useCallback, useEffect, useState } from "react";

import {
  listAccounts,
  listCategories,
  type Account,
  type Category,
} from "./accountsApi";
import { AccountsSection } from "./AccountsSection";
import { CategoriesSection } from "./CategoriesSection";
import { FirstRunPanel } from "./FirstRunPanel";
import "./accounts.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Accounts and categories (UC-ZF-001): the first-run offer of the default category set, the
 * account list with balances, and the category tree. Server messages (already in the UI language)
 * are shown in one alert line.
 */
export function AccountsPage() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [categories, setCategories] = useState<Category[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reportError = useCallback(
    (reason: unknown) => setError(message(reason)),
    [],
  );

  const refreshAccounts = useCallback(async () => {
    setAccounts(await listAccounts(showArchived));
  }, [showArchived]);

  const refreshCategories = useCallback(async () => {
    setCategories(await listCategories());
  }, []);

  useEffect(() => {
    let cancelled = false;
    listAccounts(showArchived)
      .then((loaded) => !cancelled && setAccounts(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [showArchived]);

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
    async (change: () => Promise<unknown>, reload: () => Promise<void>) => {
      setError(null);
      try {
        await change();
        await reload();
        return true;
      } catch (reason) {
        reportError(reason);
        return false;
      }
    },
    [reportError],
  );

  return (
    <section className="accounts">
      <h1>
        <Trans>Accounts</Trans>
      </h1>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <FirstRunPanel onDone={refreshCategories} onError={reportError} />
      <AccountsSection
        accounts={accounts}
        showArchived={showArchived}
        onShowArchived={setShowArchived}
        run={(change) => run(change, refreshAccounts)}
      />
      <CategoriesSection
        categories={categories}
        run={(change) => run(change, refreshCategories)}
      />
    </section>
  );
}
