import { Trans, useLingui } from "@lingui/react/macro";
import { useId } from "react";

import { Button, Card, Field } from "../../ui";
import { DetectedTable } from "./DetectedTable";
import type { DecimalSep, Detected, Profile } from "./importApi";
import { DECIMAL_LABELS } from "./labels";
import {
  DATE_FORMATS,
  problems,
  type Problem,
  type ProfileForm,
} from "./logic";

export type ProfileChoice = number | "new";

interface Props {
  profiles: Profile[];
  choice: ProfileChoice;
  onChoice: (choice: ProfileChoice) => void;
  form: ProfileForm;
  onForm: (patch: Partial<ProfileForm>) => void;
  detected: Detected | null;
  detectError: string | null;
  busy: boolean;
  /** Refusal of the file or a failed save, shown above the buttons. */
  error: string | null;
  onBack: () => void;
  onContinue: () => void;
}

interface ColumnProps {
  id: string;
  label: string;
  value: string;
  header: string[];
  /** An optional column can stay empty. */
  optional?: boolean;
  invalid: boolean;
  onChange: (value: string) => void;
}

/** A column dropdown filled from the header; a chosen name the file does not have stays visible and is flagged. */
function ColumnSelect({
  id,
  label,
  value,
  header,
  optional,
  invalid,
  onChange,
}: ColumnProps) {
  const { t } = useLingui();
  const missing = value !== "" && header.length > 0 && !header.includes(value);
  return (
    <Field
      htmlFor={id}
      label={label}
      error={
        missing ? (
          <Trans>“{value}” is not a column of this file.</Trans>
        ) : undefined
      }
    >
      <select
        id={id}
        value={value}
        aria-invalid={invalid || missing}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{optional ? t`None` : t`Choose…`}</option>
        {missing && <option value={value}>{value}</option>}
        {header.map((name, index) => (
          <option key={`${index}-${name}`} value={name}>
            {name}
          </option>
        ))}
      </select>
    </Field>
  );
}

/**
 * Step 2 (UC-ZF-005 2, 2a): choose a stored profile or create one; the columns are dropdowns over
 * the file's header, the first rows show what they mean. A changed profile is saved with "Continue".
 */
export function ProfileStep({
  profiles,
  choice,
  onChoice,
  form,
  onForm,
  detected,
  detectError,
  busy,
  error,
  onBack,
  onContinue,
}: Props) {
  const { t, i18n } = useLingui();
  const ids = {
    profile: useId(),
    bank: useId(),
    date: useId(),
    text: useId(),
    amount: useId(),
    debit: useId(),
    credit: useId(),
    counterparty: useId(),
    dateFormat: useId(),
    decimal: useId(),
    skip: useId(),
    formats: useId(),
  };
  const header = detected?.header ?? [];
  const invalid = new Set<Problem>(problems(form));
  const blocked = invalid.size > 0;

  // Marks on the first rows: which column plays which role.
  const roles: Record<string, string[]> = {};
  const mark = (name: string, role: string) => {
    if (name) roles[name] = [...(roles[name] ?? []), role];
  };
  mark(form.date, t`Date`);
  mark(form.text, t`Text`);
  if (form.amountMode === "single") mark(form.amount, t`Amount`);
  else {
    mark(form.debit, t`Debit`);
    mark(form.credit, t`Credit`);
  }
  mark(form.counterparty, t`Counterparty`);

  return (
    <Card className="import-step">
      <div className="ui-form-grid import-grid">
        <Field htmlFor={ids.profile} label={<Trans>Profile</Trans>}>
          <select
            id={ids.profile}
            value={String(choice)}
            onChange={(event) =>
              onChoice(
                event.target.value === "new"
                  ? "new"
                  : Number(event.target.value),
              )
            }
          >
            <option value="new">{t`New profile`}</option>
            {profiles.map((profile) => (
              <option key={profile.id} value={profile.id}>
                {profile.bank_name}
              </option>
            ))}
          </select>
        </Field>
        <Field htmlFor={ids.bank} label={<Trans>Bank name</Trans>}>
          <input
            id={ids.bank}
            value={form.bankName}
            maxLength={100}
            aria-invalid={invalid.has("bank")}
            onChange={(event) => onForm({ bankName: event.target.value })}
          />
        </Field>
      </div>
      <p className="ui-hint">
        <Trans>
          Assign the columns of the file. A profile belongs to one bank and is
          reused for its next statements; a new or changed profile is saved when
          you show the preview.
        </Trans>
      </p>
      <div className="ui-form-grid import-grid">
        <ColumnSelect
          id={ids.date}
          label={t`Date column`}
          value={form.date}
          header={header}
          invalid={invalid.has("date")}
          onChange={(date) => onForm({ date })}
        />
        <ColumnSelect
          id={ids.text}
          label={t`Text column (purpose)`}
          value={form.text}
          header={header}
          invalid={invalid.has("text")}
          onChange={(text) => onForm({ text })}
        />
        <ColumnSelect
          id={ids.counterparty}
          label={t`Counterparty column`}
          value={form.counterparty}
          header={header}
          optional
          invalid={false}
          onChange={(counterparty) => onForm({ counterparty })}
        />
      </div>
      <div className="import-amounts">
        <fieldset className="ui-segmented">
          <legend className="sr-only">
            <Trans>How the amount is stored</Trans>
          </legend>
          <label>
            <input
              type="radio"
              name="import-amount-mode"
              checked={form.amountMode === "single"}
              onChange={() => onForm({ amountMode: "single" })}
            />
            {t`One amount column`}
          </label>
          <label>
            <input
              type="radio"
              name="import-amount-mode"
              checked={form.amountMode === "split"}
              onChange={() => onForm({ amountMode: "split" })}
            />
            {t`Debit and credit columns`}
          </label>
        </fieldset>
        <div className="ui-form-grid import-grid">
          {form.amountMode === "single" ? (
            <ColumnSelect
              id={ids.amount}
              label={t`Amount column`}
              value={form.amount}
              header={header}
              invalid={invalid.has("amount")}
              onChange={(amount) => onForm({ amount })}
            />
          ) : (
            <>
              <ColumnSelect
                id={ids.debit}
                label={t`Debit column (money out)`}
                value={form.debit}
                header={header}
                invalid={invalid.has("debit")}
                onChange={(debit) => onForm({ debit })}
              />
              <ColumnSelect
                id={ids.credit}
                label={t`Credit column (money in)`}
                value={form.credit}
                header={header}
                invalid={invalid.has("credit")}
                onChange={(credit) => onForm({ credit })}
              />
            </>
          )}
          <Field
            htmlFor={ids.dateFormat}
            label={<Trans>Date format</Trans>}
            hint={<Trans>Day %d, month %m, year %Y or %y; e.g. %d.%m.%Y</Trans>}
          >
            <input
              id={ids.dateFormat}
              list={ids.formats}
              value={form.dateFormat}
              maxLength={20}
              aria-invalid={invalid.has("dateFormat")}
              onChange={(event) => onForm({ dateFormat: event.target.value })}
            />
            <datalist id={ids.formats}>
              {DATE_FORMATS.map((format) => (
                <option key={format} value={format} />
              ))}
            </datalist>
          </Field>
          <Field htmlFor={ids.decimal} label={<Trans>Decimal separator</Trans>}>
            <select
              id={ids.decimal}
              value={form.decimalSep}
              onChange={(event) =>
                onForm({ decimalSep: event.target.value as DecimalSep })
              }
            >
              {(Object.keys(DECIMAL_LABELS) as DecimalSep[]).map((value) => (
                <option key={value} value={value}>
                  {i18n._(DECIMAL_LABELS[value])}
                </option>
              ))}
            </select>
          </Field>
          <Field
            htmlFor={ids.skip}
            label={<Trans>Lines before the header</Trans>}
            hint={<Trans>Some banks put a title above the column names.</Trans>}
          >
            <input
              id={ids.skip}
              type="number"
              min={0}
              max={50}
              step={1}
              value={form.skipRows}
              aria-invalid={invalid.has("skipRows")}
              onChange={(event) => onForm({ skipRows: event.target.value })}
            />
          </Field>
        </div>
      </div>
      {detectError && (
        <p role="alert" className="error">
          {detectError}
        </p>
      )}
      {detected && <DetectedTable detected={detected} roles={roles} />}
      {error && (
        <p role="alert" className="error import-refusal">
          {error}
        </p>
      )}
      <div className="ui-actions">
        <Button onClick={onBack}>
          <Trans>Back</Trans>
        </Button>
        <Button
          variant="primary"
          disabled={busy || blocked}
          onClick={onContinue}
        >
          <Trans>Show the preview</Trans>
        </Button>
      </div>
    </Card>
  );
}
