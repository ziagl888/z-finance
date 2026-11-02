import { Trans, useLingui } from "@lingui/react/macro";
import { useMemo } from "react";
import { Link } from "react-router";

import { Chart } from "../../components/charts";
import { dayCategories } from "../forecast/chartData";
import { miniCurveLines } from "./chartData";
import type { CurveDay } from "./dashboardApi";

/**
 * The next 90 days of the total forecast (SPEC AK8) on the shared chart: balance and available
 * after levies. Horizon, views, what-if and the reserve check are the Forecast screen's
 * (UC-ZF-006); this is the same curve, shorter, drawn by the same component.
 */
export function MiniCurve({ days }: { days: CurveDay[] }) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const built = useMemo(
    () => ({
      categories: dayCategories(days, locale),
      lines: miniCurveLines(days, {
        balance: t`Balance`,
        available: t`Available after levies`,
      }),
    }),
    [days, locale, t],
  );

  return (
    <section className="dashboard-curve">
      <Chart
        title={t`The next 90 days`}
        description={
          <Trans>
            Your money day by day. Available after levies is the balance minus
            the open levy gap — income tax and SVS still to be paid.
          </Trans>
        }
        categories={built.categories}
        lines={built.lines}
        readHint={t`The next 90 days: use the left and right arrow keys to read a day (Shift for a week)`}
        yLabel={t`Amount (EUR)`}
        xLabel={t`Day`}
        plotHeight={200}
        empty={<Trans>No accounts yet, so there is no curve.</Trans>}
      />
      <p className="ui-actions">
        <Link to="/forecast">
          <Trans>Go to Forecast</Trans>
        </Link>
      </p>
    </section>
  );
}
