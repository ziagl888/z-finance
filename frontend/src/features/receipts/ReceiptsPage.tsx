import { Trans, useLingui } from "@lingui/react/macro";
import { useCallback, useEffect, useState } from "react";

import { MoneyCell, Table } from "../../ui";
import {
  listAccounts,
  listCategories,
  type Account,
  type Category,
} from "../accounts/accountsApi";
import { todayIso } from "../entry/draft";
import { ReceiptSettingsSection } from "./ReceiptSettingsSection";
import {
  bookReceipt,
  discardReceipt,
  listBooked,
  listReview,
  listRuns,
  type AgentRun,
  type BookInput,
  type Receipt,
} from "./receiptsApi";
import { ReviewCard } from "./ReviewCard";
import "./receipts.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Receipts (UC-ZF-013): the review list ("Check receipts") with photo and correction form, the
 * receipts booked last, the runs of the receipt agent and the settings the API books with.
 * Receipts arrive only through the agent; there is no upload here.
 */
export function ReceiptsPage() {
  const { t, i18n } = useLingui();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [review, setReview] = useState<Receipt[] | null>(null);
  const [booked, setBooked] = useState<Receipt[]>([]);
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const today = todayIso();

  const reload = useCallback(async () => {
    const [nextReview, nextBooked, nextRuns] = await Promise.all([
      listReview(),
      listBooked(),
      listRuns(),
    ]);
    setReview(nextReview);
    setBooked(nextBooked);
    setRuns(nextRuns);
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAccounts(false), listCategories()])
      .then(([loadedAccounts, loadedCategories]) => {
        if (cancelled) return;
        setAccounts(loadedAccounts);
        setCategories(loadedCategories);
      })
      .catch((reason) => !cancelled && setError(message(reason)));
    Promise.all([listReview(), listBooked(), listRuns()])
      .then(([nextReview, nextBooked, nextRuns]) => {
        if (cancelled) return;
        setReview(nextReview);
        setBooked(nextBooked);
        setRuns(nextRuns);
      })
      .catch((reason) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function onBook(receipt: Receipt, input: BookInput): Promise<boolean> {
    setError(null);
    try {
      await bookReceipt(receipt.id, input);
      const merchant = input.merchant;
      setNotice(t`Booked: ${merchant}.`);
      await reload();
      return true;
    } catch (reason) {
      setError(message(reason));
      return false;
    }
  }

  async function onDiscard(receipt: Receipt) {
    if (!window.confirm(t`Discard this receipt and its photo?`)) return;
    setError(null);
    try {
      await discardReceipt(receipt.id);
      await reload();
    } catch (reason) {
      setError(message(reason));
    }
  }

  const accountName = (id: number | null) =>
    accounts.find((account) => account.id === id)?.name ?? "—";
  const formatDate = (value: string | null) =>
    value
      ? new Date(
          value.length === 10 ? `${value}T00:00:00` : value,
        ).toLocaleString(
          i18n.locale,
          value.length === 10 ? { dateStyle: "medium" } : undefined,
        )
      : "—";

  return (
    <section className="rcp">
      <h1>
        <Trans>Receipts</Trans>
      </h1>
      <p className="rcp-intro ui-hint">
        <Trans>
          Receipt photos sent to the receipt agent are booked automatically.
          Whatever the agent was unsure about waits here until you check it.
        </Trans>
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="ui-notice" role="status">
          {notice}
        </p>
      )}

      <h2>
        <Trans>Check receipts</Trans>
      </h2>
      {review === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : review.length === 0 ? (
        <p className="ui-hint">
          <Trans>Nothing to check.</Trans>
        </p>
      ) : (
        <div className="rcp-list">
          {review.map((receipt) => (
            <ReviewCard
              key={receipt.id}
              receipt={receipt}
              accounts={accounts}
              categories={categories}
              today={today}
              onBook={onBook}
              onDiscard={onDiscard}
            />
          ))}
        </div>
      )}

      <h2>
        <Trans>Booked receipts</Trans>
      </h2>
      {booked.length === 0 ? (
        <p className="ui-hint">
          <Trans>No receipt booked yet.</Trans>
        </p>
      ) : (
        <Table>
          <thead>
            <tr>
              <th>
                <Trans>Date</Trans>
              </th>
              <th>
                <Trans>Text</Trans>
              </th>
              <th>
                <Trans>Account</Trans>
              </th>
              <th className="num">
                <Trans>Amount</Trans>
              </th>
              <th>
                <Trans>Photo</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {booked.map((receipt) => (
              <tr key={receipt.id}>
                <td>{formatDate(receipt.booking_date)}</td>
                <td>{receipt.booking_text ?? t`Booking deleted`}</td>
                <td>{accountName(receipt.booking_account_id)}</td>
                {receipt.booking_amount_cents === null ? (
                  <td className="num">—</td>
                ) : (
                  <MoneyCell cents={receipt.booking_amount_cents} />
                )}
                <td>
                  <a href={receipt.photo_url} target="_blank" rel="noreferrer">
                    <Trans>Open</Trans>
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <h2>
        <Trans>Agent runs</Trans>
      </h2>
      {runs.length === 0 ? (
        <p className="ui-hint">
          <Trans>The receipt agent has not run yet.</Trans>
        </p>
      ) : (
        <Table>
          <thead>
            <tr>
              <th>
                <Trans>Started</Trans>
              </th>
              <th className="num">
                <Trans>Booked</Trans>
              </th>
              <th className="num">
                <Trans>To check</Trans>
              </th>
              <th className="num">
                <Trans>Duplicates</Trans>
              </th>
              <th>
                <Trans>Note</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>
                  {formatDate(run.started_at)}
                  {!run.finished_at && (
                    <span className="rcp-sub">
                      <Trans>not finished</Trans>
                    </span>
                  )}
                </td>
                <td className="num">{run.booked}</td>
                <td className="num">{run.review}</td>
                <td className="num">{run.duplicate}</td>
                <td>{run.note ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}

      <ReceiptSettingsSection accounts={accounts} />
    </section>
  );
}
