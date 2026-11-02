import { Trans, useLingui } from "@lingui/react/macro";
import { Link } from "react-router";

import { Button, Card, KpiTile, Table } from "../../ui";
import type { Run } from "./importApi";

interface Props {
  run: Run;
  onAgain: () => void;
}

/** Step 4 (UC-ZF-005 6, AK5): the protocol of what was written — the run's own figures. */
export function DoneStep({ run, onAgain }: Props) {
  const { t } = useLingui();
  const counts = run.counts;
  const written = (counts.new ?? 0) + (counts.matched ?? 0);
  const uncategorized = counts.uncategorized ?? 0;
  const skipped = counts.skipped ?? 0;
  const suspects = counts.suspects ?? 0;
  const errors = run.errors.length;

  return (
    <Card className="import-step import-done">
      <h2>
        <Trans>Import finished</Trans>
      </h2>
      <p role="status" className="ui-notice">
        <Trans>{written} bookings written, in one step.</Trans>
      </p>
      <div className="ui-kpis" aria-label={t`Import protocol`}>
        <KpiTile label={<Trans>New</Trans>} value={counts.new ?? 0} />
        <KpiTile
          label={<Trans>Matched to planned items</Trans>}
          value={counts.matched ?? 0}
        />
        <KpiTile
          label={<Trans>Duplicates</Trans>}
          value={counts.duplicates ?? 0}
          sub={<Trans>not imported</Trans>}
        />
        <KpiTile
          label={<Trans>Uncategorised</Trans>}
          value={uncategorized}
          sub={<Trans>of the new bookings</Trans>}
        />
      </div>
      {(skipped > 0 || suspects > 0 || errors > 0) && (
        <p className="ui-hint">
          <Trans>
            Left out: {skipped}. Possible duplicates found: {suspects}. Lines
            with errors: {errors}.
          </Trans>
        </p>
      )}
      {errors > 0 && (
        <Table aria-label={t`Lines with errors`}>
          <thead>
            <tr>
              <th className="num">
                <Trans>Line</Trans>
              </th>
              <th>
                <Trans>Reason</Trans>
              </th>
            </tr>
          </thead>
          <tbody>
            {run.errors.map((error) => (
              <tr key={`${error.line}-${error.message}`}>
                <td className="num">{error.line}</td>
                <td>{error.message}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <div className="ui-actions">
        <Button variant="primary" onClick={onAgain}>
          <Trans>Import another file</Trans>
        </Button>
        <Link to="/entry">
          <Trans>Latest bookings</Trans>
        </Link>
        {uncategorized > 0 && (
          <Link to="/month">
            <Trans>Categorise the rest in the month report</Trans>
          </Link>
        )}
      </div>
    </Card>
  );
}
