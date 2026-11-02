import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";

import { formatCents } from "../../api/client";
import {
  Badge,
  Button,
  Card,
  MoneyCell,
  Table,
  type BadgeTone,
} from "../../ui";
import { formatDate } from "../recurring/draft";
import {
  getAllocations,
  type Allocation,
  type AllocationKind,
  type Pot,
  type PotStatus,
} from "./potsApi";

export type PotAction =
  | "deposit"
  | "withdraw"
  | "move"
  | "expense"
  | "plan"
  | "edit"
  | "close"
  | "delete";

interface Props {
  pot: Pot;
  /** Whether another open pot exists on the account (a move needs a target). */
  canMove: boolean;
  onAction: (action: PotAction, pot: Pot) => void;
}

const STATUS_TONE: Record<PotStatus, BadgeTone> = {
  on_track: "ok",
  behind: "overdue",
  reached: "ok",
};

/**
 * One pot (UC-ZF-011 step 4): balance, target, progress as a bar and a number, expected reach,
 * status and the monthly rate — for a pot behind plan the new rate (4a) — its place in the
 * emergency order or the lock, and the actions. Every figure comes from the API.
 */
export function PotCard({ pot, canMove, onAction }: Props) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [history, setHistory] = useState<Allocation[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const statusLabels: Record<PotStatus, string> = {
    on_track: t`On track`,
    behind: t`Behind plan`,
    reached: t`Reached`,
  };
  const kindLabels: Record<AllocationKind, string> = {
    deposit: t`Assigned`,
    withdraw: t`Taken out`,
    transfer_in: t`Moved in`,
    transfer_out: t`Moved out`,
    emergency: t`Emergency`,
  };

  // Load the movements when the history opens and again after a change of the balance.
  useEffect(() => {
    if (!showHistory) return;
    let cancelled = false;
    getAllocations(pot.id)
      .then((rows) => {
        if (cancelled) return;
        setHistory(rows);
        setHistoryError(null);
      })
      .catch(
        (reason: unknown) =>
          !cancelled &&
          setHistoryError(
            reason instanceof Error ? reason.message : String(reason),
          ),
      );
    return () => {
      cancelled = true;
    };
  }, [showHistory, pot.id, pot.balance_cents]);

  const target = pot.target_amount_cents;
  const percent = Math.max(0, Math.min(100, pot.progress_pct ?? 0));
  const name = pot.name;
  const targetText = target === null ? "" : money(target);
  const rate =
    pot.monthly_rate_cents === null ? "" : money(pot.monthly_rate_cents);
  const planned =
    pot.savings_plan_amount_cents === null
      ? ""
      : money(pot.savings_plan_amount_cents);
  const rank = pot.emergency_rank;
  const balance = money(pot.balance_cents);
  const targetDay = pot.target_date
    ? formatDate(pot.target_date, i18n.locale)
    : "";
  const reach = pot.expected_reach
    ? formatDate(pot.expected_reach, i18n.locale)
    : "";

  return (
    <Card as="article" className="pot-card" aria-label={name}>
      <header className="pot-card-head">
        <h3>{pot.name}</h3>
        <span className="pot-badges">
          {pot.status && (
            <Badge tone={STATUS_TONE[pot.status]}>
              {statusLabels[pot.status]}
            </Badge>
          )}
          {pot.locked && (
            <Badge>
              <Trans>Locked</Trans>
            </Badge>
          )}
          {pot.is_levy_pot && (
            <Badge>
              <Trans>Levy reserve</Trans>
            </Badge>
          )}
        </span>
      </header>

      <p className="pot-balance num">{balance}</p>

      {target === null ? (
        <p className="ui-hint">
          <Trans>No target amount: only the balance and its history.</Trans>
        </p>
      ) : (
        <>
          <div className="pot-progress">
            <progress
              className="pot-bar"
              max={100}
              value={percent}
              data-status={pot.status ?? "none"}
              aria-label={t`Progress of ${name}`}
            />
            <span className="pot-progress-text num">
              {`${pot.progress_pct ?? 0} %`}
            </span>
          </div>
          <p className="pot-line">
            <Trans>
              {balance} of {targetText}
            </Trans>
            {pot.target_date && (
              <>
                {" · "}
                <Trans>by {targetDay}</Trans>
              </>
            )}
          </p>
        </>
      )}

      {pot.monthly_rate_cents !== null && pot.status !== "reached" && (
        <p className="pot-line">
          {pot.status === "behind" ? (
            <Trans>Behind plan: needs {rate} a month from now on.</Trans>
          ) : (
            <Trans>Needs {rate} a month.</Trans>
          )}
        </p>
      )}
      {pot.expected_reach && (
        <p className="pot-line">
          <Trans>Expected to be reached on {reach}.</Trans>
        </p>
      )}
      {pot.savings_plan_id !== null && (
        <p className="pot-line">
          <Trans>Savings plan: {planned} a month.</Trans>
        </p>
      )}
      {pot.plan_expense && (
        <p className="pot-line">
          <Trans>The expense is planned on the target date.</Trans>
        </p>
      )}
      <p className="pot-line pot-rank">
        {pot.locked ? (
          <Trans>Locked: never used in an emergency.</Trans>
        ) : rank === null ? null : (
          <Trans>Emergency rank {rank}</Trans>
        )}
      </p>

      <div className="pot-actions">
        <Button onClick={() => onAction("deposit", pot)}>
          <Trans>Add money</Trans>
        </Button>
        <Button onClick={() => onAction("withdraw", pot)}>
          <Trans>Take out</Trans>
        </Button>
        <Button onClick={() => onAction("move", pot)} disabled={!canMove}>
          <Trans>Move money</Trans>
        </Button>
        <Button onClick={() => onAction("expense", pot)}>
          <Trans>Assign expense</Trans>
        </Button>
        <Button onClick={() => onAction("plan", pot)}>
          <Trans>Savings plan</Trans>
        </Button>
        <Button
          aria-expanded={showHistory}
          onClick={() => setShowHistory((open) => !open)}
        >
          <Trans>History</Trans>
        </Button>
        <Button onClick={() => onAction("edit", pot)}>
          <Trans>Edit</Trans>
        </Button>
        <Button onClick={() => onAction("close", pot)}>
          <Trans>Close</Trans>
        </Button>
        <Button variant="danger" onClick={() => onAction("delete", pot)}>
          <Trans>Delete</Trans>
        </Button>
      </div>

      {showHistory && (
        <div className="pot-history">
          {historyError && (
            <p role="alert" className="error">
              {historyError}
            </p>
          )}
          {history === null && !historyError && (
            <p>
              <Trans>Loading…</Trans>
            </p>
          )}
          {history !== null && history.length === 0 && (
            <p className="ui-hint">
              <Trans>No movements yet.</Trans>
            </p>
          )}
          {history !== null && history.length > 0 && (
            <Table>
              <thead>
                <tr>
                  <th>
                    <Trans>Date</Trans>
                  </th>
                  <th>
                    <Trans>Movement</Trans>
                  </th>
                  <th className="num">
                    <Trans>Amount</Trans>
                  </th>
                </tr>
              </thead>
              <tbody>
                {history.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDate(row.date, i18n.locale)}</td>
                    <td>{kindLabels[row.kind]}</td>
                    <MoneyCell cents={row.amount_cents} />
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </div>
      )}
    </Card>
  );
}
