import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { formatCents } from "../../api/client";
import { Button, Dialog, Field, MoneyCell, Table } from "../../ui";
import {
  centsToInput,
  parseEuroInput,
  type Account,
} from "../accounts/accountsApi";
import { formatDate } from "../recurring/draft";
import { orderedPots, sumLines } from "./draft";
import {
  confirmEmergency,
  getProposal,
  type EmergencyProposal,
  type Pot,
} from "./potsApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Props {
  /** Accounts that have pots. */
  accounts: Account[];
  pots: Pot[];
  initialAccountId: number;
  onDone: (notice: string) => Promise<void>;
  onClose: () => void;
}

/**
 * Emergency withdrawal (UC-ZF-011 5b, AK6): the amount needed → a proposal in rank order with the
 * new balance and the moved target date of every pot → the lines can be changed by hand → confirm.
 * Every unlocked pot of the account has a line, so the distribution can be changed freely; locked
 * pots are never part of it (the way to them is "Take out" on the pot, which asks first, 5c). A
 * date the proposal calculated is shown only while its amount is unchanged; a confirmed edit is
 * recalculated by the backend.
 */
export function EmergencyDialog({
  accounts,
  pots,
  initialAccountId,
  onDone,
  onClose,
}: Props) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [accountId, setAccountId] = useState(String(initialAccountId));
  const [amount, setAmount] = useState("");
  const [proposal, setProposal] = useState<EmergencyProposal | null>(null);
  const [lines, setLines] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const ranked = orderedPots(pots, Number(accountId));
  const lockedCount = pots.filter(
    (p) => p.account_id === Number(accountId) && !p.closed && p.locked,
  ).length;
  const total = sumLines(ranked.map((pot) => lines[pot.id] ?? ""));

  function reset(changes: { accountId?: string; amount?: string }) {
    if (changes.accountId !== undefined) setAccountId(changes.accountId);
    if (changes.amount !== undefined) setAmount(changes.amount);
    setProposal(null);
    setLines({});
    setError(null);
  }

  async function onPropose(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const cents = parseEuroInput(amount);
    if (cents === null || cents <= 0) {
      setError(t`Enter an amount greater than 0, e.g. 500.`);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const result = await getProposal(Number(accountId), cents);
      setProposal(result);
      setLines(
        Object.fromEntries(
          result.lines.map((line) => [
            line.pot_id,
            centsToInput(line.amount_cents, i18n.locale),
          ]),
        ),
      );
    } catch (reason) {
      setProposal(null);
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function onConfirm() {
    if (busy || total === null) {
      setError(t`Enter every amount as 0 or more, e.g. 300.`);
      return;
    }
    const send = ranked
      .map((pot) => ({
        pot_id: pot.id,
        amount_cents: parseEuroInput(lines[pot.id] ?? "") ?? 0,
      }))
      .filter((line) => line.amount_cents > 0);
    if (send.length === 0) {
      setError(t`Enter an amount for at least one pot.`);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await confirmEmergency(Number(accountId), send);
      const shown = money(total);
      await onDone(t`Emergency withdrawal of ${shown} booked.`);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  const requested = proposal ? money(proposal.amount_cents) : "";
  const covered = proposal ? money(proposal.covered_cents) : "";
  const missing = proposal ? money(proposal.shortfall_cents) : "";
  const summed = total === null ? "" : money(total);

  return (
    <Dialog
      label={t`Emergency withdrawal`}
      className="pot-dialog pot-emergency"
      onClose={onClose}
    >
      <h2>
        <Trans>Emergency withdrawal</Trans>
      </h2>
      <form onSubmit={onPropose} noValidate className="pot-emergency-ask">
        <div className="ui-form-grid">
          <Field htmlFor="pot-emergency-account" label={<Trans>Account</Trans>}>
            <select
              id="pot-emergency-account"
              value={accountId}
              onChange={(e) => reset({ accountId: e.target.value })}
            >
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.name}
                </option>
              ))}
            </select>
          </Field>
          <Field
            htmlFor="pot-emergency-amount"
            label={<Trans>Amount needed</Trans>}
            error={proposal === null ? error : undefined}
          >
            <input
              id="pot-emergency-amount"
              className="num"
              inputMode="decimal"
              autoFocus
              value={amount}
              onChange={(e) => reset({ amount: e.target.value })}
              aria-invalid={proposal === null && error ? true : undefined}
            />
          </Field>
        </div>
        <div className="ui-actions">
          <Button variant="primary" type="submit" disabled={busy}>
            <Trans>Show proposal</Trans>
          </Button>
          <Button onClick={onClose}>
            <Trans>Cancel</Trans>
          </Button>
        </div>
      </form>

      {proposal && (
        <div className="pot-emergency-result">
          <h3>
            <Trans>Proposal in emergency order</Trans>
          </h3>
          <Table className="pot-emergency-table">
            <thead>
              <tr>
                <th className="num">
                  <Trans>Rank</Trans>
                </th>
                <th>
                  <Trans>Pot</Trans>
                </th>
                <th className="num">
                  <Trans>Balance</Trans>
                </th>
                <th className="num">
                  <Trans>Take</Trans>
                </th>
                <th className="num">
                  <Trans>New balance</Trans>
                </th>
                <th>
                  <Trans>Target date</Trans>
                </th>
              </tr>
            </thead>
            <tbody>
              {ranked.map((pot, index) => {
                const typed = lines[pot.id] ?? "";
                const cents = typed.trim() === "" ? 0 : parseEuroInput(typed);
                const line = proposal.lines.find((l) => l.pot_id === pot.id);
                // The proposal's date holds only for the amount it was calculated for.
                const shifted =
                  line && cents === line.amount_cents
                    ? line.shifted_target_date
                    : null;
                const name = pot.name;
                return (
                  <tr key={pot.id}>
                    <td className="num">{index + 1}</td>
                    <td>{pot.name}</td>
                    <MoneyCell cents={pot.balance_cents} />
                    <td className="num">
                      <input
                        className="num pot-emergency-input"
                        inputMode="decimal"
                        aria-label={t`Amount to take from ${name}`}
                        value={typed}
                        onChange={(e) =>
                          setLines((now) => ({
                            ...now,
                            [pot.id]: e.target.value,
                          }))
                        }
                        aria-invalid={
                          cents === null || cents < 0 ? true : undefined
                        }
                      />
                    </td>
                    {cents === null ? (
                      <td className="num">—</td>
                    ) : (
                      <MoneyCell cents={pot.balance_cents - cents} />
                    )}
                    <td>
                      {pot.target_date === null
                        ? "—"
                        : shifted !== null
                          ? `${formatDate(pot.target_date, i18n.locale)} → ${formatDate(shifted, i18n.locale)}`
                          : formatDate(pot.target_date, i18n.locale)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <p>
            <Trans>
              {summed} of {requested} needed. The proposal covers {covered}.
            </Trans>
          </p>
          {proposal.shortfall_cents > 0 && (
            <p role="status" className="error">
              <Trans>
                The unlocked pots are {missing} short of the amount.
              </Trans>
            </p>
          )}
          {lockedCount > 0 && (
            <p className="ui-hint">
              <Trans>
                Locked pots are never part of an emergency withdrawal. To use
                one, choose Take out on the pot; it asks for a confirmation
                first.
              </Trans>
            </p>
          )}
          <p className="ui-hint">
            <Trans>
              Target dates move back when money is taken; changed amounts are
              recalculated when you confirm.
            </Trans>
          </p>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <div className="ui-actions">
            <Button
              variant="primary"
              onClick={() => void onConfirm()}
              disabled={busy}
            >
              <Trans>Confirm withdrawal</Trans>
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
