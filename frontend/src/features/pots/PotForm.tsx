import { Trans, useLingui } from "@lingui/react/macro";
import { useState, type FormEvent } from "react";

import { Button, Dialog, Field } from "../../ui";
import { AREAS, type Account } from "../accounts/accountsApi";
import { AREA_LABELS } from "../accounts/labels";
import {
  draftFrom,
  emptyDraft,
  LOCKED,
  orderedPots,
  reorder,
  toInput,
  type Invalid,
  type PotDraft,
} from "./draft";
import { createPot, listPots, putRanks, updatePot, type Pot } from "./potsApi";

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

interface Props {
  /** Active accounts a pot can live on (liability accounts left out). */
  accounts: Account[];
  /** All open pots, to count the emergency positions of the chosen account. */
  pots: Pot[];
  /** Pot opened for editing, or null for a new one. */
  editing: Pot | null;
  /** `warning` is set when the pot was saved but its position could not be applied. */
  onSaved: (pot: Pot, warning: string | null) => Promise<void>;
  onClose: () => void;
}

/**
 * Create or edit a pot (UC-ZF-011 step 1): name, account, area, target amount and date (both
 * optional, 1a/1b), "plan the expense on the target date", the place in the emergency order or
 * "locked", and the marker of the levy reserve pot. The account of an existing pot cannot change.
 * The API puts an unlocked pot at the end of the order; another chosen position is applied with a
 * second call, which says so when it fails on its own.
 */
export function PotForm({ accounts, pots, editing, onSaved, onClose }: Props) {
  const { t, i18n } = useLingui();
  const [draft, setDraft] = useState<PotDraft>(() =>
    editing ? draftFrom(editing, i18n.locale) : emptyDraft(accounts[0]),
  );
  const [invalid, setInvalid] = useState<Invalid | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const update = (changes: Partial<PotDraft>) =>
    setDraft((previous) => ({ ...previous, ...changes }));

  const accountId = Number(draft.accountId);
  // Pots that share the emergency order with this one (the edited pot itself left out).
  const others = orderedPots(pots, accountId).filter(
    (p) => p.id !== editing?.id,
  );
  const lastRank = String(others.length + 1);
  const position = draft.position === "" ? lastRank : draft.position;
  const ranks = Array.from({ length: others.length + 1 }, (_, i) =>
    String(i + 1),
  );

  const messages: Record<Invalid, string> = {
    name: t`Enter a name for the pot.`,
    account: t`Choose the account the pot is on.`,
    target: t`Enter a target amount greater than 0, e.g. 3000, or leave it empty.`,
    planExpense: t`To plan the expense, enter the target amount and the target date.`,
  };

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const result = toInput({ ...draft, position });
    if ("invalid" in result) {
      setInvalid(result.invalid);
      setError(messages[result.invalid]);
      return;
    }
    setInvalid(null);
    setError(null);
    setSaving(true);
    let saved: Pot;
    try {
      saved = editing
        ? await updatePot(editing.id, result.input)
        : await createPot(result.input);
    } catch (reason) {
      setSaving(false);
      setError(message(reason));
      return;
    }
    // The pot is stored; a failing order call must not look like a failed save.
    let notice: string | null = null;
    if (!result.input.locked) {
      try {
        await applyPosition(saved, Number(position));
      } catch (reason) {
        const why = message(reason);
        notice = t`The pot was saved, but its position could not be set: ${why}`;
      }
    }
    setSaving(false);
    await onSaved(saved, notice);
  }

  const field = (name: Invalid) =>
    invalid === name ? { "aria-invalid": true as const } : {};
  const title = editing ? editing.name : "";

  return (
    <Dialog
      as="form"
      label={editing ? t`Edit pot ${title}` : t`New pot`}
      className="pot-dialog pot-form"
      onSubmit={onSubmit}
      onClose={onClose}
      noValidate
    >
      <h2>
        {editing ? <Trans>Edit pot {title}</Trans> : <Trans>New pot</Trans>}
      </h2>
      <div className="ui-form-grid">
        <Field
          htmlFor="pot-name"
          label={<Trans>Name</Trans>}
          error={invalid === "name" ? error : undefined}
        >
          <input
            id="pot-name"
            autoFocus
            maxLength={100}
            value={draft.name}
            onChange={(e) => update({ name: e.target.value })}
            {...field("name")}
          />
        </Field>
        <Field htmlFor="pot-account" label={<Trans>Account</Trans>}>
          <select
            id="pot-account"
            value={draft.accountId}
            disabled={editing !== null}
            onChange={(e) => {
              const chosen = accounts.find(
                (a) => String(a.id) === e.target.value,
              );
              update({
                accountId: e.target.value,
                area: chosen?.area ?? draft.area,
                position: "",
              });
            }}
          >
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </Field>
        <Field htmlFor="pot-area" label={<Trans>Area</Trans>}>
          <select
            id="pot-area"
            value={draft.area}
            onChange={(e) =>
              update({ area: e.target.value as PotDraft["area"] })
            }
          >
            {AREAS.map((area) => (
              <option key={area} value={area}>
                {i18n._(AREA_LABELS[area])}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="ui-form-grid">
        <Field
          htmlFor="pot-target"
          label={<Trans>Target amount (optional)</Trans>}
          error={invalid === "target" ? error : undefined}
        >
          <input
            id="pot-target"
            className="num"
            inputMode="decimal"
            value={draft.target}
            onChange={(e) => update({ target: e.target.value })}
            {...field("target")}
          />
        </Field>
        <Field
          htmlFor="pot-target-date"
          label={<Trans>Target date (optional)</Trans>}
        >
          <input
            id="pot-target-date"
            type="date"
            value={draft.targetDate}
            onChange={(e) => update({ targetDate: e.target.value })}
          />
        </Field>
        <Field
          htmlFor="pot-position"
          label={<Trans>Emergency rank</Trans>}
          hint={
            <Trans>Rank 1 is used first. A locked pot is never used.</Trans>
          }
        >
          <select
            id="pot-position"
            value={position}
            onChange={(e) => update({ position: e.target.value })}
          >
            {ranks.map((rank) => (
              <option key={rank} value={rank}>
                {t`Rank ${rank}`}
              </option>
            ))}
            <option
              value={LOCKED}
            >{t`Locked (never used in an emergency)`}</option>
          </select>
        </Field>
      </div>
      <label className="pot-check">
        <input
          type="checkbox"
          checked={draft.planExpense}
          onChange={(e) => update({ planExpense: e.target.checked })}
          {...field("planExpense")}
        />
        <Trans>Plan the expense on the target date</Trans>
      </label>
      {invalid === "planExpense" && error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <label className="pot-check">
        <input
          type="checkbox"
          checked={draft.isLevyPot}
          onChange={(e) => update({ isLevyPot: e.target.checked })}
        />
        <Trans>Levy reserve pot</Trans>
      </label>
      <p className="ui-hint">
        <Trans>
          The reserve for taxes and levies is kept in the pot with this marker;
          set it on one pot only.
        </Trans>
      </p>
      {error && invalid === null && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="ui-actions">
        <Button variant="primary" type="submit" disabled={saving}>
          {editing ? <Trans>Save changes</Trans> : <Trans>Save</Trans>}
        </Button>
        <Button onClick={onClose}>
          <Trans>Cancel</Trans>
        </Button>
      </div>
    </Dialog>
  );
}

/** Puts an unlocked, just saved pot at its wished rank (1-based) in the account's emergency order. */
async function applyPosition(saved: Pot, rank: number): Promise<void> {
  const fresh = await listPots();
  const ids = orderedPots(fresh.pots, saved.account_id).map((p) => p.id);
  const next = reorder(ids, saved.id, { index: rank - 1 });
  if (next) await putRanks(saved.account_id, next);
}
