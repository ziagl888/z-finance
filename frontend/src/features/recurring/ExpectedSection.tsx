import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { Button, Card, Field, MoneyCell, Table } from "../../ui";
import type { Account, Category } from "../accounts/accountsApi";
import { CATEGORY_KIND_LABELS } from "../accounts/labels";
import { todayIso } from "../entry/draft";
import { formatDate, magnitudeCents } from "./draft";
import { createExpected, deleteExpected, type Expected } from "./recurringApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

type Kind = "expense" | "income";

interface Props {
  accounts: Account[];
  allAccounts: Account[];
  categories: Category[];
  expected: Expected[] | null;
  /** Reload the list after a change. */
  onChanged: () => Promise<void>;
}

/**
 * Expected one-off items (UC-ZF-003 step 3): a known future payment or income with date, amount,
 * account, category and text. It is a planned booking; when it happens it is booked under due
 * items or replaced by the imported bank line.
 */
export function ExpectedSection({
  accounts,
  allAccounts,
  categories,
  expected,
  onChanged,
}: Props) {
  const { t, i18n } = useLingui();
  const [kind, setKind] = useState<Kind>("expense");
  const [date, setDate] = useState("");
  const [amount, setAmount] = useState("");
  const [accountId, setAccountId] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const account = accounts.some((a) => String(a.id) === accountId)
    ? accountId
    : accounts[0]
      ? String(accounts[0].id)
      : "";
  const accountName = (id: number) =>
    allAccounts.find((a) => a.id === id)?.name ?? "—";
  const categoryName = (id: number | null) =>
    id === null ? "" : (categories.find((c) => c.id === id)?.name ?? "");

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const cents = magnitudeCents(amount);
    if (date <= todayIso()) {
      setError(t`Choose a date after today. Enter earlier ones as bookings.`);
      return;
    }
    if (cents === null) {
      setError(t`Enter an amount other than 0, e.g. 12,50.`);
      return;
    }
    if (account === "" || categoryId === "") {
      setError(t`Choose an account and a category.`);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      await createExpected({
        date,
        amount_cents: kind === "expense" ? -cents : cents,
        account_id: Number(account),
        category_id: Number(categoryId),
        text: text.trim(),
        counterparty: null,
      });
      setAmount("");
      setText("");
      await onChanged();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  }

  async function onDelete(item: Expected) {
    if (!window.confirm(t`Delete this expected item?`)) return;
    setError(null);
    try {
      await deleteExpected(item.id);
      await onChanged();
    } catch (reason) {
      setError(message(reason));
    }
  }

  return (
    <Card as="section" aria-labelledby="rec-expected-title">
      <h2 id="rec-expected-title">
        <Trans>Expected one-off items</Trans>
      </h2>
      <form
        className="rec-form"
        onSubmit={(event) => void onSubmit(event)}
        noValidate
        aria-label={t`Expected item`}
      >
        <div className="ui-form-grid">
          <Field htmlFor="exp-kind" label={<Trans>Kind</Trans>}>
            <select
              id="exp-kind"
              value={kind}
              onChange={(event) => setKind(event.target.value as Kind)}
            >
              <option value="expense">{t`Expense`}</option>
              <option value="income">{t`Income`}</option>
            </select>
          </Field>
          <Field htmlFor="exp-date" label={<Trans>Date</Trans>}>
            <input
              id="exp-date"
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
          </Field>
          <Field htmlFor="exp-amount" label={<Trans>Amount (€)</Trans>}>
            <input
              id="exp-amount"
              className="num"
              inputMode="decimal"
              autoComplete="off"
              placeholder="0,00"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
            />
          </Field>
          <Field htmlFor="exp-account" label={<Trans>Account</Trans>}>
            <select
              id="exp-account"
              value={account}
              onChange={(event) => setAccountId(event.target.value)}
            >
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </Field>
          <Field htmlFor="exp-category" label={<Trans>Category</Trans>}>
            <select
              id="exp-category"
              value={categoryId}
              onChange={(event) => setCategoryId(event.target.value)}
            >
              <option value="">{t`Choose…`}</option>
              {categories
                .filter((c) => c.kind !== "transfer")
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({i18n._(CATEGORY_KIND_LABELS[c.kind])})
                  </option>
                ))}
            </select>
          </Field>
          <Field htmlFor="exp-text" label={<Trans>Text</Trans>}>
            <input
              id="exp-text"
              autoComplete="off"
              maxLength={200}
              value={text}
              onChange={(event) => setText(event.target.value)}
            />
          </Field>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="ui-actions">
          <Button variant="primary" type="submit" disabled={saving}>
            <Trans>Add expected item</Trans>
          </Button>
        </div>
      </form>

      {expected === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : expected.length === 0 ? (
        <p className="ui-hint">
          <Trans>No expected items.</Trans>
        </p>
      ) : (
        <Table className="rec-table">
          <thead>
            <tr>
              <th>
                <Trans>Date</Trans>
              </th>
              <th>
                <Trans>Text</Trans>
              </th>
              <th>
                <Trans>Category</Trans>
              </th>
              <th>
                <Trans>Account</Trans>
              </th>
              <th className="num">
                <Trans>Amount</Trans>
              </th>
              <th>
                <span className="sr-only">
                  <Trans>Actions</Trans>
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {expected.map((item) => (
              <tr key={item.id}>
                <td>{formatDate(item.date, i18n.locale)}</td>
                <td>{item.text || "—"}</td>
                <td>{categoryName(item.category_id)}</td>
                <td>{accountName(item.account_id)}</td>
                <MoneyCell cents={item.amount_cents} />
                <td className="rec-row-actions">
                  <Button
                    variant="danger"
                    type="button"
                    onClick={() => void onDelete(item)}
                  >
                    <Trans>Delete</Trans>
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
