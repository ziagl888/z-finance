import { Plural, Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useRef, useState } from "react";

import { Button, Card, KpiTile, Table } from "../../ui";
import type { Category } from "../accounts/accountsApi";
import type { Transaction } from "../entry/entryApi";
import { RuleDialog } from "../month/RuleDialog";
import type { Preview } from "./importApi";
import { PreviewRow, type LineChange } from "./PreviewRow";
import {
  claimedPlanned,
  liveCounts,
  ruleDraft,
  withEdit,
  type Edits,
} from "./logic";

interface Props {
  preview: Preview;
  edits: Edits;
  onEdits: (edits: Edits) => void;
  categories: Category[];
  bookings: Record<number, Transaction>;
  /** The file is being classified again (after a rule was saved or on request). */
  refreshing: boolean;
  committing: boolean;
  commitError: string | null;
  onBack: () => void;
  onRefresh: () => void;
  onCommit: () => void;
}

/**
 * Step 3 (UC-ZF-005 3–5): every line as the engine classified it, with the corrections the user
 * can make (category, match, leave out) and the button that writes it all. The figures above the
 * table follow the corrections; the protocol after the commit reports what was written.
 */
export function PreviewStep({
  preview,
  edits,
  onEdits,
  categories,
  bookings,
  refreshing,
  committing,
  commitError,
  onBack,
  onRefresh,
  onCommit,
}: Props) {
  const { t } = useLingui();
  const [hideDuplicates, setHideDuplicates] = useState(false);
  const [ruleFor, setRuleFor] = useState<{
    line: number;
    categoryId: number;
  } | null>(null);
  const ruleBox = useRef<HTMLDivElement>(null);
  // The dialog opens below a possibly long table: bring it into view (not every environment can scroll).
  useEffect(() => {
    if (ruleFor) ruleBox.current?.scrollIntoView?.({ block: "nearest" });
  }, [ruleFor]);
  const live = liveCounts(preview.lines, edits);
  const { duplicates = 0, suspects = 0 } = preview.counts;
  const write = live.write;
  const errorCount = preview.errors.length;
  const lines = hideDuplicates
    ? preview.lines.filter((line) => line.kind !== "duplicate")
    : preview.lines;
  const ruleLine =
    ruleFor && preview.lines.find((line) => line.line === ruleFor.line);
  const draft =
    ruleLine && ruleFor ? ruleDraft(ruleLine, ruleFor.categoryId) : null;

  return (
    <div className="import-preview">
      <div className="ui-kpis">
        <KpiTile label={<Trans>New</Trans>} value={live.fresh} />
        <KpiTile
          label={<Trans>Match a planned item</Trans>}
          value={live.matched}
        />
        <KpiTile
          label={<Trans>Duplicates</Trans>}
          value={duplicates}
          sub={<Trans>not imported</Trans>}
        />
        <KpiTile label={<Trans>Possible duplicates</Trans>} value={suspects} />
        <KpiTile
          label={<Trans>Without category</Trans>}
          value={live.uncategorized}
        />
        <KpiTile
          label={<Trans>Lines with errors</Trans>}
          value={errorCount}
          tone={errorCount > 0 ? "alert" : "default"}
        />
      </div>
      {errorCount > 0 && (
        <Card className="import-errors">
          <h2>
            <Trans>These lines are not imported</Trans>
          </h2>
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
              {preview.errors.map((error) => (
                <tr key={`${error.line}-${error.message}`}>
                  <td className="num">{error.line}</td>
                  <td>{error.message}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      {/* Above the table: the sticky action bar at the bottom would cover a dialog placed below it. */}
      <div ref={ruleBox}>
        {ruleFor && ruleLine && draft === null && (
          <p role="alert" className="error">
            <Trans>This line has no text to make a rule from.</Trans>
          </p>
        )}
        {draft && (
          <RuleDialog
            key={`${ruleFor?.line}-${ruleFor?.categoryId}`}
            draft={draft}
            categories={categories}
            onCreated={onRefresh}
            onClose={() => setRuleFor(null)}
          />
        )}
      </div>
      <label className="import-hide">
        <input
          type="checkbox"
          checked={hideDuplicates}
          onChange={(event) => setHideDuplicates(event.target.checked)}
        />
        <Trans>Hide duplicates</Trans>
      </label>
      <Table aria-label={t`Preview of the import`} className="import-table">
        <thead>
          <tr>
            <th>
              <span className="sr-only">
                <Trans>Import</Trans>
              </span>
            </th>
            <th className="num">
              <Trans>Line</Trans>
            </th>
            <th>
              <Trans>Date</Trans>
            </th>
            <th>
              <Trans>Text</Trans>
            </th>
            <th className="num">
              <Trans>Amount</Trans>
            </th>
            <th>
              <Trans>Result</Trans>
            </th>
            <th>
              <Trans>Category</Trans>
            </th>
            <th>
              <Trans>Planned item / similar booking</Trans>
            </th>
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <PreviewRow
              key={line.line}
              line={line}
              edit={edits[line.line]}
              categories={categories}
              bookings={bookings}
              claimed={claimedPlanned(preview.lines, edits, line.line)}
              onChange={(change: LineChange) =>
                onEdits(withEdit(edits, line, change))
              }
              onRule={(categoryId) =>
                setRuleFor({ line: line.line, categoryId })
              }
            />
          ))}
        </tbody>
      </Table>
      {commitError && (
        <p role="alert" className="error">
          {commitError}
        </p>
      )}
      <div className="ui-actions import-commit">
        <Button onClick={onBack}>
          <Trans>Back to the profile</Trans>
        </Button>
        <Button disabled={refreshing || committing} onClick={onRefresh}>
          <Trans>Preview again</Trans>
        </Button>
        <Button
          variant="primary"
          disabled={committing || refreshing || write === 0}
          onClick={onCommit}
        >
          <Plural
            value={write}
            one="Apply: # booking"
            other="Apply: # bookings"
          />
        </Button>
        {committing && (
          <span role="status" className="ui-hint">
            <Trans>Writing the bookings…</Trans>
          </span>
        )}
        {write === 0 && (
          <span className="ui-hint">
            <Trans>
              Nothing to import: every line is a duplicate or left out.
            </Trans>
          </span>
        )}
      </div>
    </div>
  );
}
