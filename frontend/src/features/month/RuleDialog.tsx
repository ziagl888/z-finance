import { Plural, Trans, useLingui } from "@lingui/react/macro";
import { useEffect, useId, useState, type FormEvent } from "react";

import { Button, Dialog, Field } from "../../ui";
import type { Category } from "../accounts/accountsApi";
import {
  createRule,
  previewRule,
  type CreatedRule,
  type MatchKind,
  type RuleInput,
  type RulePreview,
} from "./monthApi";

const PREVIEW_DELAY_MS = 250;

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** Outcome of the last preview request, tied to the "kind:pattern" it was asked for. */
interface Previewed {
  key: string;
  result: RulePreview | null;
  error: string | null;
}

interface Props {
  /** The rule offered after a booking got its category: contains <text> → that category. */
  draft: RuleInput;
  categories: Category[];
  /** Called once after the rule is stored (the page reloads report, list and rules). */
  onCreated: () => void;
  onClose: () => void;
}

/**
 * "Make a rule from this booking" (UC-ZF-007 steps 4–5): pattern, kind and priority can be
 * changed; the number of uncategorised bookings the pattern hits is shown before saving, and the
 * number the new rule categorised right away is shown after.
 */
export function RuleDialog({ draft, categories, onCreated, onClose }: Props) {
  const { t } = useLingui();
  const ids = {
    pattern: useId(),
    kind: useId(),
    priority: useId(),
    counterparty: useId(),
  };
  const [pattern, setPattern] = useState(draft.pattern);
  const [kind, setKind] = useState<MatchKind>(draft.match_kind);
  const [priority, setPriority] = useState(String(draft.priority));
  const [counterparty, setCounterparty] = useState(draft.counterparty ?? "");
  // The last hit count (or refusal) and the pattern it belongs to; shown only while it still fits.
  const [previewed, setPreviewed] = useState<Previewed | null>(null);
  const [saved, setSaved] = useState<CreatedRule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const categoryName =
    categories.find((c) => c.id === draft.category_id)?.name ?? "—";
  const priorityValue = Number(priority);
  const priorityValid =
    priority.trim() !== "" && Number.isInteger(priorityValue);

  const previewKey = `${kind}:${pattern}`;
  const current = previewed?.key === previewKey ? previewed : null;
  const preview = current?.result ?? null;
  const previewError = current?.error ?? null;

  useEffect(() => {
    if (!pattern.trim()) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      previewRule(kind, pattern)
        .then((result) => {
          if (!cancelled)
            setPreviewed({ key: previewKey, result, error: null });
        })
        .catch((reason: unknown) => {
          if (!cancelled)
            setPreviewed({
              key: previewKey,
              result: null,
              error: message(reason),
            });
        });
    }, PREVIEW_DELAY_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [pattern, kind, previewKey]);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy || !pattern.trim() || !priorityValid) return;
    setBusy(true);
    setError(null);
    try {
      const created = await createRule({
        match_kind: kind,
        pattern: pattern.trim(),
        category_id: draft.category_id,
        counterparty: counterparty.trim() || null,
        priority: priorityValue,
      });
      setSaved(created);
      onCreated();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  if (saved) {
    const applied = saved.applied_count;
    return (
      <Dialog label={t`Make a rule`} className="month-dialog">
        <p role="status" className="ui-notice">
          <Plural
            value={applied}
            one="Rule saved. # booking categorised."
            other="Rule saved. # bookings categorised."
          />
        </p>
        <button type="button" onClick={onClose}>
          <Trans>Close</Trans>
        </button>
      </Dialog>
    );
  }

  return (
    <Dialog
      as="form"
      label={t`Make a rule`}
      className="month-dialog"
      onSubmit={(event) => void save(event)}
    >
      <h2>
        <Trans>Make a rule from this booking?</Trans>
      </h2>
      <p className="ui-hint">
        <Trans>
          Uncategorised bookings that match get the category{" "}
          <strong>{categoryName}</strong>. Bookings that already have a category
          are never changed.
        </Trans>
      </p>
      <div className="ui-form-grid">
        <Field
          htmlFor={ids.pattern}
          label={<Trans>Text to look for</Trans>}
          className="month-field-wide"
        >
          <input
            id={ids.pattern}
            value={pattern}
            maxLength={200}
            onChange={(event) => setPattern(event.target.value)}
          />
        </Field>
        <Field htmlFor={ids.kind} label={<Trans>Match</Trans>}>
          <select
            id={ids.kind}
            value={kind}
            onChange={(event) => setKind(event.target.value as MatchKind)}
          >
            <option value="contains">{t`Text contains the pattern`}</option>
            <option value="regex">{t`Regular expression`}</option>
          </select>
        </Field>
        <Field htmlFor={ids.priority} label={<Trans>Priority</Trans>}>
          <input
            id={ids.priority}
            type="number"
            step={1}
            value={priority}
            aria-invalid={!priorityValid}
            onChange={(event) => setPriority(event.target.value)}
          />
        </Field>
        <Field
          htmlFor={ids.counterparty}
          label={<Trans>Counterparty (filled in where empty)</Trans>}
        >
          <input
            id={ids.counterparty}
            value={counterparty}
            maxLength={100}
            onChange={(event) => setCounterparty(event.target.value)}
          />
        </Field>
      </div>
      <p className="ui-hint">
        <Trans>
          If two rules match, the higher priority wins; at equal priority the
          older rule.
        </Trans>
      </p>
      {preview !== null && (
        <p role="status" className="ui-notice">
          <Plural
            value={preview.count}
            one="# other uncategorised booking matches."
            other="# other uncategorised bookings match."
          />
        </p>
      )}
      {previewError && (
        <p role="alert" className="error">
          {previewError}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="ui-actions month-actions">
        <Button
          variant="primary"
          type="submit"
          disabled={
            busy || !pattern.trim() || !priorityValid || previewError !== null
          }
        >
          <Trans>Save rule</Trans>
        </Button>
        <button type="button" onClick={onClose}>
          <Trans>Not now</Trans>
        </button>
      </div>
    </Dialog>
  );
}
