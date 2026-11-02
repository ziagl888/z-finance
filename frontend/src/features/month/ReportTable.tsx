import { Trans, useLingui } from "@lingui/react/macro";

import { MoneyCell, Table } from "../../ui";
import type { Comparison, Flow, Line } from "./monthApi";

interface Props {
  flow: Flow;
  lines: Line[];
  total: Comparison;
  selected: Line | null;
  onSelect: (line: Line) => void;
}

/**
 * Income or expenses of the month by category (UC-ZF-007 step 2): the month's amount with the
 * previous month and the average of the six months before. A category is a button; it opens the
 * bookings behind it (step 6). Lines without a booking this month are muted.
 */
export function ReportTable({ flow, lines, total, selected, onSelect }: Props) {
  const { t } = useLingui();
  const heading =
    flow === "income" ? t`Income by category` : t`Expenses by category`;
  const name = (line: Line) => {
    if (line.category_name === null) return t`Uncategorised`;
    return line.parent_name
      ? `${line.parent_name} › ${line.category_name}`
      : line.category_name;
  };
  const isSelected = (line: Line) =>
    selected !== null &&
    selected.flow === line.flow &&
    selected.category_id === line.category_id;

  return (
    <Table>
      <caption>{heading}</caption>
      <thead>
        <tr>
          <th>
            <Trans>Category</Trans>
          </th>
          <th className="num">
            <Trans>This month</Trans>
          </th>
          <th className="num">
            <Trans>Previous month</Trans>
          </th>
          <th className="num">
            <Trans>Average of last 6 months</Trans>
          </th>
        </tr>
      </thead>
      <tbody>
        {lines.length === 0 && (
          <tr>
            <td colSpan={4} className="ui-hint">
              <Trans>Nothing booked.</Trans>
            </td>
          </tr>
        )}
        {lines.map((line) => (
          <tr
            key={`${line.flow}-${line.category_id ?? "none"}`}
            className={[
              line.comparison.amount_cents === 0 ? "month-muted" : "",
              isSelected(line) ? "month-selected" : "",
            ].join(" ")}
          >
            <td>
              <button
                type="button"
                className="month-link"
                aria-pressed={isSelected(line)}
                disabled={line.booking_count === 0}
                onClick={() => onSelect(line)}
              >
                {name(line)}
              </button>
            </td>
            <MoneyCell plain cents={line.comparison.amount_cents} />
            <MoneyCell plain cents={line.comparison.previous_cents} />
            <MoneyCell plain cents={line.comparison.average_6m_cents} />
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <th scope="row">
            <Trans>Total</Trans>
          </th>
          <MoneyCell plain cents={total.amount_cents} />
          <MoneyCell plain cents={total.previous_cents} />
          <MoneyCell plain cents={total.average_6m_cents} />
        </tr>
      </tfoot>
    </Table>
  );
}
