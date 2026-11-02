import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import {
  listAccounts,
  listCategories,
  type Account,
  type Category,
} from "../accounts/accountsApi";
import {
  deleteTransaction,
  listTransactions,
  type Transaction,
} from "./entryApi";
import { EntryForm } from "./EntryForm";
import { TransactionList } from "./TransactionList";
import { Card } from "../../ui";
import "./entry.css";

interface Loaded {
  active: Account[];
  all: Account[];
  transactions: Transaction[];
}

/** Active accounts (the form's choice), all accounts (names in the list), latest bookings. */
async function load(): Promise<Loaded> {
  const [active, all, transactions] = await Promise.all([
    listAccounts(false),
    listAccounts(true),
    listTransactions(),
  ]);
  return { active, all, transactions };
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Quick entry (UC-ZF-002): the form on top, the latest bookings below. Accounts in the form are
 * the active ones (archived accounts are not bookable); the list names archived ones too, and a
 * booking opened for editing keeps its archived account.
 */
export function EntryPage() {
  const { t } = useLingui();
  const [activeAccounts, setActiveAccounts] = useState<Account[] | null>(null);
  const [allAccounts, setAllAccounts] = useState<Account[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [transactions, setTransactions] = useState<Transaction[] | null>(null);
  const [editing, setEditing] = useState<Transaction | null>(null);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback((data: Loaded) => {
    setActiveAccounts(data.active);
    setAllAccounts(data.all);
    setTransactions(data.transactions);
  }, []);

  /** Reload accounts (balances) and bookings after a change. */
  const reload = useCallback(async () => apply(await load()), [apply]);

  // `EntryForm` renders (and is typable, `Alt+N` then `Enter`) before this first fetch has
  // necessarily resolved; a save started that early awaits `waitAccountsReady()` instead of
  // failing on an account list that is merely not there *yet* (T-2026-CU-9130-043). `accountsSettled`
  // (true on success or failure alike — a failed fetch must not leave a submit waiting forever) is
  // flipped in the *same* state update as `apply(data)`, so React commits/renders the new
  // `accounts` prop before the effect below runs and resolves the waiters: render always precedes
  // effects of the same commit, so `EntryForm`'s `accountsRef` is already fresh by then.
  const [accountsSettled, setAccountsSettled] = useState(false);
  const accountsWaitersRef = useRef<(() => void)[]>([]);
  const waitAccountsReady = useCallback((): Promise<void> => {
    if (accountsSettled) return Promise.resolve();
    return new Promise<void>((resolve) => {
      accountsWaitersRef.current.push(resolve);
    });
  }, [accountsSettled]);

  useEffect(() => {
    if (!accountsSettled) return;
    const waiters = accountsWaitersRef.current;
    accountsWaitersRef.current = [];
    waiters.forEach((resolve) => resolve());
  }, [accountsSettled]);

  useEffect(() => {
    let cancelled = false;
    load()
      .then((data) => {
        if (cancelled) return;
        apply(data);
        setAccountsSettled(true);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(message(reason));
        setAccountsSettled(true);
      });
    listCategories()
      .then((loaded) => !cancelled && setCategories(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [apply]);

  async function onDelete(item: Transaction) {
    const question =
      item.transfer_group_id !== null
        ? t`Delete this transfer? Both of its bookings are removed.`
        : t`Delete this booking?`;
    if (!window.confirm(question)) return;
    setError(null);
    try {
      await deleteTransaction(item.id);
      const sameGroup =
        item.transfer_group_id !== null &&
        editing?.transfer_group_id === item.transfer_group_id;
      if (editing?.id === item.id || sameGroup) setEditing(null);
      await reload();
    } catch (reason) {
      setError(message(reason));
    }
  }

  const noAccounts = activeAccounts !== null && activeAccounts.length === 0;

  return (
    <section className="entry">
      <h1>
        <Trans>Quick entry</Trans>
      </h1>
      {noAccounts ? (
        <p className="ui-hint">
          <Trans>
            Create an account first: <Link to="/accounts">Accounts</Link>.
          </Trans>
        </p>
      ) : (
        <Card>
          <EntryForm
            accounts={activeAccounts ?? []}
            allAccounts={allAccounts}
            categories={categories}
            waitAccountsReady={waitAccountsReady}
            editing={editing}
            onSaved={() =>
              reload().catch((reason: unknown) => setError(message(reason)))
            }
            onCancelEdit={() => setEditing(null)}
          />
        </Card>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <h2>
        <Trans>Latest bookings</Trans>
      </h2>
      <TransactionList
        transactions={transactions}
        accounts={allAccounts}
        categories={categories}
        editingId={editing?.id ?? null}
        onEdit={setEditing}
        onDelete={(item) => void onDelete(item)}
      />
    </section>
  );
}
