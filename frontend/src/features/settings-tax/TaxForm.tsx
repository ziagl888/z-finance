import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { Button, Card, Field } from "../../ui";
import type { Account } from "../accounts/accountsApi";
import { DueDatesField } from "./DueDatesField";
import { draftFrom, toInput, type Invalid, type TaxDraft } from "./draft";
import { putTaxSettings, type TaxSettings } from "./taxApi";

interface Props {
  /** Active accounts the payments can be made from (liability accounts left out). */
  accounts: Account[];
  settings: TaxSettings;
  onSaved: (settings: TaxSettings) => void;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The tax form (UC-ZF-008 steps 1 and 5): the business account, the amount per due date and the
 * due dates of the income tax prepayment and of the SVS contribution. An empty amount means "not
 * entered" (UC 1a). A failed save keeps every value.
 */
export function TaxForm({ accounts, settings, onSaved }: Props) {
  const { t, i18n } = useLingui();
  const first = accounts.find((a) => a.area === "business") ?? accounts[0];
  const fallback = first ? String(first.id) : "";
  const [draft, setDraft] = useState<TaxDraft>(() =>
    draftFrom(settings, i18n.locale, fallback),
  );
  const [invalid, setInvalid] = useState<Invalid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const update = (changes: Partial<TaxDraft>) => {
    setNotice(null);
    setDraft((previous) => ({ ...previous, ...changes }));
  };
  const messages: Record<Invalid, string> = {
    est: t`Enter an amount greater than 0, e.g. 1500, or leave it empty.`,
    svs: t`Enter an amount greater than 0, e.g. 600, or leave it empty.`,
    account: t`Choose the account the payments are made from.`,
    estDates: t`Enter each income tax due date only once.`,
    svsDates: t`Enter each SVS due date only once.`,
  };

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const result = toInput(draft);
    setNotice(null);
    if ("invalid" in result) {
      setInvalid(result.invalid);
      setError(messages[result.invalid]);
      return;
    }
    setInvalid(null);
    setError(null);
    setSaving(true);
    try {
      const saved = await putTaxSettings(result.input);
      setDraft(draftFrom(saved, i18n.locale, fallback));
      onSaved(saved);
      setNotice(t`Saved.`);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  }

  const field = (name: Invalid) =>
    invalid === name ? { "aria-invalid": true as const } : {};

  return (
    <Card>
      <form className="tax-form" onSubmit={onSubmit} noValidate>
        <Field
          htmlFor="tax-account"
          label={<Trans>Paid from (business account)</Trans>}
        >
          <select
            id="tax-account"
            value={draft.accountId}
            onChange={(e) => update({ accountId: e.target.value })}
            {...field("account")}
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </Field>

        <div className="tax-block">
          <h2>
            <Trans>Income tax prepayment (ESt-VZ)</Trans>
          </h2>
          <Field htmlFor="tax-est" label={<Trans>Amount per due date</Trans>}>
            <input
              id="tax-est"
              className="num"
              inputMode="decimal"
              value={draft.est}
              onChange={(e) => update({ est: e.target.value })}
              {...field("est")}
            />
          </Field>
          <DueDatesField
            id="tax-est-date"
            label={t`Due dates`}
            dates={draft.estDates}
            invalid={invalid === "estDates"}
            onChange={(estDates) => update({ estDates })}
          />
        </div>

        <div className="tax-block">
          <h2>
            <Trans>SVS contribution</Trans>
          </h2>
          <Field htmlFor="tax-svs" label={<Trans>Amount per due date</Trans>}>
            <input
              id="tax-svs"
              className="num"
              inputMode="decimal"
              value={draft.svs}
              onChange={(e) => update({ svs: e.target.value })}
              {...field("svs")}
            />
          </Field>
          <DueDatesField
            id="tax-svs-date"
            label={t`Due dates`}
            dates={draft.svsDates}
            invalid={invalid === "svsDates"}
            onChange={(svsDates) => update({ svsDates })}
          />
        </div>

        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="ui-actions">
          <Button variant="primary" type="submit" disabled={saving}>
            <Trans>Save</Trans>
          </Button>
          <span role="status" className="ui-notice">
            {notice}
          </span>
        </div>
      </form>
    </Card>
  );
}
