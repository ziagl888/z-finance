import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";

import { formatCents } from "../../api/client";
import { Button, Dialog, Field } from "../../ui";
import {
  centsToInput,
  parseEuroInput,
  type Account,
} from "../accounts/accountsApi";
import type { Transaction } from "../entry/entryApi";
import { formatDate } from "../recurring/draft";
import {
  assignExpense,
  deleteSavingsPlan,
  deposit,
  listAccountBookings,
  movePots,
  putSavingsPlan,
  withdraw,
  type AccountSplit,
  type Pot,
} from "./potsApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Props every action dialog shares: how to finish (with a status line) or give up. */
interface Base {
  onDone: (notice: string) => Promise<void>;
  onClose: () => void;
}

/** Shell of the action dialogs: title, the form fields, and Save / Cancel. */
function ActionDialog({
  title,
  submitLabel,
  saving,
  onSubmit,
  onClose,
  children,
}: {
  title: string;
  submitLabel: ReactNode;
  saving: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Dialog
      as="form"
      label={title}
      className="pot-dialog"
      onSubmit={onSubmit}
      onClose={onClose}
      noValidate
    >
      <h2>{title}</h2>
      {children}
      <div className="ui-actions">
        <Button variant="primary" type="submit" disabled={saving}>
          {submitLabel}
        </Button>
        <Button onClick={onClose}>
          <Trans>Cancel</Trans>
        </Button>
      </div>
    </Dialog>
  );
}

/** Checkbox that unlocks taking money from a locked pot (UC 5c). */
function ConfirmLocked({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <label className="pot-check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <Trans>
        This pot is locked. Take the money anyway; it is logged as an emergency.
      </Trans>
    </label>
  );
}

/** Runs `send`, keeps the dialog open and shows the message at the field when it fails. */
function useSubmit() {
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  async function run(send: () => Promise<void>) {
    if (saving) return;
    setError(null);
    setSaving(true);
    try {
      await send();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  }
  return { error, setError, saving, run };
}

interface MoneyProps extends Base {
  kind: "deposit" | "withdraw";
  pot: Pot;
  /** The pot's account split (free balance) and the other accounts a deposit can come from. */
  split: AccountSplit | undefined;
  otherAccounts: Account[];
}

/**
 * Assign money to a pot (UC step 3: from the free balance, or a transfer from another account) or
 * take money out of it (a locked pot only after the confirmation, 5c). A refused amount — above the
 * free balance (3a) or the pot — shows the backend's message at the field.
 */
export function MoneyDialog({
  kind,
  pot,
  split,
  otherAccounts,
  onDone,
  onClose,
}: MoneyProps) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [amount, setAmount] = useState("");
  const [from, setFrom] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const { error, setError, saving, run } = useSubmit();
  const name = pot.name;
  const needsConfirm = kind === "withdraw" && pot.locked;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cents = parseEuroInput(amount);
    if (cents === null || cents <= 0) {
      setError(t`Enter an amount greater than 0, e.g. 500.`);
      return;
    }
    if (needsConfirm && !confirmed) {
      setError(t`Confirm that you want to take money from the locked pot.`);
      return;
    }
    void run(async () => {
      if (kind === "deposit") {
        await deposit(pot.id, cents, from === "" ? null : Number(from));
      } else {
        await withdraw(pot.id, cents, confirmed);
      }
      const shown = money(cents);
      await onDone(
        kind === "deposit"
          ? t`${shown} assigned to ${name}.`
          : t`${shown} taken out of ${name}.`,
      );
    });
  }

  const free = split ? money(split.free_cents) : "";
  const balance = money(pot.balance_cents);
  const hint =
    kind === "deposit" ? (
      split && <Trans>Free on this account: {free}</Trans>
    ) : (
      <Trans>The pot holds {balance}.</Trans>
    );

  return (
    <ActionDialog
      title={
        kind === "deposit" ? t`Add money to ${name}` : t`Take out of ${name}`
      }
      submitLabel={
        kind === "deposit" ? <Trans>Assign</Trans> : <Trans>Take out</Trans>
      }
      saving={saving}
      onSubmit={onSubmit}
      onClose={onClose}
    >
      <div className="ui-form-grid">
        <Field
          htmlFor="pot-money-amount"
          label={<Trans>Amount</Trans>}
          hint={hint}
          error={error}
        >
          <input
            id="pot-money-amount"
            className="num"
            inputMode="decimal"
            autoFocus
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        </Field>
        {kind === "deposit" && (
          <Field htmlFor="pot-money-from" label={<Trans>From</Trans>}>
            <select
              id="pot-money-from"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            >
              <option value="">{t`Free balance of this account`}</option>
              {otherAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </Field>
        )}
      </div>
      {needsConfirm && (
        <ConfirmLocked checked={confirmed} onChange={setConfirmed} />
      )}
    </ActionDialog>
  );
}

interface MoveProps extends Base {
  pot: Pot;
  /** The other open pots of the same account. */
  targets: Pot[];
}

/** Move money between two pots of one account (UC 3b, AK5) — no booking, the account does not change. */
export function MoveDialog({ pot, targets, onDone, onClose }: MoveProps) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [amount, setAmount] = useState("");
  const [target, setTarget] = useState(targets[0] ? String(targets[0].id) : "");
  const [confirmed, setConfirmed] = useState(false);
  const { error, setError, saving, run } = useSubmit();
  const name = pot.name;
  const balance = money(pot.balance_cents);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cents = parseEuroInput(amount);
    if (cents === null || cents <= 0) {
      setError(t`Enter an amount greater than 0, e.g. 500.`);
      return;
    }
    if (target === "") {
      setError(t`Choose the pot that gets the money.`);
      return;
    }
    if (pot.locked && !confirmed) {
      setError(t`Confirm that you want to take money from the locked pot.`);
      return;
    }
    void run(async () => {
      const [, receiver] = await movePots(
        pot.id,
        Number(target),
        cents,
        confirmed,
      );
      const shown = money(cents);
      const to = receiver?.name ?? "";
      await onDone(t`${shown} moved from ${name} to ${to}.`);
    });
  }

  return (
    <ActionDialog
      title={t`Move money from ${name}`}
      submitLabel={<Trans>Move</Trans>}
      saving={saving}
      onSubmit={onSubmit}
      onClose={onClose}
    >
      <div className="ui-form-grid">
        <Field
          htmlFor="pot-move-amount"
          label={<Trans>Amount</Trans>}
          hint={<Trans>The pot holds {balance}.</Trans>}
          error={error}
        >
          <input
            id="pot-move-amount"
            className="num"
            inputMode="decimal"
            autoFocus
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        </Field>
        <Field htmlFor="pot-move-target" label={<Trans>To pot</Trans>}>
          <select
            id="pot-move-target"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          >
            {targets.map((other) => (
              <option key={other.id} value={other.id}>
                {other.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {pot.locked && (
        <ConfirmLocked checked={confirmed} onChange={setConfirmed} />
      )}
    </ActionDialog>
  );
}

/** Booked expenses (not transfers) of an account, newest first. */
function expensesOf(bookings: Transaction[]): Transaction[] {
  return bookings.filter(
    (b) =>
      b.status === "booked" &&
      b.amount_cents < 0 &&
      b.transfer_group_id === null,
  );
}

/**
 * Put a booked expense on the pot (UC step 5, AK4): the pot pays for it up to its balance, the rest
 * comes from the free balance (5a) and is reported afterwards. The API offers no "not yet assigned"
 * filter, so an expense that already belongs to a pot is answered with the backend's message.
 */
export function ExpenseDialog({ pot, onDone, onClose }: Base & { pot: Pot }) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [expenses, setExpenses] = useState<Transaction[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const { error, setError, saving, run } = useSubmit();
  const name = pot.name;
  const balance = money(pot.balance_cents);

  useEffect(() => {
    let cancelled = false;
    listAccountBookings(pot.account_id)
      .then((rows) => {
        if (cancelled) return;
        const found = expensesOf(rows);
        setExpenses(found);
        setChosen(found[0] ? String(found[0].id) : "");
      })
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [pot.account_id, setError]);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (chosen === "") {
      setError(t`Choose the expense the pot pays for.`);
      return;
    }
    if (pot.locked && !confirmed) {
      setError(t`Confirm that you want to take money from the locked pot.`);
      return;
    }
    void run(async () => {
      const result = await assignExpense(pot.id, Number(chosen), confirmed);
      const covered = money(result.covered_cents);
      const free = money(result.from_free_cents);
      await onDone(
        result.from_free_cents > 0
          ? t`${name} covered ${covered}; ${free} came from the free balance of the account.`
          : t`${name} covered ${covered}.`,
      );
    });
  }

  return (
    <ActionDialog
      title={t`Assign an expense to ${name}`}
      submitLabel={<Trans>Assign expense</Trans>}
      saving={saving || expenses === null}
      onSubmit={onSubmit}
      onClose={onClose}
    >
      <Field
        htmlFor="pot-expense"
        label={<Trans>Expense</Trans>}
        hint={
          <Trans>
            The pot pays up to its balance of {balance}; the rest comes from the
            free balance.
          </Trans>
        }
        error={error}
      >
        <select
          id="pot-expense"
          autoFocus
          value={chosen}
          disabled={expenses === null || expenses.length === 0}
          onChange={(e) => setChosen(e.target.value)}
          aria-invalid={error ? true : undefined}
        >
          {(expenses ?? []).map((booking) => (
            <option key={booking.id} value={booking.id}>
              {`${formatDate(booking.date, i18n.locale)} · ${booking.text} · ${money(booking.amount_cents)}`}
            </option>
          ))}
        </select>
      </Field>
      {expenses !== null && expenses.length === 0 && (
        <p className="ui-hint">
          <Trans>
            No booked expense on this account yet. Enter it under Quick entry
            first.
          </Trans>
        </p>
      )}
      {pot.locked && (
        <ConfirmLocked checked={confirmed} onChange={setConfirmed} />
      )}
    </ActionDialog>
  );
}

interface PlanProps extends Base {
  pot: Pot;
  /** Active accounts other than the pot's account. */
  fromAccounts: Account[];
}

/**
 * The pot's monthly savings transfer (UC step 2): the monthly rate is offered as the amount, the
 * money comes from another account, the first transfer is next month and the plan ends on the
 * target date. An existing plan can be changed or ended.
 */
export function SavingsPlanDialog({
  pot,
  fromAccounts,
  onDone,
  onClose,
}: PlanProps) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const offered = pot.savings_plan_amount_cents ?? pot.monthly_rate_cents;
  const [amount, setAmount] = useState(
    offered === null ? "" : centsToInput(offered, i18n.locale),
  );
  const [from, setFrom] = useState(
    fromAccounts[0] ? String(fromAccounts[0].id) : "",
  );
  const { error, setError, saving, run } = useSubmit();
  const name = pot.name;
  const rate =
    pot.monthly_rate_cents === null ? "" : money(pot.monthly_rate_cents);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const cents = parseEuroInput(amount);
    if (cents === null || cents <= 0) {
      setError(t`Enter a monthly amount greater than 0, e.g. 250.`);
      return;
    }
    if (from === "") {
      setError(t`Choose the account the money comes from.`);
      return;
    }
    void run(async () => {
      await putSavingsPlan(pot.id, Number(from), cents);
      const shown = money(cents);
      await onDone(t`Savings plan for ${name}: ${shown} a month.`);
    });
  }

  function onEnd() {
    void run(async () => {
      await deleteSavingsPlan(pot.id);
      await onDone(t`Savings plan for ${name} ended.`);
    });
  }

  return (
    <ActionDialog
      title={t`Savings plan for ${name}`}
      submitLabel={<Trans>Save plan</Trans>}
      saving={saving}
      onSubmit={onSubmit}
      onClose={onClose}
    >
      <p className="ui-hint">
        <Trans>
          Every month this amount is transferred to the pot's account, starting
          next month and ending on the target date. The transfers appear as
          planned items.
        </Trans>
      </p>
      <div className="ui-form-grid">
        <Field
          htmlFor="pot-plan-amount"
          label={<Trans>Per month</Trans>}
          hint={
            pot.monthly_rate_cents === null ? undefined : (
              <Trans>Rate to reach the target: {rate}</Trans>
            )
          }
          error={error}
        >
          <input
            id="pot-plan-amount"
            className="num"
            inputMode="decimal"
            autoFocus
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            aria-invalid={error ? true : undefined}
          />
        </Field>
        <Field htmlFor="pot-plan-from" label={<Trans>Paid from</Trans>}>
          <select
            id="pot-plan-from"
            value={from}
            onChange={(e) => setFrom(e.target.value)}
          >
            {fromAccounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </Field>
      </div>
      {pot.savings_plan_id !== null && (
        <div className="ui-actions">
          <Button variant="danger" onClick={onEnd} disabled={saving}>
            <Trans>End savings plan</Trans>
          </Button>
        </div>
      )}
    </ActionDialog>
  );
}
