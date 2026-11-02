import { Trans, useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import { SplitBar, seriesColor } from "../../components/charts";
import { Badge, Card, MoneyCell, Table } from "../../ui";
import { formatRatePercent, sharePercent } from "./draft";
import type { RequiredRevenue } from "./levyApi";

interface Block {
  key: string;
  label: string;
  cents: number;
  /** The levy block is derived from the others; it is never named the driver (UC 4a). */
  derived?: boolean;
}

/**
 * Revenue needed per month (UC step 4) with its split by block (UC 4a): the bar and the table
 * add up to the required revenue, and the largest cost block is named as its driver.
 */
export function RequiredCard({ required }: { required: RequiredRevenue }) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const rate = formatRatePercent(required.levy_rate_bp, locale);
  const blocks: Block[] = [
    { key: "fixed", label: t`Fixed costs`, cents: required.fixed_costs_cents },
    {
      key: "installments",
      label: t`Instalments`,
      cents: required.installments_cents,
    },
    {
      key: "payoff",
      label: t`Payoff budget`,
      cents: required.payoff_budget_cents,
    },
    {
      key: "pots",
      label: t`Savings pot rates`,
      cents: required.pot_rates_cents,
    },
    {
      key: "reserve",
      label: t`Reserve build-up`,
      cents: required.reserve_build_cents,
    },
    {
      key: "levy",
      label: t`Levies on the revenue`,
      cents: required.levy_cents,
      derived: true,
    },
  ];
  const driver = blocks
    .filter((block) => !block.derived && block.cents > 0)
    .reduce<Block | null>(
      (best, block) =>
        best === null || block.cents > best.cents ? block : best,
      null,
    );

  return (
    <Card as="section" aria-labelledby="levy-required-title">
      <h2 id="levy-required-title">
        <Trans>Required revenue per month</Trans>
      </h2>
      <p className="levy-required-value">
        {formatCents(required.required_cents, locale)}
      </p>
      <p className="ui-hint">
        <Trans>
          Costs (fixed costs, instalments, payoff budget, pot rates, reserve
          build-up) divided by 1 minus your levy rate of {rate} %.
        </Trans>
      </p>
      {required.required_cents > 0 && (
        <>
          <SplitBar
            segments={blocks.map((block, i) => ({
              key: block.key,
              label: block.label,
              cents: block.cents,
              color: seriesColor(i),
            }))}
          />
          <Table aria-label={t`Required revenue by block`}>
            <thead>
              <tr>
                <th scope="col">
                  <Trans>Block</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Per month</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Share</Trans>
                </th>
              </tr>
            </thead>
            <tbody>
              {blocks.map((block, i) => (
                <tr key={block.key}>
                  <th scope="row">
                    <span
                      className="chart-key"
                      style={{ background: seriesColor(i) }}
                      aria-hidden="true"
                    />
                    {block.label}
                    {block === driver && (
                      <Badge tone="warn">
                        <Trans>Biggest cost block</Trans>
                      </Badge>
                    )}
                  </th>
                  <MoneyCell cents={block.cents} />
                  <td className="num">
                    {sharePercent(block.cents, required.required_cents)} %
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="levy-total">
                <th scope="row">
                  <Trans>Required revenue</Trans>
                </th>
                <MoneyCell cents={required.required_cents} />
                <td className="num">100 %</td>
              </tr>
            </tfoot>
          </Table>
        </>
      )}
    </Card>
  );
}
