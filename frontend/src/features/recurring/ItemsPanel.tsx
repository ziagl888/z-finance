import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";

import { formatCents } from "../../api/client";
import { Badge, Card, cx, Table } from "../../ui";
import { centsToInput } from "../accounts/accountsApi";
import { formatDate, magnitudeCents } from "./draft";
import {
  listItems,
  moveItem,
  type PlannedItem,
  type Recurring,
} from "./recurringApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Props {
  recurring: Recurring;
  /** Called after an item was moved (reload the recurring list: next due date). */
  onChanged: () => Promise<void>;
}

interface Editing {
  id: number;
  date: string;
  amount: string;
}

/**
 * The items of one recurring item (UC-ZF-003 step 2): planned ones and those already booked. A
 * planned item can be moved to another date or amount; the next generation run leaves it there
 * (UC 2b). Booking and skipping due items happen under due items (UC-ZF-004).
 */
export function ItemsPanel({ recurring, onChanged }: Props) {
  const { t, i18n } = useLingui();
  const [items, setItems] = useState<PlannedItem[] | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  // Reload whenever another item is shown, the item itself changed, or a move was saved.
  useEffect(() => {
    let cancelled = false;
    listItems(recurring.id)
      .then((loaded) => !cancelled && setItems(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, [recurring, version]);

  async function save() {
    if (!editing) return;
    const cents = magnitudeCents(editing.amount);
    if (cents === null || editing.date === "") {
      setError(t`Enter a date and an amount other than 0.`);
      return;
    }
    setError(null);
    try {
      await moveItem(editing.id, editing.date, cents);
      setEditing(null);
      setVersion((v) => v + 1);
      await onChanged();
    } catch (reason) {
      setError(message(reason));
    }
  }

  const name = recurring.description;
  const statusLabel = (item: PlannedItem) =>
    item.status === "planned"
      ? t`planned`
      : item.status === "booked"
        ? t`booked`
        : t`skipped`;

  return (
    <Card as="section" className="rec-items" aria-labelledby="rec-items-title">
      <h2 id="rec-items-title">
        <Trans>Items of “{name}”</Trans>
      </h2>
      {!recurring.active && (
        <p className="ui-hint">
          <Trans>
            This recurring item is paused: it has no planned items until you
            resume it.
          </Trans>
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {items === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : items.length === 0 ? (
        <p className="ui-hint">
          <Trans>No items.</Trans>
        </p>
      ) : (
        <Table className="rec-table">
          <thead>
            <tr>
              <th>
                <Trans>Date</Trans>
              </th>
              <th className="num">
                <Trans>Amount</Trans>
              </th>
              <th>
                <Trans>Status</Trans>
              </th>
              <th>
                <span className="sr-only">
                  <Trans>Actions</Trans>
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const due = formatDate(item.due_date, i18n.locale);
              const open = editing?.id === item.id;
              return (
                <tr
                  key={item.id}
                  className={item.status === "planned" ? "" : "rec-row-done"}
                >
                  <td>
                    {open ? (
                      <input
                        type="date"
                        aria-label={t`New date`}
                        value={editing.date}
                        onChange={(event) =>
                          setEditing({ ...editing, date: event.target.value })
                        }
                      />
                    ) : (
                      formatDate(item.date, i18n.locale)
                    )}
                    {item.date !== item.due_date && (
                      <span className="rec-sub">
                        <Trans>due {due}</Trans>
                      </span>
                    )}
                  </td>
                  <td
                    className={cx(
                      "num",
                      item.amount_cents < 0 && "ui-money-negative",
                    )}
                  >
                    {open ? (
                      <input
                        className="num"
                        inputMode="decimal"
                        aria-label={t`New amount (€)`}
                        value={editing.amount}
                        onChange={(event) =>
                          setEditing({ ...editing, amount: event.target.value })
                        }
                      />
                    ) : (
                      formatCents(item.amount_cents, i18n.locale)
                    )}
                  </td>
                  <td>
                    {statusLabel(item)}
                    {item.moved && item.status === "planned" && (
                      <Badge>
                        <Trans>changed</Trans>
                      </Badge>
                    )}
                  </td>
                  <td className="rec-row-actions">
                    {open ? (
                      <>
                        <button type="button" onClick={() => void save()}>
                          <Trans>Save</Trans>
                        </button>
                        <button type="button" onClick={() => setEditing(null)}>
                          <Trans>Cancel</Trans>
                        </button>
                      </>
                    ) : (
                      item.status === "planned" && (
                        <button
                          type="button"
                          onClick={() => {
                            setError(null);
                            setEditing({
                              id: item.id,
                              date: item.date,
                              amount: centsToInput(
                                Math.abs(item.amount_cents),
                                i18n.locale,
                              ),
                            });
                          }}
                        >
                          <Trans>Move</Trans>
                        </button>
                      )
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}
    </Card>
  );
}
