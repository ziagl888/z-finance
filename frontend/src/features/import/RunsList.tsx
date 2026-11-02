import { Trans, useLingui } from "@lingui/react/macro";

import { Table } from "../../ui";
import type { Account } from "../accounts/accountsApi";
import type { Run } from "./importApi";

interface Props {
  runs: Run[];
  accounts: Account[];
}

/** Earlier imports with their protocol figures, newest first. */
export function RunsList({ runs, accounts }: Props) {
  const { t, i18n } = useLingui();
  if (runs.length === 0) return null;
  const accountName = (id: number) =>
    accounts.find((a) => a.id === id)?.name ?? `#${id}`;
  return (
    <section className="import-runs">
      <h2>
        <Trans>Earlier imports</Trans>
      </h2>
      <Table aria-label={t`Earlier imports`}>
        <thead>
          <tr>
            <th>
              <Trans>When</Trans>
            </th>
            <th>
              <Trans>File</Trans>
            </th>
            <th>
              <Trans>Account</Trans>
            </th>
            <th className="num">
              <Trans>New</Trans>
            </th>
            <th className="num">
              <Trans>Matched</Trans>
            </th>
            <th className="num">
              <Trans>Duplicates</Trans>
            </th>
            <th className="num">
              <Trans>Uncategorised</Trans>
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.id}>
              <td>{new Date(run.created_at).toLocaleString(i18n.locale)}</td>
              <td>{run.file_name ?? "—"}</td>
              <td>{accountName(run.account_id)}</td>
              <td className="num">{run.counts.new ?? 0}</td>
              <td className="num">{run.counts.matched ?? 0}</td>
              <td className="num">{run.counts.duplicates ?? 0}</td>
              <td className="num">{run.counts.uncategorized ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </section>
  );
}
