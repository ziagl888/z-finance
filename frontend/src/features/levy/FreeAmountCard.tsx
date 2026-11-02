import { Trans, useLingui } from "@lingui/react/macro";

import { Card, MoneyCell, Table } from "../../ui";
import { formatDay } from "./draft";
import type { FreeAmount } from "./levyApi";

/**
 * The core rule made visible: "free" is net of the open levy gap. The terms are the API's
 * (`/api/levy/free-amount`), shown as a sum so the reader can follow every line.
 */
export function FreeAmountCard({ free }: { free: FreeAmount }) {
  const { t, i18n } = useLingui();
  const until = formatDay(free.as_of, i18n.locale);
  return (
    <Card as="section" aria-labelledby="levy-free-title">
      <h2 id="levy-free-title">
        <Trans>Free until {until}</Trans>
      </h2>
      <Table aria-label={t`How the free amount is calculated`}>
        <tbody>
          <tr>
            <th scope="row">
              <Trans>Balance of your payment accounts</Trans>
            </th>
            <MoneyCell cents={free.accounts_cents} />
          </tr>
          <tr>
            <th scope="row">
              <Trans>Planned items until {until}</Trans>
            </th>
            <MoneyCell cents={free.planned_cents} />
          </tr>
          <tr>
            <th scope="row">
              <Trans>Reserve floors of the accounts</Trans>
            </th>
            <MoneyCell cents={-free.reserve_cents} />
          </tr>
          <tr className="levy-gap-row">
            <th scope="row">
              <Trans>Open levy gap</Trans>
            </th>
            <MoneyCell cents={-free.levy_gap_cents} />
          </tr>
        </tbody>
        <tfoot>
          <tr className="levy-total">
            <th scope="row">
              <Trans>Free</Trans>
            </th>
            <MoneyCell cents={free.free_cents} />
          </tr>
        </tfoot>
      </Table>
      <p className="ui-hint levy-rule">
        <Trans>
          The open levy gap is money that income tax and SVS will claim, so it
          is not free to spend: it is subtracted from what your accounts hold.
        </Trans>
      </p>
    </Card>
  );
}
