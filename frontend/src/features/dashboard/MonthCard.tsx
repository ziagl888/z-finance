import { Trans, useLingui } from "@lingui/react/macro";
import { Link } from "react-router";

import { Card, MoneyCell, Table } from "../../ui";
import { monthLong } from "../../components/charts";
import type { MonthOverview } from "./dashboardApi";

/**
 * The running month over both areas (SPEC AK8): income, expenses and balance with the largest
 * category lines. The full report, the drilldown and the uncategorised bookings are the Month
 * screen's (UC-ZF-007), which this card links to.
 */
export function MonthCard({ month }: { month: MonthOverview }) {
  const { t, i18n } = useLingui();
  const name = monthLong(month.month, i18n.locale);
  return (
    <Card as="section" aria-labelledby="dashboard-month-title">
      <h2 id="dashboard-month-title">
        <Trans>{name} so far</Trans>
      </h2>
      <Table aria-label={t`Income and expenses of the running month`}>
        <tbody>
          <tr>
            <th scope="row">
              <Trans>Income</Trans>
            </th>
            <MoneyCell cents={month.income_cents} plain />
          </tr>
          <tr>
            <th scope="row">
              <Trans>Expenses</Trans>
            </th>
            <MoneyCell cents={month.expense_cents} plain />
          </tr>
          {month.top_lines.map((line) => (
            <tr key={`${line.flow}-${line.category_id ?? "none"}`}>
              <td className="dashboard-month-category">
                {line.category_name ?? t`Uncategorised`}
              </td>
              <MoneyCell cents={line.amount_cents} plain />
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">
              <Trans>Balance</Trans>
            </th>
            <MoneyCell cents={month.balance_cents} />
          </tr>
        </tfoot>
      </Table>
      <p className="ui-actions">
        <Link to="/month">
          <Trans>Go to Month</Trans>
        </Link>
      </p>
    </Card>
  );
}
