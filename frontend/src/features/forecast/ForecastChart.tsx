import { Trans, useLingui } from "@lingui/react/macro";
import { useMemo } from "react";

import { formatCents } from "../../api/client";
import { Chart } from "../../components/charts";
import { dayCategories, forecastSeries } from "./chartData";
import type { Forecast } from "./forecastApi";

interface Props {
  forecast: Forecast;
  /** Index of the chosen day, `null` while none is chosen. */
  selected: number | null;
  onSelect: (index: number) => void;
  busy: boolean;
}

/**
 * The curve of UC-ZF-006 steps 2 and 3 on the shared chart: balance, "available after levies" and
 * the reserve as a dashed line, the lowest point ringed, the days under the reserve flagged. The
 * description states the core rule: the reserve check refers to the available line.
 */
export function ForecastChart({ forecast, selected, onSelect, busy }: Props) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const lowest = formatCents(forecast.minimum_cents, locale);

  const built = useMemo(
    () => ({
      categories: dayCategories(forecast.days, locale),
      ...forecastSeries(forecast, {
        balance: t`Balance`,
        available: t`Available after levies`,
        reserve: t`Reserve threshold`,
        below: t`Below the reserve`,
        lowest: t`Lowest: ${lowest}`,
      }),
    }),
    [forecast, locale, lowest, t],
  );

  return (
    <Chart
      title={t`Liquidity forecast`}
      description={
        <Trans>
          Your money day by day. Available after levies is the balance minus the
          open levy gap (income tax and SVS still to be paid). The dashed line
          is your reserve. The lowest point and the reserve check refer to
          Available after levies, not to the balance.
        </Trans>
      }
      categories={built.categories}
      lines={built.lines}
      flags={built.flags}
      marks={built.marks}
      selected={selected}
      onSelect={onSelect}
      readHint={t`Liquidity forecast: use the left and right arrow keys to choose a day (Shift for a week) and Enter to list its items`}
      yLabel={t`Amount (EUR)`}
      xLabel={t`Day`}
      plotHeight={320}
      busy={busy}
    />
  );
}
