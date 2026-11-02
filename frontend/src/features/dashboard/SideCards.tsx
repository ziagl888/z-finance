import { Plural, Trans, useLingui } from "@lingui/react/macro";
import { Link } from "react-router";

import { Badge, Card, MoneyCell, Table } from "../../ui";
import { formatDay } from "../levy/draft";
import type { Balances, Proposal } from "./dashboardApi";

/** Balances of the payment accounts per area and in total (SPEC AK8); debts are their own figure. */
export function BalancesCard({ balances }: { balances: Balances }) {
  const { t } = useLingui();
  return (
    <Card as="section" aria-labelledby="dashboard-balances-title">
      <h2 id="dashboard-balances-title">
        <Trans>Account balances</Trans>
      </h2>
      <Table aria-label={t`Account balances by area`}>
        <tbody>
          <tr>
            <th scope="row">
              <Trans>Private</Trans>
            </th>
            <MoneyCell cents={balances.private_cents} />
          </tr>
          <tr>
            <th scope="row">
              <Trans>Business</Trans>
            </th>
            <MoneyCell cents={balances.business_cents} />
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <th scope="row">
              <Trans>Together</Trans>
            </th>
            <MoneyCell cents={balances.total_cents} />
          </tr>
        </tfoot>
      </Table>
      <p className="ui-actions">
        <Link to="/accounts">
          <Trans>Go to Accounts</Trans>
        </Link>
      </p>
    </Card>
  );
}

/**
 * Income waiting to be allocated (UC-ZF-014). A proposal open for seven days or more is
 * highlighted (4b) and nothing is ever booked without the click on the Allocation screen.
 */
export function AllocateCard({ proposals }: { proposals: Proposal[] }) {
  const { t, i18n } = useLingui();
  const waiting = proposals.filter((proposal) => proposal.overdue).length;
  return (
    <Card as="section" aria-labelledby="dashboard-allocate-title">
      <h2 id="dashboard-allocate-title">
        <Trans>Allocate</Trans>
      </h2>
      {proposals.length === 0 ? (
        <p className="ui-hint">
          <Trans>
            No income is waiting. Rules are set up under{" "}
            <Link to="/allocation">Allocation</Link>.
          </Trans>
        </p>
      ) : (
        <>
          {waiting > 0 && (
            <p role="alert" className="dashboard-alert">
              <span aria-hidden="true">⚠ </span>
              <Plural
                value={waiting}
                one="# proposal has been waiting for 7 days or more."
                other="# proposals have been waiting for 7 days or more."
              />
            </p>
          )}
          <Table aria-label={t`Income waiting to be allocated`}>
            <tbody>
              {proposals.map((proposal) => (
                <tr
                  key={proposal.id}
                  className={proposal.overdue ? "dashboard-waiting" : undefined}
                >
                  <td>{formatDay(proposal.date, i18n.locale)}</td>
                  <td>
                    {proposal.text === "" ? "—" : proposal.text}
                    {proposal.overdue && (
                      <Badge tone="warn" className="dashboard-badge">
                        <Plural
                          value={proposal.days_open}
                          one="# day"
                          other="# days"
                        />
                      </Badge>
                    )}
                  </td>
                  <MoneyCell cents={proposal.amount_cents} plain />
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
      <p className="ui-actions">
        <Link to="/allocation">
          <Trans>Go to Allocation</Trans>
        </Link>
      </p>
    </Card>
  );
}

/** Receipts the agent could not book (UC-ZF-013 step 7); they wait on the Receipts screen. */
export function ReceiptsCard({ count }: { count: number }) {
  return (
    <Card as="section" aria-labelledby="dashboard-receipts-title">
      <h2 id="dashboard-receipts-title">
        <Trans>Receipts to check</Trans>
      </h2>
      <p className="dashboard-count">{count}</p>
      <p className="ui-hint">
        {count === 0 ? (
          <Trans>Every receipt sent in so far is booked.</Trans>
        ) : (
          <Plural
            value={count}
            one="# receipt could not be booked by itself and is waiting for you."
            other="# receipts could not be booked by themselves and are waiting for you."
          />
        )}
      </p>
      <p className="ui-actions">
        <Link to="/receipts">
          <Trans>Go to Receipts</Trans>
        </Link>
      </p>
    </Card>
  );
}
