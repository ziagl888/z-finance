import { Trans, useLingui } from "@lingui/react/macro";

import type { Account, Category } from "../accounts/accountsApi";
import { Badge, MoneyCell, Table } from "../../ui";
import type { Transaction } from "./entryApi";

interface Props {
  transactions: Transaction[] | null;
  accounts: Account[];
  categories: Category[];
  editingId: number | null;
  onEdit: (item: Transaction) => void;
  onDelete: (item: Transaction) => void;
}

/**
 * The latest bookings below the form (UC-ZF-002 step 6, 6b): a click on a row (or its Edit
 * button) opens it in the form; Delete asks first. A transfer shows as its two legs. A row whose
 * `source` is `"recurring"` (booked from a due item, UC-ZF-004 AK1) carries a "recurring" badge.
 */
export function TransactionList({
  transactions,
  accounts,
  categories,
  editingId,
  onEdit,
  onDelete,
}: Props) {
  const { t, i18n } = useLingui();
  const accountName = (id: number | null) =>
    accounts.find((a) => a.id === id)?.name ?? "—";
  const categoryName = (id: number | null) =>
    id === null ? "" : (categories.find((c) => c.id === id)?.name ?? "");

  if (transactions === null) {
    return (
      <p>
        <Trans>Loading…</Trans>
      </p>
    );
  }
  if (transactions.length === 0) {
    return (
      <p className="ui-hint">
        <Trans>
          No bookings yet. Your first one appears here after saving.
        </Trans>
      </p>
    );
  }

  return (
    <Table className="entry-table">
      <thead>
        <tr>
          <th>
            <Trans>Date</Trans>
          </th>
          <th>
            <Trans>Text</Trans>
          </th>
          <th>
            <Trans>Category</Trans>
          </th>
          <th>
            <Trans>Account</Trans>
          </th>
          <th className="num">
            <Trans>Amount</Trans>
          </th>
          <th>
            <span className="sr-only">
              <Trans>Actions</Trans>
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        {transactions.map((item) => {
          const other = accountName(item.transfer_account_id);
          const date = new Date(`${item.date}T00:00:00`).toLocaleDateString(
            i18n.locale,
          );
          return (
            <tr
              key={item.id}
              className={[
                "ui-row-selectable",
                item.status === "planned" ? "entry-row-planned" : "",
                item.id === editingId ? "entry-row-editing" : "",
              ].join(" ")}
              onClick={() => onEdit(item)}
            >
              <td>{date}</td>
              <td>
                {item.text || "—"}
                {item.counterparty && (
                  <span className="entry-sub">{item.counterparty}</span>
                )}
              </td>
              <td>{categoryName(item.category_id)}</td>
              <td>
                {accountName(item.account_id)}
                {item.transfer_group_id !== null && (
                  <span className="entry-sub">
                    {item.amount_cents < 0 ? (
                      <Trans>Transfer to {other}</Trans>
                    ) : (
                      <Trans>Transfer from {other}</Trans>
                    )}
                  </span>
                )}
              </td>
              <MoneyCell cents={item.amount_cents}>
                {item.source === "recurring" && (
                  <Badge tone="neutral">{t`recurring`}</Badge>
                )}
                {item.status !== "booked" && (
                  <Badge
                    tone={item.status === "planned" ? "planned" : "skipped"}
                  >
                    {item.status === "planned" ? t`planned` : t`skipped`}
                  </Badge>
                )}
              </MoneyCell>
              <td className="entry-row-actions">
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
                    onDelete(item);
                  }}
                >
                  <Trans>Delete</Trans>
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}
