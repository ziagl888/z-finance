import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { formatCents } from "../../api/client";
import { Badge, Card, KpiTile, MoneyCell, Table } from "../../ui";
import { PlanForm } from "./PlanForm";
import { WhatIf } from "./WhatIf";
import {
  formatMonth,
  getPayoff,
  putPayoff,
  type PayoffStatus,
  type PlanInput,
} from "./payoffApi";
import "./payoff.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Debt payoff plan (UC-ZF-012): what is available each month net of the levies, budget and
 * order, the debt-free month, the plan month by month, planned against booked, and "what if".
 */
export function PayoffPage() {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [status, setStatus] = useState<PayoffStatus | null>(null);
  const [version, setVersion] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getPayoff()
      .then((loaded) => !cancelled && setStatus(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSave(input: PlanInput) {
    setNotice(null);
    const saved = await putPayoff(input);
    setStatus(saved);
    // A new form instance picks up the stored values (e.g. the formatted budget).
    setVersion((v) => v + 1);
    setNotice(t`Plan saved.`);
  }

  if (status === null) {
    return (
      <section className="payoff">
        <h1>
          <Trans>Debt payoff plan</Trans>
        </h1>
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : (
          <p className="muted">
            <Trans>Loading…</Trans>
          </p>
        )}
      </section>
    );
  }

  const money = (cents: number) => formatCents(cents, locale);
  const month = (key: string | null) => (key ? formatMonth(key, locale) : "—");
  const names = new Map(status.debts.map((d) => [d.id, d.creditor]));
  const avail = status.availability;
  // A cost shown as a negative amount; 0 stays 0 (no "-€0.00").
  const cost = (cents: number) => (cents === 0 ? 0 : -cents);
  // Lingui placeholders must be plain identifiers.
  const plannedEnd = month(status.baseline_end_month);
  const shortfall = money(status.shortfall_cents);
  const available = money(avail.available_cents);
  const catchUp = money(status.catch_up_cents);
  const freeNow = money(status.free_now_cents);
  const levyRate = (avail.levy_rate_bp / 100).toLocaleString(locale);
  const unpayable = status.missing_pay_account
    .map((id) => names.get(id) ?? String(id))
    .join(", ");

  return (
    <section className="payoff">
      <h1>
        <Trans>Debt payoff plan</Trans>
      </h1>
      <p className="payoff-intro muted">
        <Trans>
          How much you can put towards your debts each month, in which order,
          and when you are debt-free. The extra money becomes a planned special
          payment each month.
        </Trans>
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <div className="ui-kpis">
        <KpiTile
          label={<Trans>Remaining debt</Trans>}
          value={money(status.remaining_total_cents)}
        />
        <KpiTile
          label={<Trans>Instalments per month</Trans>}
          value={money(avail.installments_cents)}
        />
        <KpiTile
          label={<Trans>Available per month</Trans>}
          value={money(avail.available_cents)}
          tone={avail.available_cents < 0 ? "alert" : "default"}
          sub={<Trans>after costs, instalments and levies</Trans>}
        />
        <KpiTile
          label={<Trans>Debt-free</Trans>}
          value={month(status.debt_free_month)}
          tone={status.behind ? "alert" : status.has_plan ? "ok" : "default"}
          sub={
            status.baseline_end_month &&
            status.shift_months !== null &&
            status.shift_months !== 0 ? (
              <Trans>planned: {plannedEnd}</Trans>
            ) : undefined
          }
        />
      </div>

      {status.debts.length === 0 && (
        <p className="ui-hint">
          <Trans>
            There are no open debts. Enter them first:{" "}
            <Link to="/debts">Debts</Link>.
          </Trans>
        </p>
      )}

      {!status.has_plan && (
        <Card className="payoff-shortfall" role="alert">
          <h2>
            <Trans>No plan possible</Trans>
          </h2>
          <p>
            <Trans>
              Fixed costs and instalments exceed your income by {shortfall} a
              month.
            </Trans>
          </p>
          <p>
            <Trans>
              Look for expenses you can cut in the{" "}
              <Link to="/month">Month</Link> view, or renegotiate instalments on
              the <Link to="/debts">Debts</Link> page.
            </Trans>
          </p>
        </Card>
      )}

      <div className="payoff-columns">
        <Card>
          <PlanForm key={version} status={status} onSave={onSave} />
          {notice && (
            <p role="status" className="ui-notice">
              {notice}
            </p>
          )}
          {status.over_budget && (
            <p className="payoff-warning" role="alert">
              <Badge tone="warn">
                <Trans>Warning</Trans>
              </Badge>{" "}
              <Trans>
                The budget is higher than what is available ({available}). The
                plan still works, but the forecast will show your reserve being
                used.
              </Trans>
            </p>
          )}
          {status.missing_pay_account.length > 0 && (
            <p className="ui-hint">
              <Trans>
                Some debts have no paying account, so no special payment can be
                planned for them: {unpayable}. Set one on the{" "}
                <Link to="/debts">Debts</Link> page.
              </Trans>
            </p>
          )}
        </Card>

        <Card>
          <h2>
            <Trans>Available per month</Trans>
          </h2>
          <Table zebra={false} className="payoff-terms">
            <tbody>
              <tr>
                <th scope="row">
                  {avail.income_overridden ? (
                    <Trans>Income (your amount)</Trans>
                  ) : (
                    <Trans>Average income of the last 3 months</Trans>
                  )}
                </th>
                <MoneyCell cents={avail.income_cents} />
              </tr>
              <tr>
                <th scope="row">
                  <Trans>Fixed costs</Trans>
                </th>
                <MoneyCell cents={cost(avail.fixed_costs_cents)} plain />
              </tr>
              <tr>
                <th scope="row">
                  <Trans>Instalments</Trans>
                </th>
                <MoneyCell cents={cost(avail.installments_cents)} plain />
              </tr>
              <tr>
                <th scope="row">
                  <Trans>Savings plans of pots</Trans>
                </th>
                <MoneyCell cents={cost(avail.pot_rates_cents)} plain />
              </tr>
              <tr>
                <th scope="row">
                  <Trans>Levies ({levyRate} %)</Trans>
                </th>
                <MoneyCell cents={cost(avail.levy_cents)} plain />
              </tr>
              <tr className="payoff-total">
                <th scope="row">
                  <Trans>Available</Trans>
                </th>
                <MoneyCell cents={avail.available_cents} />
              </tr>
            </tbody>
          </Table>
          <p className="ui-hint">
            <Trans>Free until the end of the month: {freeNow}</Trans>
          </p>
          {status.has_plan && (
            <WhatIf
              budgetCents={status.budget_cents}
              debtFreeMonth={status.debt_free_month}
            />
          )}
        </Card>
      </div>

      {status.progress.length > 0 && (
        <Card as="section" aria-labelledby="payoff-progress">
          <h2 id="payoff-progress">
            <Trans>Planned and paid</Trans>{" "}
            {status.behind ? (
              <Badge tone="overdue">
                <Trans>behind plan</Trans>
              </Badge>
            ) : (
              <Badge tone="ok">
                <Trans>on track</Trans>
              </Badge>
            )}
          </h2>
          {status.behind && (
            <p className="payoff-warning">
              <Trans>
                To stay debt-free by {plannedEnd}, pay {catchUp} extra next
                month.
              </Trans>
            </p>
          )}
          <Table>
            <thead>
              <tr>
                <th scope="col">
                  <Trans>Month</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Planned</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Paid</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Difference</Trans>
                </th>
              </tr>
            </thead>
            <tbody>
              {status.progress.map((row) => (
                <tr key={row.month}>
                  <td>
                    {month(row.month)}
                    {!row.completed && (
                      <>
                        {" "}
                        <Badge tone="planned">
                          <Trans>running</Trans>
                        </Badge>
                      </>
                    )}
                  </td>
                  <MoneyCell cents={row.planned_cents} />
                  <MoneyCell cents={row.actual_cents} />
                  <MoneyCell cents={row.difference_cents} />
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {status.debts.length > 0 && (
        <Card as="section" aria-labelledby="payoff-order">
          <h2 id="payoff-order">
            <Trans>Order</Trans>
          </h2>
          <Table>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">
                  <Trans>Creditor</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Remaining</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Instalment</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Interest</Trans>
                </th>
                <th scope="col">
                  <Trans>Paid off in</Trans>
                </th>
              </tr>
            </thead>
            <tbody>
              {status.debts.map((debt, index) => (
                <tr key={debt.id}>
                  <td className="num">{index + 1}</td>
                  <td>{debt.creditor}</td>
                  <MoneyCell cents={debt.remaining_cents} />
                  <td className="num">
                    {debt.installment_cents === null
                      ? "—"
                      : money(debt.installment_cents)}
                  </td>
                  <td className="num">
                    {debt.interest_rate_pct === null
                      ? "—"
                      : `${Number(debt.interest_rate_pct).toLocaleString(locale)} %`}
                  </td>
                  <td>{month(debt.end_month)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      {status.months.length > 0 && (
        <Card as="section" aria-labelledby="payoff-months">
          <h2 id="payoff-months">
            <Trans>Month by month</Trans>
          </h2>
          <Table>
            <thead>
              <tr>
                <th scope="col">
                  <Trans>Month</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Instalments</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Special payment</Trans>
                </th>
                <th scope="col">
                  <Trans>Goes to</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Remaining after</Trans>
                </th>
              </tr>
            </thead>
            <tbody>
              {status.months.map((row) => (
                <tr key={row.month}>
                  <td>{month(row.month)}</td>
                  <MoneyCell cents={row.installments_cents} />
                  <MoneyCell cents={row.extra_cents} />
                  <td>
                    {row.extra_to
                      .map(([id]) => names.get(id) ?? String(id))
                      .join(", ")}
                  </td>
                  <MoneyCell cents={row.remaining_cents} />
                </tr>
              ))}
            </tbody>
          </Table>
          <p className="ui-hint">
            <Trans>
              Special payments for the next 12 months appear as planned items in
              the <Link to="/due">due list</Link>; confirm them when you have
              paid.
            </Trans>
          </p>
        </Card>
      )}
    </section>
  );
}
