import { Trans, useLingui } from "@lingui/react/macro";
import { useState } from "react";

import { formatCents } from "../../api/client";
import { centsToInput, parseEuroInput } from "../accounts/accountsApi";
import { Badge, Button, MoneyCell, Table } from "../../ui";
import type { Proposal, ProposalLine } from "./allocationApi";

export interface ProposalCardProps {
  proposal: Proposal;
  onConfirm: (id: number, amounts: Record<number, number>) => Promise<void>;
  onSkip: (id: number) => Promise<void>;
}

/**
 * One proposal to decide (UC-ZF-014 steps 3-4): the lines with their targets, what stays free, the
 * reserve warning of UC 3a, and the two actions. Every line but the levy one can be changed before
 * confirming; the levy share is already set aside (AK7).
 */
export function ProposalCard({
  proposal,
  onConfirm,
  onSkip,
}: ProposalCardProps) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [amounts, setAmounts] = useState<Record<number, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const money = (cents: number) => formatCents(cents, locale);
  const edited = (index: number, line: ProposalLine) =>
    amounts[index] ?? centsToInput(line.amount_cents, locale);
  const parsed = proposal.lines.map((line, index) =>
    amounts[index] === undefined
      ? line.amount_cents
      : parseEuroInput(amounts[index] ?? ""),
  );
  const allocated = parsed.reduce(
    (sum, cents) => (sum === null || cents === null ? null : sum + cents),
    0 as number | null,
  );
  const remaining =
    allocated === null ? null : proposal.amount_cents - allocated;

  async function run(action: () => Promise<void>) {
    setError(null);
    setBusy(true);
    try {
      await action();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  function onConfirmClick() {
    const changed: Record<number, number> = {};
    for (const [key, text] of Object.entries(amounts)) {
      const cents = parseEuroInput(text);
      if (cents === null || cents < 0) {
        setError(t`Enter every share as an amount of 0 or more.`);
        return;
      }
      changed[Number(key)] = cents;
    }
    if (remaining !== null && remaining < 0) {
      setError(t`The shares add up to more than the income.`);
      return;
    }
    void run(() => onConfirm(proposal.id, changed));
  }

  const free = money(proposal.free_cents);
  const reduced = money(proposal.reduced_total_cents);
  const days = proposal.days_open;

  return (
    <li className="allocation-proposal">
      <div className="allocation-proposal-head">
        <span className="allocation-proposal-title">
          {proposal.text || <Trans>Income</Trans>}{" "}
          <span className="muted">
            {proposal.date} · {proposal.account_name}
          </span>
        </span>
        <span className="num">{money(proposal.amount_cents)}</span>
        {proposal.overdue && (
          <Badge tone="overdue">
            <Trans>open for {days} days</Trans>
          </Badge>
        )}
      </div>

      <Table zebra={false} className="allocation-lines">
        <thead>
          <tr>
            <th scope="col">
              <Trans>Goes to</Trans>
            </th>
            <th scope="col" className="num">
              <Trans>Amount</Trans>
            </th>
          </tr>
        </thead>
        <tbody>
          {proposal.lines.map((line, index) => (
            <tr key={index}>
              <td>
                {label(line)}
                {line.kind === "levy" && (
                  <>
                    {" "}
                    <Badge tone="ok">
                      <Trans>set aside</Trans>
                    </Badge>
                  </>
                )}
                {line.capped && (
                  <>
                    {" "}
                    <Badge tone="warn">
                      <Trans>reduced</Trans>
                    </Badge>
                  </>
                )}
                {!line.books && (
                  <>
                    {" "}
                    <span className="muted">
                      <Trans>(stays on the account)</Trans>
                    </span>
                  </>
                )}
              </td>
              <td className="num">
                {line.editable ? (
                  <input
                    className="num allocation-amount"
                    inputMode="decimal"
                    aria-label={t`Amount for ${line.target || t`the total reserve`}`}
                    value={edited(index, line)}
                    onChange={(e) =>
                      setAmounts((current) => ({
                        ...current,
                        [index]: e.target.value,
                      }))
                    }
                  />
                ) : (
                  money(line.amount_cents)
                )}
              </td>
            </tr>
          ))}
          <tr className="allocation-remaining">
            <th scope="row">
              <Trans>Stays free</Trans>
            </th>
            {remaining === null ? (
              <td className="num">—</td>
            ) : (
              <MoneyCell cents={remaining} />
            )}
          </tr>
        </tbody>
      </Table>

      {proposal.reserve_warning && (
        <p className="allocation-warning" role="alert">
          <Badge tone="warn">
            <Trans>Warning</Trans>
          </Badge>{" "}
          <Trans>
            Only {free} is free until the end of the month, so this allocation
            would eat into your reserve. You could allocate {reduced} instead.
          </Trans>
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="ui-actions">
        <Button variant="primary" disabled={busy} onClick={onConfirmClick}>
          <Trans>Confirm and book</Trans>
        </Button>
        <Button
          disabled={busy}
          onClick={() => void run(() => onSkip(proposal.id))}
        >
          <Trans>Reject</Trans>
        </Button>
      </div>
    </li>
  );
}

/** The destination of a line as the user reads it; the total reserve has no name of its own. */
function label(line: ProposalLine) {
  if (line.kind === "reserve") return <Trans>Total reserve</Trans>;
  if (line.kind === "levy")
    return <Trans>Levy pot &quot;{line.target}&quot;</Trans>;
  if (line.kind === "debt_plan")
    return <Trans>Debt &quot;{line.target}&quot;</Trans>;
  return <Trans>Pot &quot;{line.target}&quot;</Trans>;
}
