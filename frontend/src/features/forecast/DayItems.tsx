import { Trans, useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import { Badge, Card, MoneyCell, Table, type BadgeTone } from "../../ui";
import type { Account } from "../accounts/accountsApi";
import { formatDay } from "../levy/draft";
import { dayChange } from "./draft";
import type { ForecastDay, ForecastItem } from "./forecastApi";

interface Props {
  /** The chosen day of the curve, `null` while none is chosen. */
  day: ForecastDay | null;
  /** The rows of that day (stored ones from the API plus the what-if ones); `null` while loading. */
  rows: ForecastItem[] | null;
  accounts: Account[];
}

/**
 * UC-ZF-006 step 4 (AK4): the items that act on the chosen day — exactly the rows the API lists
 * for that date, what-if items marked as such — and what the day does to the curve.
 */
export function DayItems({ day, rows, accounts }: Props) {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  if (day === null) {
    return (
      <Card as="section" aria-labelledby="forecast-day-title">
        <h2 id="forecast-day-title">
          <Trans>Items of a day</Trans>
        </h2>
        <p className="ui-hint">
          <Trans>
            Click a point of the curve, or focus the chart, choose a day with
            the arrow keys and press Enter, to see what happens on that day.
          </Trans>
        </p>
      </Card>
    );
  }
  const date = formatDay(day.date, locale);
  const balance = formatCents(day.balance_cents, locale);
  const available = formatCents(day.available_cents, locale);
  const badge = (row: ForecastItem): { tone: BadgeTone; label: string } => {
    if (row.kind === "what_if") return { tone: "warn", label: t`What if` };
    if (row.overdue) return { tone: "overdue", label: t`Overdue` };
    return row.kind === "booked"
      ? { tone: "neutral", label: t`Booked` }
      : { tone: "planned", label: t`Planned` };
  };
  return (
    <Card as="section" aria-labelledby="forecast-day-title">
      <h2 id="forecast-day-title">
        <Trans>Items on {date}</Trans>
      </h2>
      <p className="ui-hint" role="status">
        <Trans>
          At the end of this day: balance {balance}, available after levies{" "}
          {available}.
        </Trans>
      </p>
      {rows === null ? (
        <p role="status" className="muted">
          <Trans>Loading…</Trans>
        </p>
      ) : rows.length === 0 ? (
        <p className="muted">
          <Trans>Nothing is planned or booked for this day.</Trans>
        </p>
      ) : (
        <Table aria-label={t`Items on the chosen day`}>
          <thead>
            <tr>
              <th scope="col">
                <Trans>Account</Trans>
              </th>
              <th scope="col">
                <Trans>Text</Trans>
              </th>
              <th scope="col">
                <Trans>Status</Trans>
              </th>
              <th scope="col" className="num">
                <Trans>Amount</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const { tone, label } = badge(row);
              const account = accounts.find((a) => a.id === row.account_id);
              return (
                <tr key={`${row.transaction_id ?? "whatif"}-${i}`}>
                  <td>{account?.name ?? `#${row.account_id}`}</td>
                  <td>{row.text === "" ? "—" : row.text}</td>
                  <td>
                    <Badge tone={tone}>{label}</Badge>
                  </td>
                  <MoneyCell cents={row.amount_cents} />
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            <tr>
              <th scope="row" colSpan={3}>
                <Trans>Change of this day</Trans>
              </th>
              <MoneyCell cents={dayChange(rows)} />
            </tr>
          </tfoot>
        </Table>
      )}
    </Card>
  );
}
