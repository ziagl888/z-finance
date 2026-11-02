import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { formatCents } from "../../api/client";
import { Badge, Button, Card, cx, Field } from "../../ui";
import type { Account } from "../accounts/accountsApi";
import { formatDay } from "../levy/draft";
import {
  canAdopt,
  emptyWhatIf,
  inView,
  MAX_WHAT_IF_ITEMS,
  nextDay,
  whatIfItem,
  type Direction,
  type Invalid,
  type WhatIfDraft,
} from "./draft";
import type { ForecastView, WhatIfItem } from "./forecastApi";

interface Props {
  /** Accounts an item can be booked on (no liabilities). */
  accounts: Account[];
  view: ForecastView;
  /** Today of the server, the first day an item may lie on. */
  start: string;
  items: WhatIfItem[];
  busy: boolean;
  onAdd: (item: WhatIfItem) => void;
  onRemove: (index: number) => void;
  onClear: () => void;
  /** Stores one item as an expected item (the only thing on this screen that saves). */
  onAdopt: (index: number) => void;
}

/**
 * UC-ZF-006 extension 1a: hypothetical items (amount, day, account) that redraw the curve.
 * They live in the page's state only — nothing is sent to be stored until "Add as expected item".
 */
export function WhatIfForm({
  accounts,
  view,
  start,
  items,
  busy,
  onAdd,
  onRemove,
  onClear,
  onAdopt,
}: Props) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [draft, setDraft] = useState<WhatIfDraft>(() => emptyWhatIf("", ""));
  const [invalid, setInvalid] = useState<Invalid | null>(null);

  // A view on one account (or an area) defaults to an account of that view.
  const preferred =
    accounts.find((account) => inView(account, view)) ?? accounts[0];
  const accountId = draft.accountId || (preferred ? String(preferred.id) : "");
  const date = draft.date || nextDay(start);

  function change(patch: Partial<WhatIfDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
    setInvalid(null);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const result = whatIfItem(
      { ...draft, accountId, date },
      start,
      items.length,
    );
    if ("invalid" in result) {
      setInvalid(result.invalid);
      return;
    }
    onAdd(result.item);
    setDraft({ ...draft, amount: "", text: "" });
  }

  const errors: Record<Invalid, string> = {
    amount: t`Enter an amount other than 0.`,
    date: t`The date must be today or later.`,
    account: t`Choose an account.`,
    limit: t`At most ${MAX_WHAT_IF_ITEMS} what-if items at a time.`,
  };
  const error = (field: Invalid) =>
    invalid === field ? errors[field] : undefined;
  const limitError = error("limit");
  const accountName = (id: number) =>
    accounts.find((account) => account.id === id)?.name ?? `#${id}`;

  return (
    <Card as="section" aria-labelledby="forecast-whatif-title">
      <h2 id="forecast-whatif-title">
        <Trans>What if?</Trans>
      </h2>
      <p className="ui-hint">
        <Trans>
          Try an item that is not in your plan, for example a larger purchase.
          The curve is redrawn. Nothing is saved: the items disappear when you
          leave or reload the page, unless you add one as an expected item.
        </Trans>
      </p>
      <form onSubmit={submit} noValidate className="forecast-whatif-form">
        <div className="ui-form-grid">
          <Field htmlFor="forecast-wi-direction" label={<Trans>Type</Trans>}>
            <select
              id="forecast-wi-direction"
              value={draft.direction}
              onChange={(event) =>
                change({ direction: event.target.value as Direction })
              }
            >
              <option value="expense">{t`Expense`}</option>
              <option value="income">{t`Income`}</option>
            </select>
          </Field>
          <Field
            htmlFor="forecast-wi-amount"
            label={<Trans>Amount (EUR)</Trans>}
            error={error("amount")}
          >
            <input
              id="forecast-wi-amount"
              inputMode="decimal"
              autoComplete="off"
              value={draft.amount}
              aria-invalid={invalid === "amount" || undefined}
              onChange={(event) => change({ amount: event.target.value })}
            />
          </Field>
          <Field
            htmlFor="forecast-wi-date"
            label={<Trans>Date</Trans>}
            error={error("date")}
          >
            <input
              id="forecast-wi-date"
              type="date"
              min={start}
              value={date}
              aria-invalid={invalid === "date" || undefined}
              onChange={(event) => change({ date: event.target.value })}
            />
          </Field>
          <Field
            htmlFor="forecast-wi-account"
            label={<Trans>Account</Trans>}
            error={error("account")}
          >
            <select
              id="forecast-wi-account"
              value={accountId}
              aria-invalid={invalid === "account" || undefined}
              onChange={(event) => change({ accountId: event.target.value })}
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            htmlFor="forecast-wi-text"
            label={<Trans>Text (optional)</Trans>}
            wide
          >
            <input
              id="forecast-wi-text"
              value={draft.text}
              maxLength={200}
              autoComplete="off"
              onChange={(event) => change({ text: event.target.value })}
            />
          </Field>
        </div>
        {limitError && (
          <p role="alert" className="error">
            {limitError}
          </p>
        )}
        <div className="ui-actions">
          <Button type="submit" variant="primary" disabled={busy}>
            <Trans>Add to forecast</Trans>
          </Button>
        </div>
      </form>

      {items.length > 0 && (
        <>
          <ul aria-label={t`What-if items`} className="forecast-whatif-list">
            {items.map((item, index) => {
              const name =
                item.text === ""
                  ? formatCents(item.amount_cents, locale)
                  : item.text;
              const outside = !inView(
                accounts.find((account) => account.id === item.account_id),
                view,
              );
              return (
                <li key={`${item.date}-${item.account_id}-${index}`}>
                  <div className="forecast-whatif-line">
                    <span>{formatDay(item.date, locale)}</span>
                    <strong
                      className={cx(
                        "num",
                        "ui-money",
                        item.amount_cents < 0 && "ui-money-negative",
                      )}
                    >
                      {formatCents(item.amount_cents, locale)}
                    </strong>
                  </div>
                  <div className="muted">
                    {accountName(item.account_id)}
                    {item.text !== "" && ` · ${item.text}`}{" "}
                    {outside && (
                      <Badge tone="neutral">
                        <Trans>not in this view</Trans>
                      </Badge>
                    )}
                  </div>
                  <div className="ui-actions">
                    <Button
                      disabled={busy || !canAdopt(item, start)}
                      aria-label={t`Add ${name} as an expected item`}
                      title={
                        canAdopt(item, start)
                          ? undefined
                          : t`An expected item needs a date after today.`
                      }
                      onClick={() => onAdopt(index)}
                    >
                      <Trans>Add as expected item</Trans>
                    </Button>
                    <Button
                      variant="danger"
                      aria-label={t`Remove ${name} from the what-if items`}
                      onClick={() => onRemove(index)}
                    >
                      <Trans>Remove</Trans>
                    </Button>
                  </div>
                </li>
              );
            })}
          </ul>
          <div className="ui-actions">
            <Button onClick={onClear}>
              <Trans>Remove all what-if items</Trans>
            </Button>
          </div>
        </>
      )}
    </Card>
  );
}
