import { Trans } from "@lingui/react/macro";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";

import { Card } from "../../ui";
import { listAccounts, type Account } from "./accountsApi";
import { AccountsSection } from "./AccountsSection";
import { FirstRunPanel } from "./FirstRunPanel";
import "./accounts.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Accounts (UC-ZF-001): the first-run offer of the default category set (kept here, where earlier
 * versions offered it) and the account list with balances. The categories have their own page;
 * a hint links to it. Server messages (already in the UI language) are shown in one alert line.
 */
export function AccountsPage() {
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  // The adopted default set is listed on the Categories page, so go there after the adoption.
  const showCategories = useCallback(async () => {
    await navigate("/categories");
  }, [navigate]);

  const reportError = useCallback(
    (reason: unknown) => setError(message(reason)),
    [],
  );

  const refreshAccounts = useCallback(async () => {
    setAccounts(await listAccounts(showArchived));
  }, [showArchived]);

  useEffect(() => {
    let cancelled = false;
    listAccounts(showArchived)
      .then((loaded) => !cancelled && setAccounts(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [showArchived]);

  /** Run a change, then reload; any failure lands in the alert line. */
  const run = useCallback(
    async (change: () => Promise<unknown>) => {
      setError(null);
      try {
        await change();
        await refreshAccounts();
        return true;
      } catch (reason) {
        reportError(reason);
        return false;
      }
    },
    [refreshAccounts, reportError],
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
      <FirstRunPanel onDone={showCategories} onError={reportError} />
      <Card>
        <p>
          <Trans>
            The income and expense categories have their own page:{" "}
            <Link to="/categories">Categories</Link>.
          </Trans>
        </p>
      </Card>
      <AccountsSection
        accounts={accounts}
        showArchived={showArchived}
        onShowArchived={setShowArchived}
        run={run}
      />
    </section>
  );
}
