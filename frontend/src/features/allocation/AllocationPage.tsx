import { Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { formatCents } from "../../api/client";
import { Badge, Button, Card, KpiTile, Table } from "../../ui";
import { ProposalCard } from "./ProposalList";
import { RuleForm } from "./RuleForm";
import {
  confirmProposal,
  deleteRule,
  emptyRule,
  formatBp,
  getAllocation,
  percentSum,
  postRule,
  putRule,
  skipProposal,
  toInput,
  type AllocationOverview,
  type Rule,
  type RuleInput,
} from "./allocationApi";
import "./allocation.css";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * Allocation (UC-ZF-014): the rules that split an income, the proposals waiting to be confirmed,
 * and the reversal suggestions of a cancelled income. The levy share of a proposal is a
 * reservation, not a proposal: it is already out of "free until the end of the month".
 */
export function AllocationPage() {
  const { t, i18n } = useLingui();
  const locale = i18n.locale;
  const [data, setData] = useState<AllocationOverview | null>(null);
  const [editing, setEditing] = useState<Rule | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getAllocation()
      .then((loaded) => !cancelled && setData(loaded))
      .catch((reason: unknown) => !cancelled && setError(message(reason)));
    return () => {
      cancelled = true;
    };
  }, []);

  async function reload() {
    setData(await getAllocation());
  }

  async function onSave(input: RuleInput) {
    if (editing === null) return;
    if (editing === "new") await postRule(input);
    else await putRule(editing.id, input);
    setEditing(null);
    setNotice(t`Rule saved.`);
    await reload();
  }

  async function onDelete(rule: Rule) {
    setError(null);
    try {
      await deleteRule(rule.id);
      setNotice(t`Rule deleted.`);
      await reload();
    } catch (reason) {
      setError(message(reason));
    }
  }

  async function onConfirm(id: number, amounts: Record<number, number>) {
    const result = await confirmProposal(id, amounts);
    const booked = formatCents(result.booked_cents, locale);
    setNotice(t`Allocated ${booked}.`);
    await reload();
  }

  async function onSkip(id: number) {
    await skipProposal(id);
    setNotice(t`Proposal rejected.`);
    await reload();
  }

  if (data === null) {
    return (
      <section className="allocation">
        <h1>
          <Trans>Allocation</Trans>
        </h1>
        {error ? (
          <p role="alert" className="error">
            {error}
          </p>
        ) : (
          <p className="muted">
            <Trans>Loading…</Trans>
          </p>
        )}
      </section>
    );
  }

  const money = (cents: number) => formatCents(cents, locale);
  const levyRate = formatBp(data.levy_rate_bp, locale);

  return (
    <section className="allocation">
      <h1>
        <Trans>Allocation</Trans>
      </h1>
      <p className="allocation-intro muted">
        <Trans>
          Decide once where your income goes, and confirm with one click when it
          arrives. The share for your levies is set aside the moment the income
          is booked, so it never shows up as free money.
        </Trans>
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="ui-notice">
          {notice}
        </p>
      )}

      <div className="ui-kpis">
        <KpiTile
          label={<Trans>To allocate</Trans>}
          value={String(data.proposals.length)}
          tone={
            data.proposals.some((proposal) => proposal.overdue)
              ? "alert"
              : "default"
          }
          sub={<Trans>proposals waiting</Trans>}
        />
        <KpiTile
          label={<Trans>Free until the end of the month</Trans>}
          value={money(data.free_cents)}
          tone={data.free_cents < 0 ? "alert" : "default"}
          sub={<Trans>after levies and reserve</Trans>}
        />
        <KpiTile
          label={<Trans>Levy share</Trans>}
          value={`${levyRate} %`}
          sub={<Trans>from Taxes &amp; levies</Trans>}
        />
        <KpiTile
          label={<Trans>Rejected this month</Trans>}
          value={String(data.skipped_this_month)}
        />
      </div>

      {data.levy_pot_missing && (
        <p className="ui-hint">
          <Trans>
            A levy rate is set but no pot is marked as your levy pot, so no levy
            share is proposed. Mark one under{" "}
            <Link to="/tax">Taxes &amp; levies</Link>.
          </Trans>
        </p>
      )}

      <Card as="section" aria-labelledby="allocation-proposals">
        <h2 id="allocation-proposals">
          <Trans>To allocate</Trans>
        </h2>
        {data.proposals.length === 0 ? (
          <p className="muted">
            <Trans>
              Nothing to allocate. As soon as an income matches one of your
              rules, its proposal appears here.
            </Trans>
          </p>
        ) : (
          <ul className="allocation-proposals">
            {data.proposals.map((proposal) => (
              <ProposalCard
                key={proposal.id}
                proposal={proposal}
                onConfirm={onConfirm}
                onSkip={onSkip}
              />
            ))}
          </ul>
        )}
      </Card>

      {data.reversals.length > 0 && (
        <Card as="section" aria-labelledby="allocation-reversals">
          <h2 id="allocation-reversals">
            <Trans>Income removed</Trans>{" "}
            <Badge tone="warn">
              <Trans>check</Trans>
            </Badge>
          </h2>
          <p>
            <Trans>
              These incomes are gone, but the transfers you confirmed for them
              are still booked. Undo them in the{" "}
              <Link to="/entry">quick entry</Link> if the money did not move.
            </Trans>
          </p>
          <Table>
            <thead>
              <tr>
                <th scope="col">
                  <Trans>Income</Trans>
                </th>
                <th scope="col">
                  <Trans>Date</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Booked</Trans>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.reversals.map((reversal) => (
                <tr key={reversal.proposal_id}>
                  <td>{reversal.text}</td>
                  <td>{reversal.date}</td>
                  <td className="num">{money(reversal.booked_cents)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}

      <Card as="section" aria-labelledby="allocation-rules">
        <h2 id="allocation-rules">
          <Trans>Rules</Trans>
        </h2>
        {data.rules.length === 0 ? (
          <p className="muted">
            <Trans>No rule yet. Add one to start allocating.</Trans>
          </p>
        ) : (
          <Table>
            <thead>
              <tr>
                <th scope="col">
                  <Trans>Name</Trans>
                </th>
                <th scope="col">
                  <Trans>Applies to</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>From</Trans>
                </th>
                <th scope="col" className="num">
                  <Trans>Shares</Trans>
                </th>
                <th scope="col">
                  <Trans>Actions</Trans>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.rules.map((rule) => (
                <tr key={rule.id}>
                  <td>
                    {rule.name}
                    {!rule.active && (
                      <>
                        {" "}
                        <Badge tone="skipped">
                          <Trans>off</Trans>
                        </Badge>
                      </>
                    )}
                  </td>
                  <td>
                    {rule.area === "business" ? (
                      <Trans>Business income</Trans>
                    ) : rule.area === "private" ? (
                      <Trans>Private income</Trans>
                    ) : (
                      <Trans>Every income</Trans>
                    )}
                  </td>
                  <td className="num">{money(rule.min_amount_cents)}</td>
                  <td className="num">
                    {formatBp(percentSum(rule.targets), locale)} %
                  </td>
                  <td>
                    <Button
                      aria-label={t`Edit ${rule.name}`}
                      onClick={() => setEditing(rule)}
                    >
                      <Trans>Edit</Trans>
                    </Button>{" "}
                    <Button
                      variant="danger"
                      aria-label={t`Delete ${rule.name}`}
                      onClick={() => void onDelete(rule)}
                    >
                      <Trans>Delete</Trans>
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
        {editing === null ? (
          <div className="ui-actions">
            <Button variant="primary" onClick={() => setEditing("new")}>
              <Trans>New rule</Trans>
            </Button>
          </div>
        ) : (
          <RuleForm
            value={editing === "new" ? emptyRule() : toInput(editing)}
            pots={data.pots}
            levyRateBp={data.levy_rate_bp}
            editing={editing !== "new"}
            onSave={onSave}
            onCancel={() => setEditing(null)}
          />
        )}
      </Card>
    </section>
  );
}
