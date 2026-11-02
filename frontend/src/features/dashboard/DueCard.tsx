import { Trans, useLingui } from "@lingui/react/macro";
import { Link } from "react-router";

import { Badge, Card, MoneyCell, Table } from "../../ui";
import { formatDay } from "../levy/draft";
import type { DueItem } from "./dashboardApi";

/**
 * The planned items of the next 14 days (SPEC AK8), overdue ones first and marked. Confirming,
 * postponing and skipping happen on the Due items screen (UC-ZF-004); this card only shows them.
 */
export function DueCard({ items }: { items: DueItem[] }) {
  const { t, i18n } = useLingui();
  return (
    <Card as="section" aria-labelledby="dashboard-due-title">
      <h2 id="dashboard-due-title">
        <Trans>Due in the next 14 days</Trans>
      </h2>
      {items.length === 0 ? (
        <p className="ui-hint">
          <Trans>
            Nothing is due. Planned items come from{" "}
            <Link to="/recurring">Recurring items</Link>.
          </Trans>
        </p>
      ) : (
        <Table aria-label={t`Items due in the next 14 days`}>
          <tbody>
            {items.map((item) => (
              <tr key={item.id}>
                <td>{formatDay(item.date, i18n.locale)}</td>
                <td>
                  {item.text === "" ? "—" : item.text}
                  {item.days_overdue > 0 && (
                    <Badge tone="overdue" className="dashboard-badge">
                      <Trans>overdue</Trans>
                    </Badge>
                  )}
                </td>
                <MoneyCell cents={item.amount_cents} />
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <p className="ui-actions">
        <Link to="/due">
          <Trans>Go to Due items</Trans>
        </Link>
      </p>
    </Card>
  );
}
