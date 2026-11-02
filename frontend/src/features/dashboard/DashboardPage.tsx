import { Trans } from "@lingui/react/macro";
import { useEffect, useState } from "react";

import { DueCard } from "./DueCard";
import { Hints, Kpis } from "./Kpis";
import { MiniCurve } from "./MiniCurve";
import { MonthCard } from "./MonthCard";
import { AllocateCard, BalancesCard, ReceiptsCard } from "./SideCards";
import { getDashboard, type Dashboard } from "./dashboardApi";
import "./dashboard.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * The start page (UC NF5, SPEC AK8): the three figures of the weekly routine above everything
 * else, then the cards for what can be acted on — due items, the running month, the next 90 days,
 * the income waiting to be allocated, the receipts to check and the account balances.
 *
 * One request (`GET /api/dashboard`) carries all of it, so every figure belongs to the same state;
 * none of them is computed here, and each card links to the screen that owns it.
 */
export function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDashboard()
      .then((loaded) => !cancelled && setData(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  if (error !== null) {
    return (
      <section className="dashboard">
        <h1>
          <Trans>Dashboard</Trans>
        </h1>
        <p role="alert" className="error">
          {error}
        </p>
      </section>
    );
  }

  if (data === null) {
    return (
      <section className="dashboard">
        <h1>
          <Trans>Dashboard</Trans>
        </h1>
        <p>
          <Trans>Loading…</Trans>
        </p>
      </section>
    );
  }

  return (
    <section className="dashboard">
      <h1>
        <Trans>Dashboard</Trans>
      </h1>
      <Kpis data={data} />
      <Hints data={data} />
      {/* Two columns, each packed from the top: what waits for a decision on the left, the
          figures that only inform on the right. */}
      <div className="dashboard-grid">
        <div className="dashboard-column">
          <DueCard items={data.due_items} />
          <AllocateCard proposals={data.proposals} />
          <ReceiptsCard count={data.receipts_review_count} />
        </div>
        <div className="dashboard-column">
          <MonthCard month={data.month} />
          <BalancesCard balances={data.balances} />
        </div>
      </div>
      <MiniCurve days={data.mini_curve} />
    </section>
  );
}
