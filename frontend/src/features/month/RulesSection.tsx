import { Plural, Trans, useLingui } from "@lingui/react/macro";

import type { Category } from "../accounts/accountsApi";
import { Badge, Button, Table } from "../../ui";
import type { Rule } from "./monthApi";

interface Props {
  rules: Rule[] | null;
  categories: Category[];
  onDelete: (rule: Rule) => void;
  onApply: () => void;
  /** Bookings the last "apply rules" categorised, or null before the first run. */
  lastApplied: number | null;
}

/**
 * The rules in the order the engine tries them (priority, then age). A rule that could not run —
 * an invalid pattern, or a regular expression that took too long — is marked and skipped; it is
 * kept until the user deletes it.
 */
export function RulesSection({
  rules,
  categories,
  onDelete,
  onApply,
  lastApplied,
}: Props) {
  const { t } = useLingui();
  const categoryName = (id: number) =>
    categories.find((c) => c.id === id)?.name ?? "—";

  if (rules === null) {
    return (
      <p>
        <Trans>Loading…</Trans>
      </p>
    );
  }
  if (rules.length === 0) {
    return (
      <p className="ui-hint">
        <Trans>
          No rules yet. Give an uncategorised booking a category and z-finance
          offers to make a rule from it.
        </Trans>
      </p>
    );
  }

  return (
    <>
      <Table>
        <thead>
          <tr>
            <th>
              <Trans>Pattern</Trans>
            </th>
            <th>
              <Trans>Match</Trans>
            </th>
            <th>
              <Trans>Category</Trans>
            </th>
            <th className="num">
              <Trans>Priority</Trans>
            </th>
            <th>
              <span className="sr-only">
                <Trans>Status and actions</Trans>
              </span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rules.map((rule) => {
            const pattern = rule.pattern;
            return (
              <tr
                key={rule.id}
                className={rule.invalid_reason ? "month-invalid" : ""}
              >
                <td>
                  <code>{rule.pattern}</code>
                </td>
                <td>
                  {rule.match_kind === "regex"
                    ? t`regular expression`
                    : t`contains`}
                </td>
                <td>{categoryName(rule.category_id)}</td>
                <td className="num">{rule.priority}</td>
                <td className="month-row-actions">
                  {rule.invalid_reason === "syntax" && (
                    <Badge tone="overdue">{t`Invalid pattern, skipped`}</Badge>
                  )}
                  {rule.invalid_reason === "timeout" && (
                    <Badge tone="overdue">{t`Too slow, skipped`}</Badge>
                  )}
                  <Button
                    variant="danger"
                    type="button"
                    aria-label={t`Delete rule ${pattern}`}
                    onClick={() => onDelete(rule)}
                  >
                    <Trans>Delete</Trans>
                  </Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </Table>
      <div className="ui-actions month-actions">
        <button type="button" onClick={onApply}>
          <Trans>Apply rules to uncategorised bookings</Trans>
        </button>
        {lastApplied !== null && (
          <span role="status" className="ui-notice">
            <Plural
              value={lastApplied}
              one="# booking categorised."
              other="# bookings categorised."
            />
          </span>
        )}
      </div>
    </>
  );
}
