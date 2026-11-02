import { Trans, useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import { Button, cx, MoneyCell, selectableRow, Table } from "../../ui";
import { AREAS } from "../accounts/accountsApi";
import { AREA_LABELS } from "../accounts/labels";
import { formatDate } from "../recurring/draft";
import type { Debt, DebtInterval, Overview } from "./debtsApi";

interface Props {
  overview: Overview | null;
  selectedId: number | null;
  onSelect: (debt: Debt) => void;
  onEdit: (debt: Debt) => void;
  onDelete: (debt: Debt) => void;
}

/**
 * The debt overview (UC-ZF-010 AK4): per debt remaining amount, instalment and projected end,
 * then the remaining amount per area and in total. A click on a row (or Enter/Space on the focused
 * row) opens the debt's details.
 */
export function DebtList({
  overview,
  selectedId,
  onSelect,
  onEdit,
  onDelete,
}: Props) {
  const { t, i18n } = useLingui();
  const money = (cents: number) => formatCents(cents, i18n.locale);
  const intervalLabels: Record<DebtInterval, string> = {
    monthly: t`monthly`,
    quarterly: t`quarterly`,
    yearly: t`yearly`,
  };

  function end(debt: Debt): string {
    if (debt.closed) return t`paid off`;
    if (debt.plan) {
      const count = debt.plan.count;
      return `${formatDate(debt.plan.end, i18n.locale)} · ${t`${count} instalments`}`;
    }
    if (debt.required_monthly_cents !== null) {
      const rate = money(debt.required_monthly_cents);
      return t`needs ${rate} a month`;
    }
    return "—";
  }

  if (overview === null) {
    return (
      <p>
        <Trans>Loading…</Trans>
      </p>
    );
  }
  if (overview.debts.length === 0) {
    return (
      <p className="ui-hint">
        <Trans>No debts recorded. Enter the first one above.</Trans>
      </p>
    );
  }

  return (
    <Table className="debt-table">
      <thead>
        <tr>
          <th>
            <Trans>Creditor</Trans>
          </th>
          <th>
            <Trans>Area</Trans>
          </th>
          <th className="num">
            <Trans>Remaining</Trans>
          </th>
          <th className="num">
            <Trans>Instalment</Trans>
          </th>
          <th>
            <Trans>Paid off on</Trans>
          </th>
          <th>
            <span className="sr-only">
              <Trans>Actions</Trans>
            </span>
          </th>
        </tr>
      </thead>
      <tbody>
        {overview.debts.map((debt) => (
          <tr
            key={debt.id}
            className={cx(
              "debt-row",
              "ui-row-selectable",
              debt.id === selectedId && "debt-row-selected",
              debt.closed && "debt-row-done",
            )}
            {...selectableRow(() => onSelect(debt))}
          >
            <td>
              {/* Keyboard access; the click bubbles to the row. */}
              <button type="button" className="debt-link">
                {debt.creditor}
              </button>
            </td>
            <td>{i18n._(AREA_LABELS[debt.area])}</td>
            <MoneyCell cents={debt.remaining_cents} />
            <td className="num">
              {debt.installment_cents === null || debt.interval === null
                ? "—"
                : `${money(debt.installment_cents)} ${intervalLabels[debt.interval]}`}
            </td>
            <td>{end(debt)}</td>
            <td className="debt-row-actions">
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onEdit(debt);
                }}
              >
                <Trans>Edit</Trans>
              </button>
              <Button
                variant="danger"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete(debt);
                }}
              >
                <Trans>Delete</Trans>
              </Button>
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        {AREAS.map((area) => {
          const areaName = i18n._(AREA_LABELS[area]);
          return (
            <tr key={area}>
              <th colSpan={2}>
                <Trans>Sum {areaName}</Trans>
              </th>
              <MoneyCell cents={overview.remaining_by_area[area] ?? 0} />
              <td colSpan={3} />
            </tr>
          );
        })}
        <tr className="debt-total">
          <th colSpan={2}>
            <Trans>Total</Trans>
          </th>
          <MoneyCell cents={overview.remaining_total_cents} />
          <td colSpan={3} />
        </tr>
      </tfoot>
    </Table>
  );
}
