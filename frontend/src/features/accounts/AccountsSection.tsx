import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { Badge, Button, Card, Field, MoneyCell, Table } from "../../ui";
import {
  ACCOUNT_TYPES,
  AREAS,
  archiveAccount,
  centsToInput,
  createAccount,
  deleteAccount,
  parseEuroInput,
  updateAccount,
  type Account,
  type AccountInput,
  type AccountType,
  type Area,
  type RunChange,
} from "./accountsApi";
import { ACCOUNT_TYPE_LABELS, AREA_LABELS } from "./labels";

/** Form state: amounts stay text until submit, so "12," can be typed on the way to "12,50". */
interface Draft {
  name: string;
  area: Area;
  type: AccountType;
  opening: string;
  openingDate: string;
  reserve: string;
}

function today(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

const emptyDraft = (): Draft => ({
  name: "",
  area: "private",
  type: "giro",
  opening: "0",
  openingDate: today(),
  reserve: "0",
});

interface Props {
  accounts: Account[] | null;
  showArchived: boolean;
  onShowArchived: (show: boolean) => void;
  run: RunChange;
}

/** Account list with balances, and the form to create or edit one. */
export function AccountsSection({
  accounts,
  showArchived,
  onShowArchived,
  run,
}: Props) {
  const { t, i18n } = useLingui();
  const [draft, setDraft] = useState<Draft>(emptyDraft);
  const [editing, setEditing] = useState<number | null>(null);
  const [invalid, setInvalid] = useState<string | null>(null);

  const update = (changes: Partial<Draft>) =>
    setDraft((current) => ({ ...current, ...changes }));

  function startEdit(account: Account) {
    setEditing(account.id);
    setInvalid(null);
    setDraft({
      name: account.name,
      area: account.area,
      type: account.type,
      opening: centsToInput(account.opening_balance_cents, i18n.locale),
      openingDate: account.opening_date,
      reserve: centsToInput(account.reserve_floor_cents, i18n.locale),
    });
  }

  function reset() {
    setEditing(null);
    setInvalid(null);
    setDraft(emptyDraft());
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const opening = parseEuroInput(draft.opening);
    const reserve = parseEuroInput(draft.reserve);
    if (opening === null) {
      setInvalid(t`Enter the opening balance as an amount, e.g. 1250.00.`);
      return;
    }
    if (reserve === null || reserve < 0) {
      setInvalid(t`Enter the reserve floor as a positive amount or 0.`);
      return;
    }
    setInvalid(null);
    const input: AccountInput = {
      name: draft.name,
      area: draft.area,
      type: draft.type,
      opening_balance_cents: opening,
      opening_date: draft.openingDate,
      reserve_floor_cents: reserve,
    };
    const saved = await run(() =>
      editing === null ? createAccount(input) : updateAccount(editing, input),
    );
    if (saved) reset();
  }

  function onDelete(account: Account) {
    const { name } = account;
    if (window.confirm(t`Delete the account ${name}?`)) {
      void run(() => deleteAccount(account.id));
    }
  }

  return (
    <Card>
      <h2>
        <Trans>Your accounts</Trans>
      </h2>
      <label className="accounts-inline">
        <input
          type="checkbox"
          checked={showArchived}
          onChange={(event) => onShowArchived(event.target.checked)}
        />{" "}
        <Trans>Show archived accounts</Trans>
      </label>
      {accounts === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : accounts.length === 0 ? (
        <p>
          {showArchived ? (
            <Trans>No accounts yet. Create your first one below.</Trans>
          ) : (
            <Trans>
              No active accounts. Create one below, or show archived accounts.
            </Trans>
          )}
        </p>
      ) : (
        <Table>
          <thead>
            <tr>
              <th>
                <Trans>Name</Trans>
              </th>
              <th>
                <Trans>Area</Trans>
              </th>
              <th>
                <Trans>Type</Trans>
              </th>
              <th className="num">
                <Trans>Balance</Trans>
              </th>
              <th className="num">
                <Trans>Reserve floor</Trans>
              </th>
              <th>
                <Trans>Actions</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {accounts.map((account) => (
              <tr
                key={account.id}
                className={account.archived_at ? "accounts-archived" : ""}
              >
                <td>
                  {account.name}
                  {account.archived_at && (
                    <>
                      {" "}
                      <Badge>
                        <Trans>archived</Trans>
                      </Badge>
                    </>
                  )}
                </td>
                <td>{i18n._(AREA_LABELS[account.area])}</td>
                <td>{i18n._(ACCOUNT_TYPE_LABELS[account.type])}</td>
                <MoneyCell cents={account.balance_cents} />
                <MoneyCell cents={account.reserve_floor_cents} />
                <td className="ui-actions">
                  <button type="button" onClick={() => startEdit(account)}>
                    <Trans>Edit</Trans>
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      void run(() =>
                        archiveAccount(account.id, !account.archived_at),
                      )
                    }
                  >
                    {account.archived_at ? t`Unarchive` : t`Archive`}
                  </button>
                  {account.booking_count === 0 && (
                    <Button
                      variant="danger"
                      type="button"
                      onClick={() => onDelete(account)}
                    >
                      <Trans>Delete</Trans>
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <form className="accounts-form" onSubmit={onSubmit}>
        <h3>{editing === null ? t`New account` : t`Edit account`}</h3>
        <div className="ui-form-grid">
          <Field htmlFor="account-name" label={<Trans>Name</Trans>}>
            <input
              id="account-name"
              required
              maxLength={100}
              value={draft.name}
              onChange={(event) => update({ name: event.target.value })}
            />
          </Field>
          <Field htmlFor="account-area" label={<Trans>Area</Trans>}>
            <select
              id="account-area"
              value={draft.area}
              onChange={(event) => update({ area: event.target.value as Area })}
            >
              {AREAS.map((area) => (
                <option key={area} value={area}>
                  {i18n._(AREA_LABELS[area])}
                </option>
              ))}
            </select>
          </Field>
          <Field htmlFor="account-type" label={<Trans>Type</Trans>}>
            <select
              id="account-type"
              value={draft.type}
              onChange={(event) =>
                update({ type: event.target.value as AccountType })
              }
            >
              {ACCOUNT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {i18n._(ACCOUNT_TYPE_LABELS[type])}
                </option>
              ))}
            </select>
          </Field>
          <Field
            htmlFor="account-opening"
            label={<Trans>Opening balance (€)</Trans>}
          >
            <input
              id="account-opening"
              inputMode="decimal"
              required
              value={draft.opening}
              onChange={(event) => update({ opening: event.target.value })}
            />
          </Field>
          <Field
            htmlFor="account-opening-date"
            label={<Trans>Opening date</Trans>}
          >
            <input
              id="account-opening-date"
              type="date"
              required
              value={draft.openingDate}
              onChange={(event) => update({ openingDate: event.target.value })}
            />
          </Field>
          <Field
            htmlFor="account-reserve"
            label={<Trans>Reserve floor (€)</Trans>}
          >
            <input
              id="account-reserve"
              inputMode="decimal"
              required
              value={draft.reserve}
              onChange={(event) => update({ reserve: event.target.value })}
            />
          </Field>
        </div>
        {invalid && (
          <p role="alert" className="error">
            {invalid}
          </p>
        )}
        <div className="ui-actions">
          <Button variant="primary" type="submit">
            {editing === null ? t`Create account` : t`Save changes`}
          </Button>
          {editing !== null && (
            <button type="button" onClick={reset}>
              <Trans>Cancel</Trans>
            </button>
          )}
        </div>
      </form>
    </Card>
  );
}
