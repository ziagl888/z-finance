import { Trans, useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import { Badge, Button, cx, selectableRow, Table } from "../../ui";
import type { Account, Category } from "../accounts/accountsApi";
import { formatDate, weekdayNames } from "./draft";
import type { Interval, Recurring } from "./recurringApi";

interface Props {
  items: Recurring[] | null;
  accounts: Account[];
  categories: Category[];
  selectedId: number | null;
  onSelect: (item: Recurring) => void;
  onEdit: (item: Recurring) => void;
  onToggleActive: (item: Recurring) => void;
  onDelete: (item: Recurring) => void;
}

/**
 * All recurring items with their rhythm, next due date and number of planned items. A click on a
 * row shows its items; Pause removes the planned items, Resume creates them again.
 */
export function RecurringList({
  items,
  accounts,
  categories,
  selectedId,
  onSelect,
  onEdit,
  onToggleActive,
  onDelete,
}: Props) {
  const { t, i18n } = useLingui();
  const accountName = (id: number | null) =>
    accounts.find((a) => a.id === id)?.name ?? "—";
  const categoryName = (id: number | null) =>
    id === null ? "" : (categories.find((c) => c.id === id)?.name ?? "");
  const weekdays = weekdayNames(i18n.locale);

  function rhythm(item: Recurring): string {
    const day = item.day_of_month ?? 0;
    const labels: Record<Interval, string> = {
      weekly: t`weekly`,
      monthly: t`monthly on day ${day}`,
      quarterly: t`quarterly on day ${day}`,
      yearly: t`yearly on day ${day}`,
    };
    const label = labels[item.interval];
    return item.interval === "weekly" && item.weekday !== null
      ? `${label}, ${weekdays[item.weekday]}`
      : label;
  }

  if (items === null) {
    return (
      <p>
        <Trans>Loading…</Trans>
      </p>
    );
  }
  if (items.length === 0) {
    return (
      <p className="ui-hint">
        <Trans>No recurring items yet. Create the first one above.</Trans>
      </p>
    );
  }

  return (
    <Table className="rec-table">
      <thead>
        <tr>
          <th>
            <Trans>Text</Trans>
          </th>
          <th>
            <Trans>Rhythm</Trans>
          </th>
          <th>
            <Trans>Account</Trans>
          </th>
          <th className="num">
            <Trans>Amount</Trans>
          </th>
          <th>
            <Trans>Next due</Trans>
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
          const other = accountName(item.transfer_target_account_id);
          const count = item.planned_count;
          const end = item.end_date
            ? formatDate(item.end_date, i18n.locale)
            : "";
          const expense =
            item.amount_cents < 0 && item.transfer_target_account_id === null;
          return (
            <tr
              key={item.id}
              className={cx(
                "ui-row-selectable",
                !item.active && "rec-row-done",
                item.id === selectedId && "rec-row-selected",
              )}
              {...selectableRow(() => onSelect(item))}
            >
              <td>
                {item.description}
                <span className="rec-sub">
                  {categoryName(item.category_id)}
                  {!item.active && (
                    <Badge>
                      <Trans>paused</Trans>
                    </Badge>
                  )}
                </span>
              </td>
              <td>
                {rhythm(item)}
                {end && (
                  <span className="rec-sub">
                    <Trans>until {end}</Trans>
                  </span>
                )}
              </td>
              <td>
                {accountName(item.account_id)}
                {item.transfer_target_account_id !== null && (
                  <span className="rec-sub">
                    <Trans>Transfer to {other}</Trans>
                  </span>
                )}
              </td>
              {/* Not MoneyCell: a transfer's negative amount is no expense
                  and stays uncoloured. */}
              <td className={cx("num", expense && "ui-money-negative")}>
                {formatCents(item.amount_cents, i18n.locale)}
              </td>
              <td>
                {item.next_due_date
                  ? formatDate(item.next_due_date, i18n.locale)
                  : "—"}
                <span className="rec-sub">
                  <Trans>{count} planned</Trans>
                </span>
              </td>
              <td className="rec-row-actions">
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onEdit(item);
                  }}
                >
                  <Trans>Edit</Trans>
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onToggleActive(item);
                  }}
                >
                  {item.active ? <Trans>Pause</Trans> : <Trans>Resume</Trans>}
                </button>
                <Button
                  variant="danger"
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(item);
                  }}
                >
                  <Trans>Delete</Trans>
                </Button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}
