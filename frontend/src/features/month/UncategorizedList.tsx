import { Trans, useLingui } from "@lingui/react/macro";

import type { Category } from "../accounts/accountsApi";
import { MoneyCell, Table } from "../../ui";
import type { MonthBooking } from "./monthApi";

interface Props {
  bookings: MonthBooking[] | null;
  categories: Category[];
  onAssign: (item: MonthBooking, categoryId: number) => void;
}

/**
 * Bookings of the month without a category (UC-ZF-007 step 3). Choosing a category assigns it
 * at once (step 4); the booking then drops out of the list and a rule is offered.
 */
export function UncategorizedList({ bookings, categories, onAssign }: Props) {
  const { t, i18n } = useLingui();
  const choices = categories.filter((c) => c.kind !== "transfer");

  if (bookings === null) {
    return (
      <p>
        <Trans>Loading…</Trans>
      </p>
    );
  }
  if (bookings.length === 0) {
    return (
      <p className="ui-hint">
        <Trans>Every booking of this month has a category.</Trans>
      </p>
    );
  }

  return (
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
          <th>
            <Trans>Category</Trans>
          </th>
        </tr>
      </thead>
      <tbody>
        {bookings.map((item) => {
          const rowLabel = item.text || item.date;
          return (
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
              <MoneyCell cents={item.amount_cents} />
              <td>
                <select
                  aria-label={t`Category for ${rowLabel}`}
                  value=""
                  onChange={(event) => {
                    if (event.target.value)
                      onAssign(item, Number(event.target.value));
                  }}
                >
                  <option value="">{t`Choose a category…`}</option>
                  {choices.map((category) => (
                    <option key={category.id} value={category.id}>
                      {category.name}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}
