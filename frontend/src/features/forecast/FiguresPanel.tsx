import { Plural, Trans, useLingui } from "@lingui/react/macro";
import { Link } from "react-router";

import { formatCents } from "../../api/client";
import { KpiTile } from "../../ui";
import { formatDay } from "../levy/draft";
import type { Forecast } from "./forecastApi";

/**
 * UC-ZF-006 step 3 beside the curve: the lowest point with its date, the first day under the
 * reserve, the planned income and expenses of the horizon and the overdue items (extension 2b,
 * with the link to "Due items"). Every figure refers to the line "available after levies".
 */
export function FiguresPanel({ forecast }: { forecast: Forecast }) {
  const { i18n } = useLingui();
  const locale = i18n.locale;
  const money = (cents: number) => formatCents(cents, locale);
  const lowestDate = formatDay(forecast.minimum_date, locale);
  const breach = forecast.first_breach_date;
  const overdue = forecast.overdue_count;
  const reserve = money(forecast.reserve_cents);
  const gap = money(forecast.levy_gap_cents);
  return (
    <section aria-labelledby="forecast-figures-title">
      <h2 id="forecast-figures-title" className="sr-only">
        <Trans>Key figures</Trans>
      </h2>
      <div className="ui-kpis">
        <KpiTile
          label={<Trans>Lowest point</Trans>}
          value={money(forecast.minimum_cents)}
          sub={lowestDate}
          tone={
            breach !== null
              ? "alert"
              : forecast.reserve_missing
                ? "default"
                : "ok"
          }
        />
        <KpiTile
          label={<Trans>First day under the reserve</Trans>}
          value={
            breach !== null ? (
              formatDay(breach, locale)
            ) : forecast.reserve_missing ? (
              "—"
            ) : (
              <Trans>None</Trans>
            )
          }
          sub={
            forecast.reserve_missing ? (
              <Trans>No reserve defined</Trans>
            ) : (
              <Trans>Reserve {reserve}</Trans>
            )
          }
          tone={breach !== null ? "alert" : "default"}
        />
        <KpiTile
          label={<Trans>Planned income</Trans>}
          value={money(forecast.planned_income_cents)}
        />
        <KpiTile
          label={<Trans>Planned expenses</Trans>}
          value={money(forecast.planned_expense_cents)}
        />
      </div>
      {forecast.levy_applies && (
        <p className="ui-hint forecast-note">
          <Trans>Open levy gap today: {gap}</Trans>
        </p>
      )}
      <p className="forecast-note" role="status">
        {overdue > 0 ? (
          <>
            <Plural
              value={overdue}
              one="# overdue item is counted from today."
              other="# overdue items are counted from today."
            />{" "}
            <Link to="/due">
              <Trans>Open due items</Trans>
            </Link>
          </>
        ) : (
          <span className="muted">
            <Trans>No overdue items.</Trans>
          </span>
        )}
      </p>
    </section>
  );
}
