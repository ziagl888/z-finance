import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState, type FormEvent } from "react";

import { formatCents } from "../../api/client";
import { Badge, Button, Card, Field, MoneyCell, Table } from "../../ui";
import { parseEuroInput, type Account } from "../accounts/accountsApi";
import { todayIso } from "../entry/draft";
import { formatDate } from "../recurring/draft";
import { getHistory, payExtra, type Debt, type HistoryEntry } from "./debtsApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Props {
  debt: Debt;
  /** Active accounts a special payment can come from. */
  accounts: Account[];
  /** All accounts, for the names in the history. */
  allAccounts: Account[];
  /** Called after a payment (reload the overview). */
  onChanged: () => Promise<void>;
}

/**
 * One debt: its figures, a special payment (UC-ZF-010 step 5, capped at the remaining amount,
 * 5a) and the history of its liability account — instalments, special payments, increases.
 */
export function DebtDetail({ debt, accounts, allAccounts, onChanged }: Props) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [amount, setAmount] = useState("");
  const [date, setDate] = useState(todayIso());
  const [fromId, setFromId] = useState(
    debt.pay_account_id === null ? "" : String(debt.pay_account_id),
  );
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // Reload whenever the debt changes (another debt selected, or its figures after a payment).
  useEffect(() => {
    let cancelled = false;
    getHistory(debt.id)
      .then((rows) => !cancelled && setHistory(rows))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [debt]);

  const from = accounts.some((a) => String(a.id) === fromId)
    ? fromId
    : accounts[0]
      ? String(accounts[0].id)
      : "";
  const accountName = (id: number | null) =>
    allAccounts.find((a) => a.id === id)?.name ?? "";

  async function onPay(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const cents = parseEuroInput(amount);
    setNotice(null);
    if (cents === null || cents <= 0) {
      setError(t`Enter an amount greater than 0, e.g. 500.`);
      return;
    }
    if (from === "") {
      setError(t`Choose an account to pay from.`);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const result = await payExtra(debt.id, cents, date, Number(from));
      const booked = money(result.booked_cents);
      setNotice(
        result.capped
          ? t`The payment was more than the remaining amount. Only ${booked} were booked; the rest stays on the account.`
          : t`${booked} booked.`,
      );
      setAmount("");
      await onChanged();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setSaving(false);
    }
  }

  const creditor = debt.creditor;
  const total = money(debt.total_amount_cents);
  const paid = money(debt.paid_cents);

  return (
    <Card as="section" className="debt-detail" aria-label={creditor}>
      <h2>{creditor}</h2>
      <p className="debt-figures">
        <Trans>
          {paid} of {total} repaid.
        </Trans>{" "}
        {debt.closed ? (
          <strong>
            <Trans>Paid off.</Trans>
          </strong>
        ) : (
          debt.plan && (
            <PlanText
              count={debt.plan.count}
              next={formatDate(debt.plan.next_due, i18n.locale)}
              end={formatDate(debt.plan.end, i18n.locale)}
              last={money(debt.plan.last_installment_cents)}
            />
          )
        )}
      </p>

      {!debt.closed && (
        <form className="debt-pay" onSubmit={onPay} noValidate>
          <h3>
            <Trans>Special payment</Trans>
          </h3>
          <div className="ui-form-grid">
            <Field htmlFor="debt-pay-amount" label={<Trans>Amount</Trans>}>
              <input
                id="debt-pay-amount"
                className="num"
                inputMode="decimal"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </Field>
            <Field htmlFor="debt-pay-date" label={<Trans>Date</Trans>}>
              <input
                id="debt-pay-date"
                type="date"
                max={todayIso()}
                value={date}
                onChange={(e) => setDate(e.target.value)}
              />
            </Field>
            <Field htmlFor="debt-pay-from" label={<Trans>From account</Trans>}>
              <select
                id="debt-pay-from"
                value={from}
                onChange={(e) => setFromId(e.target.value)}
              >
                {accounts.map((account) => (
                  <option key={account.id} value={account.id}>
                    {account.name}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="ui-actions">
            <Button
              variant="primary"
              type="submit"
              disabled={saving || from === ""}
            >
              <Trans>Book special payment</Trans>
            </Button>
          </div>
        </form>
      )}
      {notice && (
        <p role="status" className="ui-notice">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <h3>
        <Trans>History</Trans>
      </h3>
      {history === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : history.length === 0 ? (
        <p className="ui-hint">
          <Trans>No bookings yet.</Trans>
        </p>
      ) : (
        <Table>
          <thead>
            <tr>
              <th>
                <Trans>Date</Trans>
              </th>
              <th>
                <Trans>Booking</Trans>
              </th>
              <th className="num">
                <Trans>Amount</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {history.map((row) => (
              <tr
                key={row.id}
                className={row.status === "booked" ? "" : "debt-row-done"}
              >
                <td>{formatDate(row.date, i18n.locale)}</td>
                <td>
                  {row.text}
                  {row.transfer_account_id !== null && (
                    <span className="debt-sub">
                      {accountName(row.transfer_account_id)}
                    </span>
                  )}
                  {row.status !== "booked" && (
                    <Badge
                      tone={row.status === "planned" ? "planned" : "skipped"}
                    >
                      {row.status === "planned" ? (
                        <Trans>planned</Trans>
                      ) : (
                        <Trans>skipped</Trans>
                      )}
                    </Badge>
                  )}
                </td>
                <MoneyCell cents={row.amount_cents} />
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </Card>
  );
}

function PlanText(props: {
  count: number;
  next: string;
  end: string;
  last: string;
}) {
  const { count, next, end, last } = props;
  return (
    <Trans>
      {count} instalments left, next on {next}, paid off on {end} (last
      instalment {last}).
    </Trans>
  );
}
