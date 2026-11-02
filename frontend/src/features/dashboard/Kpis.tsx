import { Plural, Trans, useLingui } from "@lingui/react/macro";
import { Link } from "react-router";

import { formatCents } from "../../api/client";
import { KpiTile } from "../../ui";
import { formatDay } from "../levy/draft";
import type { Dashboard } from "./dashboardApi";

/**
 * The three figures of the weekly routine (UC NF5), above everything else and without scrolling:
 * what is free until the end of the month — net of the levy share, which is named beside it —
 * the remaining debt with its trend against the previous month, and the overdue items.
 */
export function Kpis({ data }: { data: Dashboard }) {
  const { i18n } = useLingui();
  const locale = i18n.locale;
  const until = formatDay(data.month_end, locale);
  const reserved = formatCents(data.free.levy_gap_cents, locale);
  const trend = formatCents(Math.abs(data.debt.trend_cents), locale);

  return (
    <div className="ui-kpis dashboard-kpis">
      <KpiTile
        label={<Trans>Free until {until}</Trans>}
        value={formatCents(data.free.free_cents, locale)}
        tone={data.free.free_cents < 0 ? "alert" : "default"}
        sub={
          <Link to="/levy">
            <Trans>of which reserved for levies: {reserved}</Trans>
          </Link>
        }
      />
      <KpiTile
        label={<Trans>Remaining debt</Trans>}
        value={formatCents(data.debt.total_cents, locale)}
        tone={debtTone(data.debt.trend_cents)}
        sub={
          <Link to="/debts">
            {data.debt.trend_cents === 0 ? (
              <Trans>unchanged since the end of last month</Trans>
            ) : data.debt.trend_cents < 0 ? (
              <Trans>{trend} less than at the end of last month</Trans>
            ) : (
              <Trans>{trend} more than at the end of last month</Trans>
            )}
          </Link>
        }
      />
      <KpiTile
        label={<Trans>Overdue items</Trans>}
        value={data.overdue_count}
        tone={data.overdue_count > 0 ? "alert" : "ok"}
        sub={
          <Link to="/due">
            <Plural
              value={data.due_items.length}
              one="# item due in the next 14 days"
              other="# items due in the next 14 days"
            />
          </Link>
        }
      />
    </div>
  );
}

/** A shrinking debt is good news, a growing one needs attention; an unchanged one is neither. */
function debtTone(trendCents: number): "ok" | "alert" | "default" {
  if (trendCents < 0) return "ok";
  return trendCents > 0 ? "alert" : "default";
}

/**
 * The two hints of the dashboard: the levy gap exceeds the free balance (UC-ZF-015 2b) and no tax
 * plan is stored (UC-ZF-008 1a). Each links to the screen that settles it.
 */
export function Hints({ data }: { data: Dashboard }) {
  if (!data.hints.levies_not_covered && !data.hints.tax_plan_missing)
    return null;
  return (
    <div className="dashboard-hints">
      {data.hints.levies_not_covered && (
        <p role="alert" className="dashboard-alert">
          <span aria-hidden="true">⚠ </span>
          <Trans>
            Levies not covered: the open levy gap is larger than the money on
            your accounts. <Link to="/levy">Levy &amp; revenue</Link> shows what
            is missing.
          </Trans>
        </p>
      )}
      {data.hints.tax_plan_missing && (
        <p role="status" className="dashboard-note">
          <Trans>
            Taxes and levies are not set, so no prepayment is planned. Enter
            them under <Link to="/tax">Taxes &amp; levies</Link>.
          </Trans>
        </p>
      )}
    </div>
  );
}
