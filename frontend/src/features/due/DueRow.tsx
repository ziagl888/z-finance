import { Plural, Trans, useLingui } from "@lingui/react/macro";
import { useState, type KeyboardEvent } from "react";

import { Badge, Button } from "../../ui";
import { formatDate } from "../recurring/draft";
import type { Account } from "../accounts/accountsApi";
import { confirmBody, rowDraft, tomorrow, type Invalid } from "./draft";
import { confirmDue, postponeDue, skipDue, type DueItem } from "./dueApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Props {
  item: DueItem;
  accounts: Account[];
  /** Called after a change was saved; `what` names it for the status line. */
  onChanged: (
    what: "booked" | "postponed" | "skipped",
    item: DueItem,
  ) => Promise<void>;
}

/**
 * One planned item of the review (UC-ZF-004): amount and booking date are editable in the row,
 * Enter (in a field or on the row) confirms, Postpone asks for a later date, Skip drops an item
 * that will not happen. An item more than 30 days overdue says so in words and with a mark, not
 * by colour alone (UC 1a).
 */
export function DueRow({ item, accounts, onChanged }: Props) {
  const { t, i18n } = useLingui();
  const [draft, setDraft] = useState(() => rowDraft(item, i18n.locale));
  const [postponeTo, setPostponeTo] = useState<string | null>(null);
  const [invalid, setInvalid] = useState<Invalid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const accountName = (id: number | null) =>
    accounts.find((a) => a.id === id)?.name ?? "—";
  const where =
    item.transfer_account_id === null
      ? accountName(item.account_id)
      : `${accountName(item.account_id)} → ${accountName(item.transfer_account_id)}`;
  // The amount field has no sign, so the kind says which way the money goes.
  const kind =
    item.transfer_account_id !== null
      ? t`Transfer`
      : item.amount_cents < 0
        ? t`Expense`
        : t`Income`;
  const days = item.days_overdue;
  const planned = formatDate(item.date, i18n.locale);

  async function run(change: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await change();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  function confirm() {
    const result = confirmBody(item, draft);
    if ("invalid" in result) {
      setInvalid(result.invalid);
      setError(
        result.invalid === "amount"
          ? t`Enter an amount greater than 0.`
          : t`Enter a booking date.`,
      );
      return;
    }
    setInvalid(null);
    void run(async () => {
      await confirmDue(item.id, result.body);
      await onChanged("booked", item);
    });
  }

  function postpone() {
    if (postponeTo === null) return;
    void run(async () => {
      await postponeDue(item.id, postponeTo);
      await onChanged("postponed", item);
    });
  }

  function skip() {
    if (
      !window.confirm(
        t`Skip this item? It will count towards neither the balance nor the forecast; the recurring item goes on.`,
      )
    ) {
      return;
    }
    void run(async () => {
      await skipDue(item.id);
      await onChanged("skipped", item);
    });
  }

  function onKeyDown(event: KeyboardEvent<HTMLTableRowElement>) {
    const target = event.target as HTMLElement;
    if (busy || target.tagName === "BUTTON") return;
    if (event.key === "Escape" && postponeTo !== null) {
      setPostponeTo(null);
      return;
    }
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (postponeTo === null) confirm();
    else postpone();
  }

  return (
    <>
      <tr
        data-due-row=""
        tabIndex={0}
        className={item.long_overdue ? "due-row due-row-late" : "due-row"}
        onKeyDown={onKeyDown}
      >
        <td>
          {planned}
          {item.moved && (
            <Badge>
              <Trans>changed</Trans>
            </Badge>
          )}
          {days > 0 && (
            <span className="due-sub">
              <Plural value={days} one="# day overdue" other="# days overdue" />
            </span>
          )}
          {item.long_overdue && (
            <strong className="due-late">
              <span aria-hidden="true">⚠ </span>
              <Trans>Over 30 days overdue</Trans>
            </strong>
          )}
        </td>
        <td>
          {item.text === "" ? "—" : item.text}
          <span className="due-sub">
            {kind} · {where}
          </span>
        </td>
        {postponeTo === null ? (
          <>
            <td className="num">
              <input
                className={`due-amount${item.amount_cents < 0 ? " due-negative" : ""}`}
                inputMode="decimal"
                aria-label={t`Amount (€)`}
                aria-invalid={invalid === "amount"}
                value={draft.amount}
                onChange={(event) =>
                  setDraft({ ...draft, amount: event.target.value })
                }
              />
            </td>
            <td>
              <input
                type="date"
                aria-label={t`Booking date`}
                aria-invalid={invalid === "date"}
                value={draft.date}
                onChange={(event) =>
                  setDraft({ ...draft, date: event.target.value })
                }
              />
            </td>
            <td className="due-actions">
              <Button
                variant="primary"
                type="button"
                disabled={busy}
                onClick={confirm}
              >
                <Trans>Confirm</Trans>
              </Button>
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  setError(null);
                  setInvalid(null);
                  setPostponeTo(tomorrow());
                }}
              >
                <Trans>Postpone</Trans>
              </button>
              <button type="button" disabled={busy} onClick={skip}>
                <Trans>Skip</Trans>
              </button>
            </td>
          </>
        ) : (
          <>
            <td colSpan={2}>
              <label className="due-postpone">
                <Trans>Postpone to</Trans>
                <input
                  type="date"
                  aria-label={t`New date`}
                  value={postponeTo}
                  onChange={(event) => setPostponeTo(event.target.value)}
                />
              </label>
            </td>
            <td className="due-actions">
              <Button
                variant="primary"
                type="button"
                disabled={busy || postponeTo === ""}
                onClick={postpone}
              >
                <Trans>Postpone</Trans>
              </Button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setPostponeTo(null)}
              >
                <Trans>Cancel</Trans>
              </button>
            </td>
          </>
        )}
      </tr>
      {error && (
        <tr>
          <td colSpan={5}>
            <p role="alert" className="error due-error">
              {error}
            </p>
          </td>
        </tr>
      )}
    </>
  );
}
