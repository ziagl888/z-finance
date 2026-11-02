import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { Button, Field } from "../../ui";
import { AREAS, type Account } from "../accounts/accountsApi";
import { AREA_LABELS } from "../accounts/labels";
import {
  createDebt,
  DEBT_INTERVALS,
  updateDebt,
  type Debt,
  type DebtInterval,
} from "./debtsApi";
import {
  draftFrom,
  emptyDraft,
  toInput,
  type DebtDraft,
  type Invalid,
} from "./draft";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Props {
  /** Active accounts the instalments can be paid from (liability accounts left out). */
  accounts: Account[];
  /** Debt opened for editing, or null for a new one. */
  editing: Debt | null;
  onSaved: (debt: Debt) => Promise<void>;
  onCancelEdit: () => void;
}

/**
 * Create or edit a debt (UC-ZF-010 step 1): creditor, area, total, already repaid, a fixed
 * instalment with interval, first due date and paying account — or none, with a wished end
 * (UC 1a) — plus interest rate and contract end as information. A failed save keeps every value.
 */
export function DebtForm({ accounts, editing, onSaved, onCancelEdit }: Props) {
  const { t, i18n } = useLingui();
  const firstAccount = accounts[0] ? String(accounts[0].id) : "";
  const [draft, setDraft] = useState<DebtDraft>(() => emptyDraft(firstAccount));
  const [shownEditing, setShownEditing] = useState<Debt | null>(null);
  const [invalid, setInvalid] = useState<Invalid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (editing !== shownEditing) {
    setShownEditing(editing);
    setDraft(
      editing ? draftFrom(editing, i18n.locale) : emptyDraft(firstAccount),
    );
    setInvalid(null);
    setError(null);
  }

  // The account list may arrive after the first render.
  const payAccountId = accounts.some((a) => String(a.id) === draft.payAccountId)
    ? draft.payAccountId
    : firstAccount;
  const current = { ...draft, payAccountId };
  const update = (changes: Partial<DebtDraft>) =>
    setDraft((previous) => ({ ...previous, payAccountId, ...changes }));

  const messages: Record<Invalid, string> = {
    creditor: t`Enter the creditor.`,
    total: t`Enter a total amount greater than 0, e.g. 5000.`,
    repaid: t`The amount already repaid must be less than the total.`,
    installment: t`Enter an instalment greater than 0, e.g. 250.`,
    firstDue: t`Choose the first due date.`,
    payAccount: t`Choose the account the instalments are paid from.`,
    interestRate: t`Enter the interest rate as a number, e.g. 4.5.`,
  };
  const intervalLabels: Record<DebtInterval, string> = {
    monthly: t`Monthly`,
    quarterly: t`Quarterly`,
    yearly: t`Yearly`,
  };

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
      const { repaid_cents, ...fields } = result.input;
      const saved = editing
        ? await updateDebt(editing.id, fields)
        : await createDebt({ ...fields, repaid_cents });
      if (!editing) setDraft(emptyDraft(firstAccount));
      await onSaved(saved);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  }

  const editingName = editing?.creditor ?? null;
  const field = (name: Invalid) =>
    invalid === name ? { "aria-invalid": true as const } : {};

  return (
    <form className="debt-form" onSubmit={onSubmit} noValidate>
      <div className="debt-head">
        <h2>
          {editingName !== null ? (
            <Trans>Edit debt {editingName}</Trans>
          ) : (
            <Trans>New debt</Trans>
          )}
        </h2>
      </div>
      <div className="ui-form-grid">
        <Field htmlFor="debt-creditor" label={<Trans>Creditor</Trans>}>
          <input
            id="debt-creditor"
            value={current.creditor}
            maxLength={100}
            onChange={(e) => update({ creditor: e.target.value })}
            {...field("creditor")}
          />
        </Field>
        <Field htmlFor="debt-area" label={<Trans>Area</Trans>}>
          <select
            id="debt-area"
            value={current.area}
            onChange={(e) =>
              update({ area: e.target.value as DebtDraft["area"] })
            }
          >
            {AREAS.map((area) => (
              <option key={area} value={area}>
                {i18n._(AREA_LABELS[area])}
              </option>
            ))}
          </select>
        </Field>
        <Field htmlFor="debt-total" label={<Trans>Total amount</Trans>}>
          <input
            id="debt-total"
            className="num"
            inputMode="decimal"
            value={current.total}
            onChange={(e) => update({ total: e.target.value })}
            {...field("total")}
          />
        </Field>
        {!editing && (
          <Field htmlFor="debt-repaid" label={<Trans>Already repaid</Trans>}>
            <input
              id="debt-repaid"
              className="num"
              inputMode="decimal"
              placeholder="0"
              value={current.repaid}
              onChange={(e) => update({ repaid: e.target.value })}
              {...field("repaid")}
            />
          </Field>
        )}
      </div>

      <label className="debt-check">
        <input
          type="checkbox"
          checked={current.hasInstallment}
          onChange={(e) => update({ hasInstallment: e.target.checked })}
        />
        <Trans>Fixed instalment</Trans>
      </label>

      {current.hasInstallment ? (
        <div className="ui-form-grid">
          <Field htmlFor="debt-installment" label={<Trans>Instalment</Trans>}>
            <input
              id="debt-installment"
              className="num"
              inputMode="decimal"
              value={current.installment}
              onChange={(e) => update({ installment: e.target.value })}
              {...field("installment")}
            />
          </Field>
          <Field htmlFor="debt-interval" label={<Trans>Interval</Trans>}>
            <select
              id="debt-interval"
              value={current.interval}
              onChange={(e) =>
                update({ interval: e.target.value as DebtInterval })
              }
            >
              {DEBT_INTERVALS.map((interval) => (
                <option key={interval} value={interval}>
                  {intervalLabels[interval]}
                </option>
              ))}
            </select>
          </Field>
          <Field htmlFor="debt-first-due" label={<Trans>First due date</Trans>}>
            <input
              id="debt-first-due"
              type="date"
              value={current.firstDue}
              onChange={(e) => update({ firstDue: e.target.value })}
              {...field("firstDue")}
            />
          </Field>
          <Field htmlFor="debt-pay-account" label={<Trans>Paid from</Trans>}>
            <select
              id="debt-pay-account"
              value={current.payAccountId}
              onChange={(e) => update({ payAccountId: e.target.value })}
              {...field("payAccount")}
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      ) : (
        <div className="ui-form-grid">
          <Field
            htmlFor="debt-target-end"
            label={<Trans>Paid off by (optional)</Trans>}
          >
            <input
              id="debt-target-end"
              type="date"
              value={current.targetEnd}
              onChange={(e) => update({ targetEnd: e.target.value })}
            />
          </Field>
        </div>
      )}

      <div className="ui-form-grid">
        <Field
          htmlFor="debt-interest"
          label={<Trans>Interest rate % (information)</Trans>}
        >
          <input
            id="debt-interest"
            inputMode="decimal"
            value={current.interestRate}
            onChange={(e) => update({ interestRate: e.target.value })}
            {...field("interestRate")}
          />
        </Field>
        <Field
          htmlFor="debt-contract-end"
          label={<Trans>Contract end (information)</Trans>}
        >
          <input
            id="debt-contract-end"
            type="date"
            value={current.contractEnd}
            onChange={(e) => update({ contractEnd: e.target.value })}
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
          {editing ? <Trans>Save changes</Trans> : <Trans>Save</Trans>}
        </Button>
        {editing && (
          <button type="button" onClick={onCancelEdit}>
            <Trans>Cancel</Trans>
          </button>
        )}
      </div>
    </form>
  );
}
