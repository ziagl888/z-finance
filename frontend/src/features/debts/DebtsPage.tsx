import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router";

import {
  centsToInput,
  listAccounts,
  parseEuroInput,
  type Account,
} from "../accounts/accountsApi";
import { Button, Card, Field } from "../../ui";
import { DebtDetail } from "./DebtDetail";
import { DebtForm } from "./DebtForm";
import { DebtList } from "./DebtList";
import {
  deleteDebt,
  getDebtSettings,
  listDebts,
  putDebtSettings,
  type Debt,
  type Overview,
} from "./debtsApi";
import "./debts.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Debts (UC-ZF-010): the form for a debt, the overview with remaining amount, instalment, end and
 * sums, the selected debt's details with special payment and history, and the payoff budget.
 */
export function DebtsPage() {
  const { t, i18n } = useLingui();
  const [payAccounts, setPayAccounts] = useState<Account[] | null>(null);
  const [allAccounts, setAllAccounts] = useState<Account[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Debt | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setOverview(await listDebts());
    setAllAccounts(await listAccounts(true));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const fail = (reason: unknown) => !cancelled && setError(message(reason));
    Promise.all([listAccounts(false), listAccounts(true)])
      .then(([active, all]) => {
        if (cancelled) return;
        // Instalments are paid from real money accounts, never from another debt.
        setPayAccounts(active.filter((a) => a.type !== "liability"));
        setAllAccounts(all);
      })
      .catch(fail);
    listDebts()
      .then((loaded) => !cancelled && setOverview(loaded))
      .catch(fail);
    return () => {
      cancelled = true;
    };
  }, []);

  async function run(change: () => Promise<unknown>) {
    setError(null);
    try {
      await change();
      await reload();
    } catch (reason) {
      setError(message(reason));
    }
  }

  function onDelete(debt: Debt) {
    if (
      !window.confirm(
        t`Delete this debt? Its planned instalments are removed; booked payments stay.`,
      )
    ) {
      return;
    }
    if (selectedId === debt.id) setSelectedId(null);
    if (editing?.id === debt.id) setEditing(null);
    void run(() => deleteDebt(debt.id));
  }

  const selected =
    overview?.debts.find((debt) => debt.id === selectedId) ?? null;
  const noAccounts = payAccounts !== null && payAccounts.length === 0;

  return (
    <section className="debt">
      <h1>
        <Trans>Debts</Trans>
      </h1>
      <p className="debt-intro muted">
        <Trans>
          Loans, instalment purchases, private debts: see how much is still open
          and when it is paid off. Instalments appear as planned items.
        </Trans>
      </p>
      {noAccounts ? (
        <p className="ui-hint">
          <Trans>
            Create a payment account first: <Link to="/accounts">Accounts</Link>
            .
          </Trans>
        </p>
      ) : (
        <Card>
          <DebtForm
            accounts={payAccounts ?? []}
            editing={editing}
            onSaved={async (debt) => {
              setEditing(null);
              setSelectedId(debt.id);
              await reload();
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
      <DebtList
        overview={overview}
        selectedId={selectedId}
        onSelect={(debt) => setSelectedId(debt.id)}
        onEdit={(debt) => {
          setEditing(debt);
          setSelectedId(debt.id);
          window.scrollTo({ top: 0, behavior: "smooth" });
        }}
        onDelete={onDelete}
      />
      {selected && (
        <DebtDetail
          key={selected.id}
          debt={selected}
          accounts={payAccounts ?? []}
          allAccounts={allAccounts}
          onChanged={reload}
        />
      )}
      <PayoffBudget locale={i18n.locale} />
    </section>
  );
}

/** The monthly payoff budget (used by the payoff plan and the allocation rules). */
function PayoffBudget({ locale }: { locale: string }) {
  const { t } = useLingui();
  const [value, setValue] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDebtSettings()
      .then(
        (settings) =>
          !cancelled &&
          setValue(centsToInput(settings.payoff_budget_cents, locale)),
      )
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [locale]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cents = parseEuroInput(value ?? "");
    setNotice(null);
    if (cents === null || cents < 0) {
      setError(t`Enter an amount of 0 or more, e.g. 300.`);
      return;
    }
    setError(null);
    try {
      const saved = await putDebtSettings({ payoff_budget_cents: cents });
      setValue(centsToInput(saved.payoff_budget_cents, locale));
      setNotice(t`Saved.`);
    } catch (reason) {
      setError(message(reason));
    }
  }

  return (
    <form className="ui-card debt-budget" onSubmit={onSubmit} noValidate>
      <h2>
        <Trans>Payoff budget</Trans>
      </h2>
      <p className="ui-hint">
        <Trans>
          How much you can put towards your debts each month on top of the fixed
          instalments.
        </Trans>
      </p>
      <div className="debt-actions">
        <Field htmlFor="debt-budget" label={<Trans>Per month</Trans>}>
          <input
            id="debt-budget"
            className="num"
            inputMode="decimal"
            value={value ?? ""}
            disabled={value === null}
            onChange={(e) => setValue(e.target.value)}
          />
        </Field>
        <Button variant="primary" type="submit" disabled={value === null}>
          <Trans>Save</Trans>
        </Button>
      </div>
      {notice && (
        <p role="status" className="ui-notice">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </form>
  );
}
