import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { Button, Field } from "../../ui";
import type { Account, Category } from "../accounts/accountsApi";
import { CATEGORY_KIND_LABELS } from "../accounts/labels";
import { todayIso } from "../entry/draft";
import {
  draftFrom,
  emptyDraft,
  toInput,
  weekdayNames,
  type Invalid,
  type Kind,
  type RecurringDraft,
} from "./draft";
import {
  createRecurring,
  INTERVALS,
  updateRecurring,
  type Interval,
  type Recurring,
} from "./recurringApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Props {
  accounts: Account[];
  categories: Category[];
  /** Item opened for editing, or null for a new one. */
  editing: Recurring | null;
  /** Called after a successful save with the stored item (reload lists). */
  onSaved: (item: Recurring) => Promise<void>;
  onCancelEdit: () => void;
}

/**
 * Create or edit a recurring item (UC-ZF-003 step 1): text, amount, account, category, interval
 * with day of the month or weekday, start and optional end. Saving generates the planned items
 * for the next 12 months at once. A failed save keeps every value.
 */
export function RecurringForm({
  accounts,
  categories,
  editing,
  onSaved,
  onCancelEdit,
}: Props) {
  const { t, i18n } = useLingui();
  const firstAccount = accounts[0] ? String(accounts[0].id) : "";
  const [draft, setDraft] = useState<RecurringDraft>(() =>
    emptyDraft(todayIso(), firstAccount),
  );
  const [shownEditing, setShownEditing] = useState<Recurring | null>(null);
  const [invalid, setInvalid] = useState<Invalid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Opening an item from the list fills the form; ending the edit returns to a new item.
  if (editing !== shownEditing) {
    setShownEditing(editing);
    setDraft(
      editing
        ? draftFrom(editing, i18n.locale)
        : emptyDraft(todayIso(), firstAccount),
    );
    setInvalid(null);
    setError(null);
  }

  // The account list may arrive after the first render.
  const account = accounts.some((a) => String(a.id) === draft.accountId)
    ? draft.accountId
    : firstAccount;
  const current = { ...draft, accountId: account };
  const update = (changes: Partial<RecurringDraft>) =>
    setDraft((previous) => ({ ...previous, accountId: account, ...changes }));

  const messages: Record<Invalid, string> = {
    description: t`Enter a text.`,
    amount: t`Enter an amount other than 0, e.g. 12,50.`,
    account: t`Choose an account (a transfer needs two different ones).`,
    category: t`Choose a category.`,
    day: t`Choose a day of the month between 1 and 31.`,
    start: t`Choose a start date.`,
    end: t`The end date must not be before the start date.`,
  };
  const kinds: { value: Kind; label: string }[] = [
    { value: "expense", label: t`Expense` },
    { value: "income", label: t`Income` },
    { value: "transfer", label: t`Transfer` },
  ];
  const intervalLabels: Record<Interval, string> = {
    weekly: t`Weekly`,
    monthly: t`Monthly`,
    quarterly: t`Quarterly`,
    yearly: t`Yearly`,
  };
  const categoryOptions =
    current.kind === "transfer"
      ? categories.filter((c) => c.kind === "transfer")
      : categories;
  const weekdays = weekdayNames(i18n.locale);
  const editingName = editing?.description ?? null;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const result = toInput(current);
    if ("invalid" in result) {
      setInvalid(result.invalid);
      setError(messages[result.invalid]);
      return;
    }
    setInvalid(null);
    setError(null);
    setSaving(true);
    try {
      const saved = await (editing
        ? updateRecurring(editing.id, result.input)
        : createRecurring(result.input));
      if (!editing) setDraft(emptyDraft(todayIso(), account));
      await onSaved(saved);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      className="rec-form"
      onSubmit={(event) => void onSubmit(event)}
      noValidate
      aria-label={t`Recurring item`}
    >
      <div className="rec-head">
        <fieldset className="ui-segmented">
          <legend className="sr-only">
            <Trans>Kind of item</Trans>
          </legend>
          {kinds.map((option) => (
            <label key={option.value}>
              <input
                type="radio"
                name="rec-kind"
                value={option.value}
                checked={current.kind === option.value}
                onChange={() =>
                  update({
                    kind: option.value,
                    ...(option.value === "transfer" ||
                    current.kind === "transfer"
                      ? { categoryId: "" }
                      : {}),
                  })
                }
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        {editingName !== null && (
          <p className="rec-editing">
            <Trans>Editing “{editingName}”</Trans>
          </p>
        )}
      </div>

      <div className="ui-form-grid rec-grid-main">
        <Field htmlFor="rec-description" label={<Trans>Text</Trans>}>
          <input
            id="rec-description"
            autoComplete="off"
            maxLength={200}
            value={current.description}
            aria-invalid={invalid === "description"}
            onChange={(event) => update({ description: event.target.value })}
          />
        </Field>
        <Field htmlFor="rec-amount" label={<Trans>Amount (€)</Trans>}>
          <input
            id="rec-amount"
            className="num"
            inputMode="decimal"
            autoComplete="off"
            placeholder="0,00"
            value={current.amount}
            aria-invalid={invalid === "amount"}
            onChange={(event) => update({ amount: event.target.value })}
          />
        </Field>
        <Field htmlFor="rec-category" label={<Trans>Category</Trans>}>
          <select
            id="rec-category"
            value={current.categoryId}
            aria-invalid={invalid === "category"}
            onChange={(event) => update({ categoryId: event.target.value })}
          >
            <option value="">
              {current.kind === "transfer" ? t`No category` : t`Choose…`}
            </option>
            {categoryOptions.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({i18n._(CATEGORY_KIND_LABELS[c.kind])})
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="ui-form-grid">
        <Field
          htmlFor="rec-account"
          label={
            current.kind === "transfer" ? (
              <Trans>From account</Trans>
            ) : (
              <Trans>Account</Trans>
            )
          }
        >
          <select
            id="rec-account"
            value={current.accountId}
            aria-invalid={invalid === "account"}
            onChange={(event) => update({ accountId: event.target.value })}
          >
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </Field>
        {current.kind === "transfer" && (
          <Field htmlFor="rec-to-account" label={<Trans>To account</Trans>}>
            <select
              id="rec-to-account"
              value={current.toAccountId}
              aria-invalid={invalid === "account"}
              onChange={(event) => update({ toAccountId: event.target.value })}
            >
              <option value="">{t`Choose…`}</option>
              {accounts
                .filter((a) => String(a.id) !== current.accountId)
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
            </select>
          </Field>
        )}
      </div>

      <div className="ui-form-grid">
        <Field htmlFor="rec-interval" label={<Trans>Interval</Trans>}>
          <select
            id="rec-interval"
            value={current.interval}
            onChange={(event) =>
              update({ interval: event.target.value as Interval })
            }
          >
            {INTERVALS.map((interval) => (
              <option key={interval} value={interval}>
                {intervalLabels[interval]}
              </option>
            ))}
          </select>
        </Field>
        {current.interval === "weekly" ? (
          <Field htmlFor="rec-weekday" label={<Trans>Weekday</Trans>}>
            <select
              id="rec-weekday"
              value={current.weekday}
              onChange={(event) => update({ weekday: event.target.value })}
            >
              {weekdays.map((name, index) => (
                <option key={name} value={index}>
                  {name}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <Field
            htmlFor="rec-day"
            label={<Trans>Day of the month</Trans>}
            hint={
              Number(current.dayOfMonth) >= 29 && (
                <Trans>
                  In shorter months the item falls on the last day of the month.
                </Trans>
              )
            }
          >
            <input
              id="rec-day"
              type="number"
              min={1}
              max={31}
              value={current.dayOfMonth}
              aria-invalid={invalid === "day"}
              onChange={(event) => update({ dayOfMonth: event.target.value })}
            />
          </Field>
        )}
        <Field htmlFor="rec-start" label={<Trans>Starts on</Trans>}>
          <input
            id="rec-start"
            type="date"
            value={current.startDate}
            aria-invalid={invalid === "start"}
            onChange={(event) => update({ startDate: event.target.value })}
          />
        </Field>
        <Field htmlFor="rec-end" label={<Trans>Ends on (optional)</Trans>}>
          <input
            id="rec-end"
            type="date"
            value={current.endDate}
            aria-invalid={invalid === "end"}
            onChange={(event) => update({ endDate: event.target.value })}
          />
        </Field>
      </div>

      {current.interval !== "weekly" && current.interval !== "monthly" && (
        <p className="ui-hint">
          <Trans>
            Quarterly and yearly items repeat in the month of the start date.
          </Trans>
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <div className="ui-actions">
        <Button variant="primary" type="submit" disabled={saving}>
          {editing ? <Trans>Save changes</Trans> : <Trans>Save</Trans>}
        </Button>
        {editing && (
          <button type="button" onClick={onCancelEdit}>
            <Trans>Cancel</Trans>
          </button>
        )}
        <span className="ui-hint">
          <Trans>
            Planned items for the next 12 months are created on save.
          </Trans>
        </span>
      </div>
    </form>
  );
}
