import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import type { Account } from "../accounts/accountsApi";
import { Button, Card, Field } from "../../ui";
import {
  formatRatePercent,
  fromMonthDay,
  monthName,
  parseRatePercent,
  toMonthDay,
} from "./draft";
import type {
  LevyBase,
  LevySettings,
  LevySettingsInput,
  PotOption,
} from "./levyApi";

interface Props {
  settings: LevySettings;
  pots: PotOption[];
  accounts: Account[];
  busy: boolean;
  /** Stores the settings; the page reloads everything afterwards. Rejects with the API message. */
  onSave: (input: LevySettingsInput) => Promise<void>;
}

const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1);

/**
 * Levy settings (UC-ZF-015 preconditions): rate, base, due date of the back-payment, levy pot and
 * the account the back-payment is planned on. Saving recalculates the whole page (UC step 6).
 */
export function SettingsForm({
  settings,
  pots,
  accounts,
  busy,
  onSave,
}: Props) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const due = fromMonthDay(settings.due_month_day);
  const [rate, setRate] = useState(
    settings.settings_missing
      ? ""
      : formatRatePercent(settings.levy_rate_bp, locale),
  );
  const [base, setBase] = useState<LevyBase>(settings.levy_base);
  const [month, setMonth] = useState(String(due.month));
  const [day, setDay] = useState(String(due.day));
  const [potId, setPotId] = useState(
    settings.levy_pot_id === null ? "" : String(settings.levy_pot_id),
  );
  const [accountId, setAccountId] = useState(
    settings.account_id === null ? "" : String(settings.account_id),
  );
  const [rateError, setRateError] = useState<string | null>(null);
  const [dueError, setDueError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const bp = parseRatePercent(rate);
    const monthDay = toMonthDay(Number(month), Number(day));
    setRateError(
      bp === null ? t`Enter a rate from 0 to 90, e.g. 35 or 35,5.` : null,
    );
    setDueError(
      monthDay === null ? t`Enter a day from 1 to 31 and a month.` : null,
    );
    setError(null);
    if (bp === null || monthDay === null) return;
    try {
      await onSave({
        levy_rate_bp: bp,
        levy_base: base,
        due_month_day: monthDay,
        levy_pot_id: potId === "" ? null : Number(potId),
        account_id: accountId === "" ? null : Number(accountId),
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  const openPots = pots.filter((pot) => !pot.closed);
  const payAccounts = accounts.filter(
    (account) => account.type !== "liability",
  );

  return (
    <Card as="section" aria-labelledby="levy-settings-title">
      <form className="levy-form" onSubmit={onSubmit} noValidate>
        <h2 id="levy-settings-title">
          <Trans>Settings</Trans>
        </h2>
        <div className="ui-form-grid">
          <Field
            htmlFor="levy-rate"
            label={<Trans>Levy rate (%)</Trans>}
            hint={
              <Trans>
                From your tax advisor: the share of your business income that
                income tax and SVS will claim.
              </Trans>
            }
            error={rateError}
          >
            <input
              id="levy-rate"
              className="num"
              inputMode="decimal"
              autoComplete="off"
              value={rate}
              onChange={(event) => setRate(event.target.value)}
            />
          </Field>
          <fieldset className="levy-base">
            <legend>
              <Trans>The rate applies to</Trans>
            </legend>
            <div className="ui-segmented">
              {(
                [
                  ["income", t`Business income`],
                  ["profit", t`Business profit`],
                ] as [LevyBase, string][]
              ).map(([value, label]) => (
                <label key={value}>
                  <input
                    type="radio"
                    name="levy-base"
                    value={value}
                    checked={base === value}
                    onChange={() => setBase(value)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </fieldset>
          <fieldset className="levy-due" aria-describedby="levy-due-hint">
            <legend>
              <Trans>Back-payment due on</Trans>
            </legend>
            <div className="levy-due-row">
              <select
                aria-label={t`Month`}
                value={month}
                onChange={(event) => setMonth(event.target.value)}
              >
                {MONTHS.map((m) => (
                  <option key={m} value={m}>
                    {monthName(m, locale)}
                  </option>
                ))}
              </select>
              <input
                aria-label={t`Day`}
                className="num levy-day"
                type="number"
                min={1}
                max={31}
                value={day}
                onChange={(event) => setDay(event.target.value)}
              />
            </div>
            <span id="levy-due-hint" className="ui-field-hint">
              <Trans>
                In the year after the income. The default is 1 July.
              </Trans>
            </span>
            {dueError && (
              <span role="alert" className="ui-field-error">
                {dueError}
              </span>
            )}
          </fieldset>
          <Field
            htmlFor="levy-pot"
            label={<Trans>Levy pot</Trans>}
            hint={
              <Trans>
                The savings pot that holds your levy reserve. Without a choice,
                the pot flagged as levy pot is used.
              </Trans>
            }
          >
            <select
              id="levy-pot"
              value={potId}
              onChange={(event) => setPotId(event.target.value)}
            >
              <option value="">{t`Automatic (pot flagged as levy pot)`}</option>
              {openPots.map((pot) => (
                <option key={pot.id} value={pot.id}>
                  {pot.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            htmlFor="levy-account"
            label={<Trans>Back-payment account</Trans>}
            hint={
              <Trans>The account the planned back-payment is taken from.</Trans>
            }
          >
            <select
              id="levy-account"
              value={accountId}
              onChange={(event) => setAccountId(event.target.value)}
            >
              <option value="">{t`None`}</option>
              {payAccounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="ui-actions">
          <Button variant="primary" type="submit" disabled={busy}>
            <Trans>Save and recalculate</Trans>
          </Button>
          <span className="ui-hint">
            <Trans>
              Reserve, back-payment, required revenue and both charts update
              when you save.
            </Trans>
          </span>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
      </form>
    </Card>
  );
}
