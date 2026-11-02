import { Trans, useLingui } from "@lingui/react/macro";

import { formatCents } from "../../api/client";
import { Badge, Button, cx, MoneyCell, type BadgeTone } from "../../ui";
import type { Category } from "../accounts/accountsApi";
import { CATEGORY_KIND_LABELS } from "../accounts/labels";
import type { Transaction } from "../entry/entryApi";
import type { LineKind, PreviewLine } from "./importApi";
import { KIND_LABELS } from "./labels";
import {
  effectiveCategory,
  effectivePlanned,
  willWrite,
  type LineEdit,
} from "./logic";

const KIND_TONES: Record<LineKind, BadgeTone> = {
  new: "ok",
  duplicate: "skipped",
  match: "planned",
  duplicate_suspect: "warn",
};

/** What the user can change on a line; `withEdit` in logic.ts folds it into the edits. */
export interface LineChange {
  skip?: boolean;
  categoryId?: number | null;
  planned?: number | null;
}

interface Props {
  line: PreviewLine;
  edit: LineEdit | undefined;
  categories: Category[];
  /** Planned items and resembling bookings by id, as far as loaded. */
  bookings: Record<number, Transaction>;
  /** Planned items another written line uses; they cannot be chosen again. */
  claimed: Set<number>;
  onChange: (change: LineChange) => void;
  /** Offer the rule dialog for the corrected category. */
  onRule: (categoryId: number) => void;
}

/** One line of the preview: classification, the category to correct, the match beside it. */
export function PreviewRow({
  line,
  edit,
  categories,
  bookings,
  claimed,
  onChange,
  onRule,
}: Props) {
  const { t, i18n } = useLingui();
  const written = willWrite(line, edit);
  const isDuplicate = line.kind === "duplicate";
  const n = line.line;
  const date = new Date(`${line.date}T00:00:00`).toLocaleDateString(
    i18n.locale,
  );
  const category = effectiveCategory(line, edit);
  const planned = effectivePlanned(line, edit);

  const describe = (id: number | null) => {
    if (id === null) return "—";
    const booking = bookings[id];
    if (!booking) return `#${id}`;
    const day = new Date(`${booking.date}T00:00:00`).toLocaleDateString(
      i18n.locale,
    );
    return `${booking.text || "—"} · ${day} · ${formatCents(booking.amount_cents, i18n.locale)}`;
  };

  const resembles = describe(line.suspect_id);
  const source =
    edit?.categoryId !== undefined
      ? t`Changed by you`
      : line.category_source === "rule"
        ? t`From a rule`
        : line.category_source === "planned"
          ? t`From the planned item`
          : null;

  return (
    <tr
      className={cx(
        "import-row",
        (isDuplicate || !written) && "import-row-out",
      )}
    >
      <td>
        {!isDuplicate && (
          <input
            type="checkbox"
            checked={written}
            aria-label={t`Import line ${n}`}
            onChange={(event) => onChange({ skip: !event.target.checked })}
          />
        )}
      </td>
      <td className="num">{line.line}</td>
      <td>{date}</td>
      <td className="import-text">
        {line.text || "—"}
        {line.counterparty && (
          <span className="import-sub">{line.counterparty}</span>
        )}
      </td>
      <MoneyCell cents={line.amount_cents} />
      <td>
        <Badge tone={KIND_TONES[line.kind]}>
          {i18n._(KIND_LABELS[line.kind])}
        </Badge>
        {line.kind === "match" &&
          line.review &&
          edit?.planned === undefined && (
            <Badge tone="warn" className="import-flag">
              <Trans>Check the match</Trans>
            </Badge>
          )}
      </td>
      <td className="import-category">
        {isDuplicate ? (
          "—"
        ) : (
          <>
            <select
              aria-label={t`Category of line ${n}`}
              value={category === null ? "" : String(category)}
              onChange={(event) =>
                onChange({
                  categoryId:
                    event.target.value === ""
                      ? null
                      : Number(event.target.value),
                })
              }
            >
              {line.category_id === null && (
                <option value="">{t`Uncategorised`}</option>
              )}
              {categories.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} ({i18n._(CATEGORY_KIND_LABELS[item.kind])})
                </option>
              ))}
            </select>
            {source && <span className="import-sub">{source}</span>}
            {edit?.categoryId !== undefined && (
              <Button
                className="import-rule"
                aria-label={t`Save the category of line ${n} as a rule`}
                onClick={() => onRule(edit.categoryId as number)}
              >
                <Trans>Save as rule</Trans>
              </Button>
            )}
          </>
        )}
      </td>
      <td className="import-match">
        {line.kind === "match" && (
          <>
            {planned !== null ? (
              <span>{describe(planned)}</span>
            ) : (
              <span className="import-sub">
                <Trans>No match: booked as a new entry.</Trans>
              </span>
            )}
            {line.candidates.length > 1 && (
              <select
                aria-label={t`Planned item for line ${n}`}
                value={planned === null ? "none" : String(planned)}
                onChange={(event) =>
                  onChange({
                    planned:
                      event.target.value === "none"
                        ? null
                        : Number(event.target.value),
                  })
                }
              >
                {line.candidates.map((id) => (
                  <option key={id} value={id} disabled={claimed.has(id)}>
                    {describe(id)}
                  </option>
                ))}
                <option value="none">{t`No match`}</option>
              </select>
            )}
            {planned !== null ? (
              <Button
                aria-label={t`Remove the match of line ${n}`}
                onClick={() => onChange({ planned: null })}
              >
                <Trans>Remove match</Trans>
              </Button>
            ) : (
              <Button
                aria-label={t`Restore the match of line ${n}`}
                onClick={() => onChange({ planned: line.planned_id })}
              >
                <Trans>Restore match</Trans>
              </Button>
            )}
          </>
        )}
        {line.kind === "duplicate_suspect" && (
          <>
            <span>
              <Trans>Looks like: {resembles}</Trans>
            </span>
            <span className="import-sub">
              <Trans>Not imported unless you tick it.</Trans>
            </span>
          </>
        )}
        {isDuplicate && (
          <span className="import-sub">
            <Trans>Imported before.</Trans>
          </span>
        )}
      </td>
    </tr>
  );
}
