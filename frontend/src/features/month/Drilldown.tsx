import { Trans, useLingui } from "@lingui/react/macro";

import { Card, MoneyCell, Table } from "../../ui";
import type { MonthBooking } from "./monthApi";

interface Props {
  title: string;
  bookings: MonthBooking[] | null;
  onClose: () => void;
}

/** The single bookings behind a category line (UC-ZF-007 step 6); their sum is the line's amount. */
export function Drilldown({ title, bookings, onClose }: Props) {
  const { t, i18n } = useLingui();
  const sum = (bookings ?? []).reduce(
    (acc, item) => acc + item.amount_cents,
    0,
  );

  return (
    <Card as="section" aria-label={t`Bookings of the category`}>
      <div className="month-card-head">
        <h2>{title}</h2>
        <button type="button" onClick={onClose}>
          <Trans>Close</Trans>
        </button>
      </div>
      {bookings === null ? (
        <p>
          <Trans>Loading…</Trans>
        </p>
      ) : (
        <Table>
          <thead>
            <tr>
              <th>
                <Trans>Date</Trans>
              </th>
              <th>
                <Trans>Text</Trans>
              </th>
              <th>
                <Trans>Account</Trans>
              </th>
              <th className="num">
                <Trans>Amount</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {bookings.map((item) => (
              <tr key={item.id}>
                <td>
                  {new Date(`${item.date}T00:00:00`).toLocaleDateString(
                    i18n.locale,
                  )}
                </td>
                <td>
                  {item.text || "—"}
                  {item.counterparty && (
                    <span className="muted month-sub">{item.counterparty}</span>
                  )}
                </td>
                <td>{item.account_name}</td>
                <MoneyCell plain cents={item.amount_cents} />
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" colSpan={3}>
                <Trans>Sum</Trans>
              </th>
              <MoneyCell plain cents={sum} />
            </tr>
          </tfoot>
        </Table>
      )}
    </Card>
  );
}
