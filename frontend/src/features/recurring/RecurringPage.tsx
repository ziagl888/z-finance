import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";

import { Card } from "../../ui";
import {
  listAccounts,
  listCategories,
  type Account,
  type Category,
} from "../accounts/accountsApi";
import { ExpectedSection } from "./ExpectedSection";
import { ItemsPanel } from "./ItemsPanel";
import {
  deleteRecurring,
  listExpected,
  listRecurring,
  setRecurringActive,
  type Expected,
  type Recurring,
} from "./recurringApi";
import { RecurringForm } from "./RecurringForm";
import { RecurringList } from "./RecurringList";
import "./recurring.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Recurring and expected items (UC-ZF-003): the form for a recurring item, the list of recurring
 * items with the items of the selected one, and the expected one-off items below.
 */
export function RecurringPage() {
  const { t } = useLingui();
  const [activeAccounts, setActiveAccounts] = useState<Account[] | null>(null);
  const [allAccounts, setAllAccounts] = useState<Account[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [recurring, setRecurring] = useState<Recurring[] | null>(null);
  const [expected, setExpected] = useState<Expected[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Recurring | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reloadRecurring = useCallback(
    async () => setRecurring(await listRecurring()),
    [],
  );
  const reloadExpected = useCallback(
    async () => setExpected(await listExpected()),
    [],
  );

  useEffect(() => {
    let cancelled = false;
    const fail = (reason: unknown) => !cancelled && setError(message(reason));
    Promise.all([listAccounts(false), listAccounts(true), listCategories()])
      .then(([active, all, loaded]) => {
        if (cancelled) return;
        setActiveAccounts(active);
        setAllAccounts(all);
        setCategories(loaded);
      })
      .catch(fail);
    listRecurring()
      .then((items) => !cancelled && setRecurring(items))
      .catch(fail);
    listExpected()
      .then((items) => !cancelled && setExpected(items))
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, []);

  /** Run a change, then reload the recurring list; a failure shows its message. */
  async function run(change: () => Promise<unknown>) {
    setError(null);
    try {
      await change();
      await reloadRecurring();
    } catch (reason) {
      setError(message(reason));
    }
  }

  function onToggleActive(item: Recurring) {
    if (
      item.active &&
      !window.confirm(
        t`Pause this recurring item? Its planned items are removed; booked ones stay.`,
      )
    ) {
      return;
    }
    void run(() => setRecurringActive(item.id, !item.active));
  }

  function onDelete(item: Recurring) {
    if (
      !window.confirm(
        t`Delete this recurring item? Its planned items are removed; booked ones stay as bookings.`,
      )
    ) {
      return;
    }
    if (selectedId === item.id) setSelectedId(null);
    if (editing?.id === item.id) setEditing(null);
    void run(() => deleteRecurring(item.id));
  }

  const selected = recurring?.find((item) => item.id === selectedId) ?? null;
  const noAccounts = activeAccounts !== null && activeAccounts.length === 0;

  return (
    <section className="rec">
      <h1>
        <Trans>Recurring items</Trans>
      </h1>
      <p className="rec-intro muted">
        <Trans>
          Rent, insurance, subscriptions, instalments: enter them once and they
          appear as planned items for the next 12 months.
        </Trans>
      </p>
      {noAccounts ? (
        <p className="ui-hint">
          <Trans>
            Create an account first: <Link to="/accounts">Accounts</Link>.
          </Trans>
        </p>
      ) : (
        <Card>
          <RecurringForm
            accounts={activeAccounts ?? []}
            categories={categories}
            editing={editing}
            onSaved={async (item) => {
              setEditing(null);
              setSelectedId(item.id);
              await reloadRecurring();
            }}
            onCancelEdit={() => setEditing(null)}
          />
        </Card>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <RecurringList
        items={recurring}
        accounts={allAccounts}
        categories={categories}
        selectedId={selectedId}
        onSelect={(item) => setSelectedId(item.id)}
        onEdit={(item) => {
          setEditing(item);
          setSelectedId(item.id);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
        onToggleActive={onToggleActive}
        onDelete={onDelete}
      />
      {selected && (
        <ItemsPanel recurring={selected} onChanged={reloadRecurring} />
      )}
      {!noAccounts && (
        <ExpectedSection
          accounts={activeAccounts ?? []}
          allAccounts={allAccounts}
          categories={categories}
          expected={expected}
          onChanged={() =>
            reloadExpected().catch((reason: unknown) =>
              setError(message(reason)),
            )
          }
        />
      )}
    </section>
  );
}
