import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { Card, MoneyCell, Table } from "../../ui";
import { listAccounts, type Account } from "../accounts/accountsApi";
import { useDueLabel } from "./DueDatesField";
import { TaxForm } from "./TaxForm";
import { getTaxSettings, type TaxKind, type TaxSettings } from "./taxApi";
import "./tax.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Taxes & levies (UC-ZF-008): the form for the income-tax prepayment, the SVS contribution and their
 * due dates, the hint while nothing is entered (UC 1a), and the resulting plan — one row per due
 * date with the next planned item. The planned items themselves live on the recurring screen.
 */
export function TaxPage() {
  const { t, i18n } = useLingui();
  const dueLabel = useDueLabel();
  const [accounts, setAccounts] = useState<Account[] | null>(null);
  const [settings, setSettings] = useState<TaxSettings | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listAccounts(false), getTaxSettings()])
      .then(([active, loaded]) => {
        if (cancelled) return;
        // Payments come from real money accounts, never from a debt.
        setAccounts(active.filter((a) => a.type !== "liability"));
        setSettings(loaded);
      })
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  const kindLabels: Record<TaxKind, string> = {
    est: t`Income tax prepayment`,
    svs: t`SVS contribution`,
  };
  const date = (iso: string) =>
    new Intl.DateTimeFormat(i18n.locale).format(new Date(`${iso}T00:00:00`));

  return (
    <section className="tax">
      <h1>
        <Trans>Taxes &amp; levies</Trans>
      </h1>
      <p className="ui-hint">
        <Trans>
          Enter the income tax prepayment and the SVS contribution once. They
          appear as planned items on their due dates. Small business: there is
          no VAT in z-finance.
        </Trans>
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {accounts !== null && settings !== null && (
        <>
          {settings.plan_missing && (
            <p className="ui-hint" role="status">
              <Trans>
                Taxes &amp; levies are not set yet. Fill in an amount and the
                account below.
              </Trans>
            </p>
          )}
          {accounts.length === 0 ? (
            <p className="ui-hint">
              <Trans>
                Create a business account first:{" "}
                <Link to="/accounts">Accounts</Link>.
              </Trans>
            </p>
          ) : (
            <TaxForm
              accounts={accounts}
              settings={settings}
              onSaved={setSettings}
            />
          )}
          {settings.terms.length > 0 && (
            <Card>
              <h2>
                <Trans>Plan</Trans>
              </h2>
              <Table>
                <thead>
                  <tr>
                    <th>
                      <Trans>What</Trans>
                    </th>
                    <th>
                      <Trans>Due date</Trans>
                    </th>
                    <th className="num">
                      <Trans>Amount</Trans>
                    </th>
                    <th>
                      <Trans>Next planned item</Trans>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {settings.terms.map((term) => (
                    <tr key={term.recurring_id}>
                      <td>{kindLabels[term.kind]}</td>
                      <td>{dueLabel(term.due)}</td>
                      <MoneyCell cents={term.amount_cents} />
                      <td>
                        {term.next_due_date ? date(term.next_due_date) : "–"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <p className="ui-hint">
                <Trans>
                  A new amount applies from the next open due date; quarters you
                  have already confirmed keep their amount. Back-payments after
                  an assessment notice go in as expected items on the{" "}
                  <Link to="/recurring">Recurring items</Link> page.
                </Trans>
              </p>
            </Card>
          )}
        </>
      )}
    </section>
  );
}
