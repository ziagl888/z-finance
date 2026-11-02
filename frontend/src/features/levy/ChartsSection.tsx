import { Trans, useLingui } from "@lingui/react/macro";
import { useMemo } from "react";

import { Chart } from "../../components/charts";
import {
  currentMonthKey,
  debtSeries,
  expenseSeries,
  firstPlannedIndex,
  incomeSeries,
  monthCategories,
} from "./chartData";
import type { SlotMap } from "./draft";
import type { ChartArea, ExpenseMonth, IncomeMonth } from "./levyApi";

interface Props {
  income: IncomeMonth[];
  expenses: ExpenseMonth[];
  area: ChartArea;
  onArea: (area: ChartArea) => void;
  slots: SlotMap;
  /** A reload is running: the charts stay, dimmed. */
  busy: boolean;
}

/**
 * The two charts of UC-ZF-015 (steps 4 and 5) under one filter row: income against the required
 * revenue over 24 months (months under the need marked), and expenses by category against the
 * liabilities line, with the remaining debt in a panel of its own (one axis per chart).
 */
export function ChartsSection({
  income,
  expenses,
  area,
  onArea,
  slots,
  busy,
}: Props) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const current = currentMonthKey();

  const incomeChart = useMemo(() => {
    const months = income.map((m) => m.month);
    return {
      categories: monthCategories(months, locale, t`Planned`, current),
      divider: firstPlannedIndex(months, current),
      ...incomeSeries(income, {
        booked: t`Booked income`,
        planned: t`Planned income`,
        required: t`Required revenue`,
        below: t`Below required revenue`,
      }),
    };
  }, [income, locale, current, t]);

  const expenseChart = useMemo(() => {
    const months = expenses.map((m) => m.month);
    return {
      categories: monthCategories(months, locale, t`Planned`, current),
      divider: firstPlannedIndex(months, current),
      ...expenseSeries(expenses, slots, {
        liabilities: t`Liabilities (instalments and special payments)`,
        other: t`Other categories`,
        uncategorised: t`Uncategorised`,
      }),
      debt: debtSeries(expenses, t`Remaining debt`),
    };
  }, [expenses, slots, locale, current, t]);

  const hasDebt = expenses.some((month) => month.remaining_debt_cents > 0);
  const areas: { value: ChartArea; label: string }[] = [
    { value: "all", label: t`All` },
    { value: "private", label: t`Private` },
    { value: "business", label: t`Business` },
  ];
  const planned = (index: number | null) =>
    index === null ? undefined : { index, label: t`Planned →` };
  const month = t`Month`;

  return (
    <section className="levy-charts" aria-labelledby="levy-charts-title">
      <h2 id="levy-charts-title">
        <Trans>Charts</Trans>
      </h2>
      <div className="levy-filter">
        <fieldset className="ui-segmented">
          <legend className="sr-only">
            <Trans>Area of the expense chart</Trans>
          </legend>
          {areas.map((option) => (
            <label key={option.value}>
              <input
                type="radio"
                name="levy-area"
                value={option.value}
                checked={area === option.value}
                onChange={() => onArea(option.value)}
              />
              {option.label}
            </label>
          ))}
        </fieldset>
        <span className="ui-hint">
          <Trans>
            Applies to the expense chart. The income chart always shows the
            business.
          </Trans>
        </span>
      </div>

      <Chart
        title={t`Income against required revenue`}
        description={
          <Trans>
            Business income per month: booked (solid) and planned (light). The
            line is the revenue you need each month; months below it are marked.
          </Trans>
        }
        categories={incomeChart.categories}
        bars={incomeChart.bars}
        lines={incomeChart.lines}
        flags={incomeChart.flags}
        divider={planned(incomeChart.divider)}
        yLabel={t`Amount (EUR)`}
        xLabel={month}
        busy={busy}
      />

      <Chart
        title={t`Expenses and liabilities`}
        description={
          <Trans>
            Expenses per month by category, booked up to this month and planned
            after it. The line is what you pay towards your debts each month.
          </Trans>
        }
        categories={expenseChart.categories}
        bars={expenseChart.bars}
        lines={expenseChart.lines}
        divider={planned(expenseChart.divider)}
        yLabel={t`Amount per month (EUR)`}
        xLabel={month}
        busy={busy}
      />

      {hasDebt ? (
        <Chart
          title={t`Remaining debt`}
          description={
            <Trans>
              What you still owe at the end of each month, with the planned
              instalments taken into account. It has its own axis because it is
              a stock, not a monthly amount.
            </Trans>
          }
          categories={expenseChart.categories}
          lines={expenseChart.debt}
          divider={planned(expenseChart.divider)}
          yLabel={t`Remaining debt (EUR)`}
          xLabel={month}
          plotHeight={180}
          busy={busy}
        />
      ) : (
        <p className="ui-hint">
          <Trans>No open debts in this area, so there is no debt curve.</Trans>
        </p>
      )}
    </section>
  );
}
