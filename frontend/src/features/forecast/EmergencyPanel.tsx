import { Trans, useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import { Button, Card, MoneyCell, Table } from "../../ui";
import type { Account } from "../accounts/accountsApi";
import { formatDay } from "../levy/draft";
import type { Emergency, EmergencyProposal } from "./forecastApi";

interface Props {
  emergency: Emergency;
  accounts: Account[];
  /** What-if items are on the curve; the proposal only knows the saved plan. */
  whatIfActive: boolean;
  busy: boolean;
  onTake: (proposal: EmergencyProposal) => void;
}

/**
 * UC-ZF-006 extension 3b: the reserve is breached — which pots, in emergency order, would cover the
 * shortfall (locked pots never), with the withdrawal one click away. Only shown with a breach.
 */
export function EmergencyPanel({
  emergency,
  accounts,
  whatIfActive,
  busy,
  onTake,
}: Props) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const shortfall = formatCents(emergency.shortfall_cents, locale);
  const uncovered = formatCents(emergency.uncovered_cents, locale);
  const nameOf = (id: number) =>
    accounts.find((account) => account.id === id)?.name ?? `#${id}`;
  return (
    <Card as="section" aria-labelledby="forecast-emergency-title">
      <h2 id="forecast-emergency-title">
        <Trans>Cover the shortfall from your pots</Trans>
      </h2>
      <p>
        <Trans>
          The line falls short of your reserve by {shortfall}. These pots would
          cover it, in your emergency order. Locked pots are never used.
        </Trans>
      </p>
      {whatIfActive && (
        <p className="ui-hint">
          <Trans>
            This suggestion looks at your saved plan only, not at the what-if
            items.
          </Trans>
        </p>
      )}
      {emergency.proposals.length === 0 && (
        <p role="status" className="forecast-alert">
          <Trans>
            No pot can cover it: there are no pots on these accounts, or all are
            locked or empty.
          </Trans>
        </p>
      )}
      {emergency.proposals.map((proposal) => {
        const account = nameOf(proposal.account_id);
        const covered = formatCents(proposal.covered_cents, locale);
        return (
          <div key={proposal.account_id} className="forecast-proposal">
            <h3>{account}</h3>
            <Table aria-label={t`Pots that would be used on ${account}`}>
              <thead>
                <tr>
                  <th scope="col">
                    <Trans>Pot</Trans>
                  </th>
                  <th scope="col" className="num">
                    <Trans>Take</Trans>
                  </th>
                  <th scope="col" className="num">
                    <Trans>Left in the pot</Trans>
                  </th>
                  <th scope="col">
                    <Trans>Target date</Trans>
                  </th>
                </tr>
              </thead>
              <tbody>
                {proposal.lines.map((line) => (
                  <tr key={line.pot_id}>
                    <td>
                      {line.rank}. {line.name}
                    </td>
                    <MoneyCell cents={line.amount_cents} plain />
                    <MoneyCell cents={line.new_balance_cents} plain />
                    <td>
                      {line.target_date === null
                        ? "—"
                        : line.shifted_target_date !== null &&
                            line.shifted_target_date !== line.target_date
                          ? `${formatDay(line.target_date, locale)} → ${formatDay(line.shifted_target_date, locale)}`
                          : formatDay(line.target_date, locale)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </Table>
            <div className="ui-actions">
              <Button
                variant="primary"
                disabled={busy}
                onClick={() => onTake(proposal)}
              >
                <Trans>
                  Take {covered} from these pots ({account})
                </Trans>
              </Button>
            </div>
          </div>
        );
      })}
      {emergency.proposals.length > 0 && emergency.uncovered_cents > 0 && (
        <p role="status" className="forecast-alert">
          <Trans>Still not covered: {uncovered}</Trans>
        </p>
      )}
      <p className="ui-hint">
        <Trans>
          Taking money out of a pot moves it to the free balance of the account
          and may push the pot's target date. It does not change the balance of
          the account itself.
        </Trans>
      </p>
    </Card>
  );
}
